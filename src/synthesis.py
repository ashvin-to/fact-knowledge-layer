"""
Multi-hop fact synthesis and reasoning chains across N >= 3 documents.

Discovers metric/entity trajectories that span multiple documents and generates
coherent chronological reasoning narratives using the Reasoner LLM.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from typing import Any

from pydantic import ValidationError

from .llm_client import LLMClient, get_reasoner_client
from .models import TrajectoryHop, TrajectoryItem

log = logging.getLogger(__name__)

_SYNTHESIS_SYSTEM_PROMPT = """\
You are an executive intelligence synthesis and cross-document reasoning engine.
Your task is to analyze a sequence of facts regarding a specific entity or metric extracted from multiple distinct source documents.

You will receive:
1. Metric / Subject topic
2. A sequence of fact hops across different source documents (with page numbers, values, units, time scopes, and verbatim evidence).

Your goals:
1. Generate an executive "synthesis_narrative" (2-4 sentences) that explains how this metric/finding evolved over time or across different reporting authorities, highlighting consensus, trends, or reconciliations.
2. List "key_findings": 1-3 bullet summaries of established facts.
3. List "discrepancies": any revisions, temporal shifts, or unresolved contradictions observed across the documents.
4. Assign an overall "confidence" (0.0 to 1.0).

Rules:
- Output ONLY valid JSON with keys: "title", "synthesis_narrative", "key_findings", "discrepancies", "confidence".
- Ground your analysis strictly in the provided evidence.
"""


def _normalize_cluster_key(subject: str, predicate: str) -> str:
    """Normalize subject + predicate into a canonical cluster key."""
    s = re.sub(r"[^a-zA-Z0-9]+", " ", subject).lower().strip()
    p = re.sub(r"[^a-zA-Z0-9]+", " ", predicate).lower().strip()
    return f"{s}::{p}"


def discover_fact_clusters(
    conn: sqlite3.Connection,
    min_docs: int = 2,
) -> list[dict[str, Any]]:
    """
    Cluster facts across documents by entity/metric.
    Returns clusters that span at least min_docs distinct documents.
    """
    query = """
        SELECT
            f.id AS fact_id,
            f.document_id,
            d.filename AS document_filename,
            d.upload_time,
            f.pdf_page_index,
            f.subject,
            f.predicate,
            f.value,
            f.unit,
            f.time_scope,
            f.qualifier,
            f.evidence_text,
            f.confidence
        FROM facts f
        JOIN documents d ON f.document_id = d.id
        WHERE d.status = 'completed' OR d.status = 'done'
        ORDER BY f.document_id, f.pdf_page_index
    """
    rows = conn.execute(query).fetchall()

    clusters: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = _normalize_cluster_key(r["subject"], r["predicate"])
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(dict(r))

    subject_clusters: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        s_key = re.sub(r"[^a-zA-Z0-9]+", " ", r["subject"]).lower().strip()
        if s_key not in subject_clusters:
            subject_clusters[s_key] = []
        subject_clusters[s_key].append(dict(r))

    selected_clusters: list[dict[str, Any]] = []
    seen_fact_sets: list[set[str]] = []

    candidate_pools = list(clusters.values()) + list(subject_clusters.values())

    for fact_list in candidate_pools:
        doc_ids = {f["document_id"] for f in fact_list}
        if len(doc_ids) < min_docs:
            continue

        fact_ids = {f["fact_id"] for f in fact_list}
        if any(fact_ids.issubset(seen) for seen in seen_fact_sets):
            continue

        seen_fact_sets.append(fact_ids)

        def sort_key(f):
            ts = f.get("time_scope") or ""
            return (ts, f.get("upload_time") or "", f.get("document_filename") or "", f.get("pdf_page_index", 0))

        sorted_hops = sorted(fact_list, key=sort_key)

        primary_subject = sorted_hops[0]["subject"]
        primary_predicate = sorted_hops[0]["predicate"]

        selected_clusters.append({
            "id": str(uuid.uuid4()),
            "metric": f"{primary_subject} — {primary_predicate}",
            "subject": primary_subject,
            "predicate": primary_predicate,
            "document_count": len(doc_ids),
            "hops": sorted_hops,
        })

    return selected_clusters


def _format_trajectory_prompt(cluster: dict[str, Any]) -> str:
    hops = cluster["hops"]
    lines = [
        f"METRIC / TOPIC: {cluster['metric']}",
        f"NUMBER OF DISTINCT DOCUMENTS: {cluster['document_count']}",
        "CHRONOLOGICAL FACT SEQUENCE ACROSS DOCUMENTS:",
    ]
    for idx, hop in enumerate(hops, 1):
        lines.append(
            f"{idx}. Document: {hop['document_filename']} (Page {hop['pdf_page_index'] + 1})\n"
            f"   Claim: [{hop['subject']}] {hop['predicate']} -> {hop['value']} {hop.get('unit') or ''}\n"
            f"   Time Scope: {hop.get('time_scope') or 'Unspecified'} | Qualifier: {hop.get('qualifier') or 'None'}\n"
            f"   Verbatim Evidence: \"{hop['evidence_text']}\""
        )
    return "\n".join(lines)


import hashlib
from datetime import datetime, timezone


def synthesize_trajectory(
    cluster: dict[str, Any],
    reasoner_client: LLMClient | None = None,
    conn: sqlite3.Connection | None = None,
    force_refresh: bool = False,
    skip_llm: bool = False,
) -> tuple[TrajectoryItem, bool]:
    """Run LLM synthesis over a multi-document fact trajectory with database caching.
    Returns (TrajectoryItem, llm_failed_flag)."""
    fact_ids = sorted(h["fact_id"] for h in cluster["hops"])
    cluster_hash = hashlib.sha256(",".join(fact_ids).encode()).hexdigest()

    # 1. Return from DB cache if facts in cluster have not changed
    if conn is not None and not force_refresh:
        row = conn.execute(
            "SELECT * FROM synthesized_trajectories WHERE cluster_hash = ?", (cluster_hash,)
        ).fetchone()
        if row:
            hops_data = json.loads(row["hops_json"])
            item = TrajectoryItem(
                id=row["id"],
                title=row["title"],
                metric=row["metric"],
                document_count=row["document_count"],
                hops=[TrajectoryHop.model_validate(h) for h in hops_data],
                synthesis_narrative=row["synthesis_narrative"],
                key_findings=json.loads(row["key_findings"]),
                discrepancies=json.loads(row["discrepancies"]),
                confidence=row["confidence"],
                reasoner_model=row["reasoner_model"],
            )
            return item, False

    # 2. Invoke Reasoner LLM (or fallback gracefully to heuristic synthesis if LLM is offline)
    data = {}
    used_model_name = None
    llm_failed = False

    if not skip_llm and reasoner_client is not None:
        prompt = _format_trajectory_prompt(cluster)
        messages = [
            {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = reasoner_client.chat(messages, max_tokens=600)
            text = raw.strip()
            if "```" in text:
                lines = text.splitlines()
                text = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()

            try:
                data = json.loads(text, strict=False)
            except json.JSONDecodeError:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        data = json.loads(text[start : end + 1], strict=False)
                    except Exception:
                        pass
            last_r = getattr(reasoner_client, "last_model_used", None)
            used_model_name = last_r if isinstance(last_r, str) else str(getattr(reasoner_client, "model", "reasoner"))
        except Exception as exc:
            log.warning("LLM synthesis unavailable (%s), switching to heuristic trajectory narrative.", exc)
            llm_failed = True

    if not data:
        used_model_name = "heuristic-synthesizer (offline)"
        hops = cluster["hops"]
        doc_names = list(dict.fromkeys(h["document_filename"] for h in hops))
        scopes = [h.get("time_scope") for h in hops if h.get("time_scope")]
        scope_str = f" across {', '.join(scopes)}" if scopes else ""
        data = {
            "title": f"{cluster['metric']} Multi-Document Evolution",
            "synthesis_narrative": (
                f"Tracked {len(hops)} chronological observations for {cluster['metric']} across "
                f"{len(doc_names)} documents ({', '.join(doc_names[:3])}){scope_str}."
            ),
            "key_findings": [
                f"{h['document_filename']} (p.{h['pdf_page_index'] + 1}): {h['predicate']} = {h['value']} {h.get('unit') or ''}"
                for h in hops[:4]
            ],
            "discrepancies": [],
            "confidence": 0.80,
        }

    title = data.get("title") or cluster["metric"]
    narrative = data.get("synthesis_narrative") or (
        f"Observed {len(cluster['hops'])} reported values for {cluster['metric']} across {cluster['document_count']} documents."
    )
    key_findings = data.get("key_findings") or [
        f"{h['document_filename']}: {h['value']} {h.get('unit') or ''}" for h in cluster["hops"][:3]
    ]
    discrepancies = data.get("discrepancies") or []
    confidence = float(data.get("confidence", 0.85))

    hops_models = [
        TrajectoryHop(
            fact_id=h["fact_id"],
            document_id=h["document_id"],
            document_filename=h["document_filename"],
            pdf_page_index=h["pdf_page_index"],
            subject=h["subject"],
            predicate=h["predicate"],
            value=h["value"],
            unit=h.get("unit"),
            time_scope=h.get("time_scope"),
            qualifier=h.get("qualifier"),
            evidence_text=h["evidence_text"],
            confidence=h["confidence"],
        )
        for h in cluster["hops"]
    ]

    reasoner_model = used_model_name or "reasoner"

    item = TrajectoryItem(
        id=cluster["id"],
        title=title,
        metric=cluster["metric"],
        document_count=cluster["document_count"],
        hops=hops_models,
        synthesis_narrative=narrative,
        key_findings=key_findings,
        discrepancies=discrepancies,
        confidence=confidence,
        reasoner_model=reasoner_model,
    )

    # 3. Persist to DB cache
    if conn is not None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO synthesized_trajectories
            (id, cluster_hash, metric, title, document_count, synthesis_narrative,
             key_findings, discrepancies, confidence, reasoner_model, hops_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                cluster_hash,
                item.metric,
                item.title,
                item.document_count,
                item.synthesis_narrative,
                json.dumps(item.key_findings),
                json.dumps(item.discrepancies),
                item.confidence,
                item.reasoner_model,
                json.dumps([h.model_dump() for h in item.hops]),
                now,
            ),
        )
        conn.commit()

    return item, llm_failed


def run_multi_hop_synthesis(
    conn: sqlite3.Connection,
    min_docs: int = 2,
    reasoner_client: LLMClient | None = None,
    force_refresh: bool = False,
) -> list[TrajectoryItem]:
    """Discover clusters and synthesize all multi-document trajectories."""
    if reasoner_client is None:
        reasoner_client = get_reasoner_client()

    clusters = discover_fact_clusters(conn, min_docs=min_docs)
    log.info("Discovered %d multi-document trajectories (min_docs=%d)", len(clusters), min_docs)

    trajectories: list[TrajectoryItem] = []
    skip_llm = False
    for cluster in clusters:
        try:
            item, failed = synthesize_trajectory(
                cluster,
                reasoner_client,
                conn=conn,
                force_refresh=force_refresh,
                skip_llm=skip_llm,
            )
            if failed:
                skip_llm = True
            trajectories.append(item)
        except Exception as exc:
            log.warning("Synthesis failed for cluster %s: %s", cluster["metric"], exc)

    return trajectories


