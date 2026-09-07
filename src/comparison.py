"""
Cross-document pairwise fact comparison and reasoning pipeline.

Given facts from multiple processed documents:
  1. Computes / retrieves embeddings for all candidate facts.
  2. Generates candidate pairs across different documents using cosine similarity.
  3. Skips pairs previously evaluated in fact_relationships.
  4. Prompts Reasoner LLM to classify relationship:
       - corroborate
       - contradict
       - context_reconciled (with reconciliation_factor: time | scope | unit | other)
       - unrelated
  5. Second-opinion verification: triggers when verdict is 'contradict' or confidence < 0.7.
  6. Persists verdicts into SQLite.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .db import (
    get_done_documents,
    get_existing_relationship_pairs,
    get_facts_for_documents,
    insert_fact_relationship,
)
from .embeddings import (
    DEFAULT_SIMILARITY_THRESHOLD,
    EmbeddingService,
    generate_candidate_pairs,
)
from .llm_client import LLMClient, get_extractor_client, get_reasoner_client
from .models import FactRelationshipVerdict

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REASONER_SYSTEM_PROMPT = """\
You are an expert fact-reconciliation and corroboration reasoning engine.
Your task is to analyze two facts extracted from different documents and determine how they relate.

Categories:
1. "corroborate": Both facts assert the same substantive finding, metric, or value under the same time scope and context.
2. "contradict": Both facts assert conflicting, incompatible values or claims for the same entity and time period without a reconciling context.
3. "context_reconciled": The facts appear to report different values or descriptions, but the difference is explained by a difference in time scope (e.g., Q3 vs FY24, different years), measurement scope/methodology, or reporting units.
4. "unrelated": The two facts describe different entities, unrelated metrics, or distinct topics.

Rules:
- Output ONLY a single JSON object. No commentary, no markdown fences.
- JSON schema:
  {
    "relationship_type": "corroborate" | "contradict" | "context_reconciled" | "unrelated",
    "explanation": "<concise explanation of the relationship and why this category applies>",
    "confidence": <float between 0.0 and 1.0>,
    "reconciliation_factor": "time" | "scope" | "unit" | "other" | null
  }
- "reconciliation_factor" MUST be one of "time", "scope", "unit", "other" when relationship_type is "context_reconciled", and null otherwise.
"""

_PAIR_TEMPLATE = """\
Compare the following two facts:

--- FACT A ---
Source Document: {doc_a}
Subject: {subject_a}
Predicate: {predicate_a}
Value: {value_a} {unit_a}
Time Scope: {time_scope_a}
Qualifier: {qualifier_a}
Evidence Text: "{evidence_a}"

--- FACT B ---
Source Document: {doc_b}
Subject: {subject_b}
Predicate: {predicate_b}
Value: {value_b} {unit_b}
Time Scope: {time_scope_b}
Qualifier: {qualifier_b}
Evidence Text: "{evidence_b}"

Return ONLY the JSON object.\
"""


def _format_fact_prompt(fact_a: dict[str, Any], fact_b: dict[str, Any]) -> str:
    return _PAIR_TEMPLATE.format(
        doc_a=fact_a.get("document_filename", fact_a.get("document_id")),
        subject_a=fact_a.get("subject", ""),
        predicate_a=fact_a.get("predicate", ""),
        value_a=fact_a.get("value", ""),
        unit_a=fact_a.get("unit") or "",
        time_scope_a=fact_a.get("time_scope") or "Not specified",
        qualifier_a=fact_a.get("qualifier") or "None",
        evidence_a=fact_a.get("evidence_text", ""),
        doc_b=fact_b.get("document_filename", fact_b.get("document_id")),
        subject_b=fact_b.get("subject", ""),
        predicate_b=fact_b.get("predicate", ""),
        value_b=fact_b.get("value", ""),
        unit_b=fact_b.get("unit") or "",
        time_scope_b=fact_b.get("time_scope") or "Not specified",
        qualifier_b=fact_b.get("qualifier") or "None",
        evidence_b=fact_b.get("evidence_text", ""),
    )


def _parse_verdict(raw: str) -> tuple[FactRelationshipVerdict | None, str | None]:
    text = raw.strip()
    if "```" in text:
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()

    data = None
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1], strict=False)
            except json.JSONDecodeError as exc:
                return None, f"JSON parse error: {exc}"
        else:
            return None, f"Could not locate JSON object in response: {text[:200]}"

    if not isinstance(data, dict):
        return None, f"Expected JSON object, got {type(data).__name__}"

    try:
        verdict = FactRelationshipVerdict.model_validate(data)
        return verdict, None
    except ValidationError as exc:
        return None, f"Validation error: {exc}"


def evaluate_pair(
    fact_a: dict[str, Any],
    fact_b: dict[str, Any],
    reasoner_client: LLMClient,
    verifier_client: LLMClient | None = None,
) -> dict[str, Any]:
    """
    Evaluate one candidate pair using the primary reasoner and optional second-opinion verifier.
    """
    user_prompt = _format_fact_prompt(fact_a, fact_b)
    messages = [
        {"role": "system", "content": _REASONER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    raw = reasoner_client.chat(messages, max_tokens=350)
    verdict, error = _parse_verdict(raw)

    if error is not None:
        log.warning("Reasoner verdict invalid on first try: %s — retrying", error)
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": f"Your previous response had this error:\n{error}\nPlease return ONLY the corrected JSON object.",
        })
        raw2 = reasoner_client.chat(messages, max_tokens=350)
        verdict, error2 = _parse_verdict(raw2)

        if error2 is not None:
            log.error("Reasoner verdict failed after retry: %s", error2)
            # Graceful fallback: mark unrelated + needs_review
            verdict = FactRelationshipVerdict(
                relationship_type="unrelated",
                explanation=f"Malformed reasoner output: {error2}",
                confidence=0.0,
                reconciliation_factor=None,
            )

    needs_review = 0
    second_opinion_verdict = None
    second_opinion_model = None

    # Second opinion trigger: contradict OR confidence < 0.7
    if (verdict.relationship_type == "contradict" or verdict.confidence < 0.7) and verifier_client is not None:
        try:
            verifier_raw = verifier_client.chat([
                {"role": "system", "content": _REASONER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            second_verdict, second_err = _parse_verdict(verifier_raw)
            if second_verdict is not None:
                last_m = getattr(verifier_client, "last_model_used", None)
                second_opinion_model = last_m if isinstance(last_m, str) else str(getattr(verifier_client, "model", "verifier"))
                second_opinion_verdict = second_verdict.relationship_type
                if second_verdict.relationship_type != verdict.relationship_type:
                    needs_review = 1
                    log.info(
                        "Disagreement flagged: primary=%s, second_opinion=%s",
                        verdict.relationship_type,
                        second_verdict.relationship_type,
                    )
        except Exception as exc:
            log.warning("Second opinion check failed: %s", exc)

    last_r = getattr(reasoner_client, "last_model_used", None)
    reasoner_model_name = last_r if isinstance(last_r, str) else str(getattr(reasoner_client, "model", "reasoner"))

    return {
        "relationship_type": verdict.relationship_type,
        "explanation": verdict.explanation,
        "reconciliation_factor": verdict.reconciliation_factor,
        "confidence": verdict.confidence,
        "reasoner_model": reasoner_model_name,
        "needs_review": needs_review,
        "second_opinion_verdict": second_opinion_verdict,
        "second_opinion_model": second_opinion_model,
    }


def run_comparison_pipeline(
    conn: sqlite3.Connection,
    document_ids: list[str] | None = None,
    similarity_threshold: float | None = None,
    reasoner_client: LLMClient | None = None,
    verifier_client: LLMClient | None = None,
    embedding_service: EmbeddingService | None = None,
) -> dict[str, int]:
    """
    Run the full cross-document comparison pipeline.

    Returns summary counts:
      candidates_evaluated, corroborate, contradict, context_reconciled, unrelated, needs_review
    """
    if similarity_threshold is None:
        env_thresh = os.environ.get("MATCH_SIMILARITY_THRESHOLD")
        similarity_threshold = float(env_thresh) if env_thresh else DEFAULT_SIMILARITY_THRESHOLD

    if reasoner_client is None:
        reasoner_client = get_reasoner_client()
    if verifier_client is None:
        verifier_client = get_extractor_client()
    if embedding_service is None:
        embedding_service = EmbeddingService()

    # 1. Fetch eligible done documents
    done_docs = get_done_documents(conn, doc_ids=document_ids)
    if len(done_docs) < 2:
        log.info("Fewer than 2 done documents found (%d) — returning empty summary.", len(done_docs))
        return {
            "candidates_evaluated": 0,
            "corroborate": 0,
            "contradict": 0,
            "context_reconciled": 0,
            "unrelated": 0,
            "needs_review": 0,
        }

    target_doc_ids = [d["id"] for d in done_docs]

    # 2. Fetch all facts for these documents
    facts = get_facts_for_documents(conn, target_doc_ids)
    if not facts:
        return {
            "candidates_evaluated": 0,
            "corroborate": 0,
            "contradict": 0,
            "context_reconciled": 0,
            "unrelated": 0,
            "needs_review": 0,
        }

    # 3. Ensure embeddings are computed & attached
    facts = embedding_service.ensure_embeddings(conn, facts)

    # 4. Generate candidate pairs
    existing_pairs = get_existing_relationship_pairs(conn)
    candidates = generate_candidate_pairs(
        facts=facts,
        similarity_threshold=similarity_threshold,
        existing_pairs=existing_pairs,
    )

    log.info(
        "Candidate generation: %d facts across %d docs generated %d new candidate pairs (threshold=%.2f).",
        len(facts),
        len(target_doc_ids),
        len(candidates),
        similarity_threshold,
    )

    counts = {
        "candidates_evaluated": len(candidates),
        "corroborate": 0,
        "contradict": 0,
        "context_reconciled": 0,
        "unrelated": 0,
        "needs_review": 0,
    }

    now = datetime.now(timezone.utc).isoformat()

    # 5. Evaluate candidate pairs
    for fact_a, fact_b, sim_score in candidates:
        eval_result = evaluate_pair(
            fact_a=fact_a,
            fact_b=fact_b,
            reasoner_client=reasoner_client,
            verifier_client=verifier_client,
        )

        rel_type = eval_result["relationship_type"]
        if rel_type in counts:
            counts[rel_type] += 1
        if eval_result["needs_review"]:
            counts["needs_review"] += 1

        rel_record = {
            "id": str(uuid.uuid4()),
            "fact_id_a": fact_a["id"],
            "fact_id_b": fact_b["id"],
            "relationship_type": rel_type,
            "explanation": eval_result["explanation"],
            "reconciliation_factor": eval_result["reconciliation_factor"],
            "confidence": eval_result["confidence"],
            "similarity_score": sim_score,
            "reasoner_model": eval_result["reasoner_model"],
            "needs_review": eval_result["needs_review"],
            "second_opinion_verdict": eval_result["second_opinion_verdict"],
            "second_opinion_model": eval_result["second_opinion_model"],
            "created_at": now,
        }

        insert_fact_relationship(conn, rel_record)
        conn.commit()

    return counts
