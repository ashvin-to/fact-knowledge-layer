"""
Fact extraction from a single page of markdown.

Public API
----------
  extract_facts_from_page(page_markdown, page_index, document_id, llm_client)
      -> list[dict]   # ready-to-insert fact dicts (all DB columns populated)

LLM contract
------------
  The LLM is asked to return ONLY a JSON array. Each element must match
  FactExtraction (see models.py). If the first response is invalid, one
  retry is attempted with the error appended. On second failure the page
  is signalled to the caller via FactExtractionPageError.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from .llm_client import LLMClient
from .models import FactExtraction

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert fact-extraction engine. Your job is to extract all discrete, atomic facts, statistics, financial figures, counts, team sizes, technology metrics, table rows, and entity attributes from the provided document page.

Rules:
1. Output ONLY a valid JSON array — no prose, no markdown fences, no commentary.
2. Each element of the array must be a JSON object with exactly these fields:
   {
     "subject":      "<entity or concept the fact is about>",
     "predicate":    "<relationship, metric, or attribute name>",
     "value":        "<the exact fact value or metric>",
     "value_type":   "numeric" | "categorical" | "boolean" | "text",
     "unit":         "<unit string like %, ₹ million, shares, professionals, or null>",
     "time_scope":   "<time period / date if specified, or null>",
     "qualifier":    "<qualifying condition if any, or null>",
     "confidence":   <float 0.0 to 1.0>,
     "evidence_text":"<verbatim substring copied EXACTLY from the page content>"
   }
3. evidence_text MUST be an exact verbatim substring from the page text provided.
4. TABLE & NARRATIVE EXTRACTION: Extract facts from both tables and narrative paragraphs. If the text mentions numbers (e.g. 'team of 505 professionals', 'over 80 applications', '3,730 delivery centres'), you MUST extract each into a fact object!
5. If and only if a page has ZERO factual statements or data (e.g. pure table of contents without numbers, or blank page), return []. If the page contains any facts, numbers, or claims, you MUST extract them.
6. Do not invent facts not present in the text.\
"""

_USER_TEMPLATE = """\
Extract all facts from the following page content.

--- PAGE CONTENT START ---
{page_markdown}
--- PAGE CONTENT END ---

Remember: output ONLY the JSON array.\
"""


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class FactExtractionPageError(Exception):
    """Raised when a page cannot be parsed after one retry."""


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def extract_facts_from_page(
    page_markdown: str,
    page_index: int,
    document_id: str,
    llm_client: LLMClient,
) -> list[dict]:
    """
    Extract facts from one page of markdown.

    Returns a list of dicts ready to INSERT into the facts table.
    Raises FactExtractionPageError if LLM output is unparseable after retry.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(page_markdown=page_markdown)},
    ]

    raw = llm_client.chat(messages, max_tokens=3000)
    extractions, error = _parse_response(raw)

    if error is not None:
        log.warning(
            "Page %d: parse/validation error on first attempt: %s — retrying",
            page_index,
            error,
        )
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                f"Your previous response had this error:\n{error}\n\n"
                "Please fix it and return ONLY the corrected JSON array."
            ),
        })
        raw2 = llm_client.chat(messages, max_tokens=3000)
        extractions, error2 = _parse_response(raw2)
        if error2 is not None:
            raise FactExtractionPageError(
                f"Page {page_index}: still invalid after retry: {error2}"
            )

    now = datetime.now(timezone.utc).isoformat()
    last = getattr(llm_client, "last_model_used", None)
    used_model = last if isinstance(last, str) else str(getattr(llm_client, "model", "default"))
    extraction_method = f"local:{used_model}"

    return [
        {
            "id": str(uuid.uuid4()),
            "document_id": document_id,
            "subject": fe.subject,
            "predicate": fe.predicate,
            "value": fe.value,
            "value_type": fe.value_type,
            "unit": fe.unit,
            "time_scope": fe.time_scope,
            "qualifier": fe.qualifier,
            "confidence": fe.confidence,
            "pdf_page_index": page_index,
            "evidence_text": fe.evidence_text,
            "extraction_method": extraction_method,
            "created_at": now,
        }
        for fe in extractions
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> tuple[list[FactExtraction], str | None]:
    """
    Try to parse the LLM response as a JSON array of FactExtraction objects.

    Returns (extractions, None) on success or ([], error_message) on failure.
    """
    text = raw.strip()

    # Strip markdown fences if present
    if "```" in text:
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    data = None
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # Try extracting the substring from first '[' to last ']'
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1], strict=False)
            except json.JSONDecodeError:
                data = None

        # If still None (e.g. truncated due to token limit), salvage complete objects up to last '}'
        if data is None and start != -1:
            last_brace = text.rfind("}")
            if last_brace != -1 and last_brace > start:
                salvaged = text[start : last_brace + 1] + "]"
                try:
                    data = json.loads(salvaged, strict=False)
                except json.JSONDecodeError as exc:
                    return [], f"JSON parse error: {exc}"

        if data is None:
            return [], f"JSON parse error: could not locate JSON array in response: {text[:200]}"

    if not isinstance(data, list):
        return [], f"Expected a JSON array, got {type(data).__name__}"

    extractions: list[FactExtraction] = []
    seen_keys: set[tuple[str, str, str, str | None]] = set()

    for i, item in enumerate(data):
        try:
            fact_obj = FactExtraction.model_validate(item)
            dedup_key = (
                fact_obj.subject.strip().lower(),
                fact_obj.predicate.strip().lower(),
                fact_obj.value.strip().lower(),
                fact_obj.time_scope,
            )
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                extractions.append(fact_obj)
        except ValidationError as exc:
            return [], f"Validation error on item {i}: {exc}"

    return extractions, None
