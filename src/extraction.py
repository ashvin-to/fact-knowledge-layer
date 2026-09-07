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
You are an expert fact-extraction engine. Your job is to extract all discrete, atomic facts, statistics, financial figures, percentages, counts, team sizes, technology metrics, table rows, and entity attributes from the provided document page.

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
4. TABLE & NARRATIVE EXTRACTION: Extract facts from BOTH tables and narrative paragraphs. If the text mentions numbers, metrics, percentages, capacities, or milestones (e.g. 'team of 505 professionals', 'revenues grew by 31%', 'share crossed 70%'), you MUST extract each into a fact object!
5. If and only if a page has ZERO factual statements or data (e.g. pure table of contents without numbers, or blank page), return []. If the page contains any facts, numbers, or claims, you MUST extract them.
6. Do not invent facts not present in the text.

Example Input 1 (Corporate / Financial):
"Our part truckload tonnage grew by 30%, and revenues from part truckload grew by 31% in FY24. The share of load carried through fuel-efficient 46-ft tractor trailers crossed 70% by the end of FY24."

Example Output 1:
[
  {
    "subject": "Delhivery Part Truckload Tonnage",
    "predicate": "growth_rate",
    "value": "30%",
    "value_type": "numeric",
    "unit": "%",
    "time_scope": "FY24",
    "qualifier": null,
    "confidence": 0.95,
    "evidence_text": "Our part truckload tonnage grew by 30%"
  },
  {
    "subject": "Delhivery Part Truckload Revenue",
    "predicate": "growth_rate",
    "value": "31%",
    "value_type": "numeric",
    "unit": "%",
    "time_scope": "FY24",
    "qualifier": null,
    "confidence": 0.95,
    "evidence_text": "revenues from part truckload grew by 31% in FY24"
  }
]

Example Input 2 (Macroeconomic / Policy):
"Growth in the services sector is expected to remain robust at 7.2 per cent in FY25, driven by financial and professional services, while trade-restrictive measures now affect 12.7 per cent of G20 imports."

Example Output 2:
[
  {
    "subject": "Services sector",
    "predicate": "expected_growth_rate",
    "value": "7.2%",
    "value_type": "numeric",
    "unit": "%",
    "time_scope": "FY25",
    "qualifier": null,
    "confidence": 0.95,
    "evidence_text": "Growth in the services sector is expected to remain robust at 7.2 per cent"
  },
  {
    "subject": "G20 imports",
    "predicate": "trade_restrictive_measures_coverage",
    "value": "12.7%",
    "value_type": "numeric",
    "unit": "%",
    "time_scope": null,
    "qualifier": "trade-restrictive measures",
    "confidence": 0.95,
    "evidence_text": "now affecting 12.7 per cent of G20 imports"
  }
]\
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


def extract_facts_from_page_image(
    page_image_bytes: bytes,
    page_index: int,
    document_id: str,
    llm_client: LLMClient,
) -> list[dict]:
    """
    Extract facts directly from a rendered PDF page image (e.g. scanned pages, infographics, complex chart plots)
    using a Vision-capable LLM prompt with base64 image encoding.
    """
    import base64

    b64_img = base64.b64encode(page_image_bytes).decode("utf-8")
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Extract all discrete atomic facts, statistics, table rows, and chart data points from Page {page_index + 1}. "
                        "Return ONLY the JSON array."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64_img}",
                    },
                },
            ],
        },
    ]

    raw = llm_client.chat(messages, max_tokens=3000)
    extractions, error = _parse_response(raw)
    if error is not None:
        log.warning("Vision page %d: parse error: %s", page_index, error)
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": f"Previous response had error:\n{error}\nPlease return ONLY the corrected JSON array.",
        })
        raw2 = llm_client.chat(messages, max_tokens=3000)
        extractions, error2 = _parse_response(raw2)
        if error2 is not None:
            raise FactExtractionPageError(f"Vision Page {page_index}: invalid after retry: {error2}")

    now = datetime.now(timezone.utc).isoformat()
    last = getattr(llm_client, "last_model_used", None)
    used_model = last if isinstance(last, str) else str(getattr(llm_client, "model", "default"))
    extraction_method = f"vision:{used_model}"

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
        if not isinstance(item, dict):
            continue
        clean_item = dict(item)
        if "value" in clean_item and clean_item["value"] is None:
            clean_item["value"] = "-"
        elif "value" in clean_item:
            clean_item["value"] = str(clean_item["value"]).strip()

        try:
            fact_obj = FactExtraction.model_validate(clean_item)
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
