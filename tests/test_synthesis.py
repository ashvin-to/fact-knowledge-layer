"""Tests for multi-hop synthesis across N >= 3 documents."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.db import get_connection, init_db, insert_document, insert_fact
from src.synthesis import discover_fact_clusters, run_multi_hop_synthesis, synthesize_trajectory


@pytest.fixture
def populated_db(tmp_path: Path):
    db_path = tmp_path / "synthesis_test.db"
    init_db(db_path)
    conn = get_connection(db_path)

    # Insert 3 documents
    for i in range(1, 4):
        insert_document(conn, {
            "id": f"doc-{i}",
            "filename": f"economic_report_{2022+i}.pdf",
            "storage_path": f"/tmp/doc_{i}.pdf",
            "upload_time": f"2026-01-0{i}T00:00:00+00:00",
            "status": "completed",
        })

    # Insert India GDP facts across all 3 documents
    insert_fact(conn, {
        "id": "f1",
        "document_id": "doc-1",
        "subject": "India Real GDP",
        "predicate": "growth rate",
        "value": "7.0%",
        "value_type": "numeric",
        "unit": "%",
        "time_scope": "FY23",
        "qualifier": "provisional",
        "confidence": 0.95,
        "pdf_page_index": 0,
        "evidence_text": "Real GDP growth for FY23 stood at 7.0 percent.",
        "extraction_method": "llm",
        "created_at": "2026-01-01T00:00:00+00:00",
    })

    insert_fact(conn, {
        "id": "f2",
        "document_id": "doc-2",
        "subject": "India Real GDP",
        "predicate": "growth rate",
        "value": "7.2%",
        "value_type": "numeric",
        "unit": "%",
        "time_scope": "FY24",
        "qualifier": "revised estimate",
        "confidence": 0.96,
        "pdf_page_index": 2,
        "evidence_text": "Real GDP growth is estimated at 7.2 percent for FY24.",
        "extraction_method": "llm",
        "created_at": "2026-01-02T00:00:00+00:00",
    })

    insert_fact(conn, {
        "id": "f3",
        "document_id": "doc-3",
        "subject": "India Real GDP",
        "predicate": "growth rate",
        "value": "6.8%",
        "value_type": "numeric",
        "unit": "%",
        "time_scope": "FY25",
        "qualifier": "projected",
        "confidence": 0.92,
        "pdf_page_index": 1,
        "evidence_text": "Real GDP growth is projected at 6.8 percent for FY25.",
        "extraction_method": "llm",
        "created_at": "2026-01-03T00:00:00+00:00",
    })

    conn.commit()
    yield conn
    conn.close()


def test_discover_fact_clusters(populated_db):
    clusters = discover_fact_clusters(populated_db, min_docs=3)
    assert len(clusters) >= 1
    cluster = clusters[0]
    assert cluster["document_count"] == 3
    assert len(cluster["hops"]) == 3
    assert [h["time_scope"] for h in cluster["hops"]] == ["FY23", "FY24", "FY25"]


def test_synthesize_trajectory(populated_db):
    clusters = discover_fact_clusters(populated_db, min_docs=3)
    cluster = clusters[0]

    mock_llm = MagicMock()
    mock_llm.chat.return_value = json.dumps({
        "title": "India Real GDP Trajectory (FY23 - FY25)",
        "synthesis_narrative": "India sustained strong GDP expansion across three fiscal years, peaking at 7.2% in FY24 before moderating to 6.8% projection in FY25.",
        "key_findings": [
            "FY23 actualized at 7.0%",
            "FY24 accelerated to 7.2%",
            "FY25 projected at 6.8%",
        ],
        "discrepancies": ["FY24 estimate was revised upward"],
        "confidence": 0.94,
    })

    res, failed = synthesize_trajectory(cluster, mock_llm)
    assert not failed
    assert res.title == "India Real GDP Trajectory (FY23 - FY25)"
    assert res.document_count == 3
    assert len(res.hops) == 3
    assert len(res.key_findings) == 3
    assert res.confidence == 0.94


def test_run_multi_hop_synthesis(populated_db):
    mock_llm = MagicMock()
    mock_llm.chat.return_value = json.dumps({
        "title": "India Real GDP Trajectory",
        "synthesis_narrative": "Consistent macroeconomic growth trajectory across all 3 source documents.",
        "key_findings": ["Established continuous 3-year trend"],
        "discrepancies": [],
        "confidence": 0.95,
    })

    trajectories = run_multi_hop_synthesis(populated_db, min_docs=2, reasoner_client=mock_llm)
    assert len(trajectories) >= 1
    assert trajectories[0].document_count >= 2


def test_synthesize_trajectory_offline_llm_fallback(populated_db):
    clusters = discover_fact_clusters(populated_db, min_docs=3)
    cluster = clusters[0]

    failing_llm = MagicMock()
    failing_llm.chat.side_effect = RuntimeError("All configured LLM providers failed. Connection refused")

    res, failed = synthesize_trajectory(cluster, failing_llm, conn=populated_db)
    assert failed is True
    assert res is not None
    assert "heuristic-synthesizer" in res.reasoner_model
    assert len(res.hops) == 3
    assert len(res.key_findings) > 0
    assert res.confidence == 0.80

