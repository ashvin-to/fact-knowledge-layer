"""Tests for cross-document comparison API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.db import get_connection, init_db, insert_document, insert_fact
from src.main import app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "test.db"
    storage = tmp_path / "storage"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("STORAGE_DIR", str(storage))
    import src.main as m
    monkeypatch.setattr(m, "_DB_PATH", db)
    monkeypatch.setattr(m, "_STORAGE_DIR", storage)
    init_db(db)
    storage.mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _insert_test_data(db_path: Path):
    conn = get_connection(db_path)
    # Doc 1
    insert_document(conn, {
        "id": "doc-1",
        "filename": "report_2023.pdf",
        "storage_path": "/tmp/1.pdf",
        "upload_time": "2026-01-01T00:00:00+00:00",
        "status": "done",
    })
    # Doc 2
    insert_document(conn, {
        "id": "doc-2",
        "filename": "report_2024.pdf",
        "storage_path": "/tmp/2.pdf",
        "upload_time": "2026-01-01T00:00:00+00:00",
        "status": "done",
    })

    # Normalized dummy vector
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()

    # Fact in doc 1
    insert_fact(conn, {
        "id": "fact-1",
        "document_id": "doc-1",
        "subject": "GDP Growth",
        "predicate": "rate",
        "value": "7.2",
        "value_type": "numeric",
        "unit": "%",
        "time_scope": "FY24",
        "qualifier": None,
        "confidence": 0.95,
        "pdf_page_index": 0,
        "evidence_text": "GDP growth was 7.2% in FY24",
        "extraction_method": "local:test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "embedding": vec,
        "embedding_model": "all-MiniLM-L6-v2",
    })

    # Fact in doc 2
    insert_fact(conn, {
        "id": "fact-2",
        "document_id": "doc-2",
        "subject": "GDP Growth",
        "predicate": "rate",
        "value": "7.0",
        "value_type": "numeric",
        "unit": "%",
        "time_scope": "FY24",
        "qualifier": "advance estimate",
        "confidence": 0.90,
        "pdf_page_index": 0,
        "evidence_text": "GDP growth estimated at 7.0% for FY24",
        "extraction_method": "local:test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "embedding": vec,
        "embedding_model": "all-MiniLM-L6-v2",
    })

    conn.commit()
    conn.close()


class TestCompareApi:
    def test_compare_fewer_than_two_docs_returns_zero_counts(self, client) -> None:
        resp = client.post("/compare")
        assert resp.status_code == 200
        data = resp.json()
        assert data["candidates_evaluated"] == 0
        assert data["corroborate"] == 0
        assert data["contradict"] == 0
        assert data["context_reconciled"] == 0

    def test_compare_happy_path(self, client, tmp_path) -> None:
        _insert_test_data(tmp_path / "test.db")

        mock_verdict = json.dumps({
            "relationship_type": "context_reconciled",
            "explanation": "Differs between actual and advance estimate",
            "confidence": 0.9,
            "reconciliation_factor": "scope",
        })

        with patch("src.comparison.LLMClient.chat", return_value=mock_verdict):
            resp = client.post("/compare")

        assert resp.status_code == 200
        data = resp.json()
        assert data["candidates_evaluated"] == 1
        assert data["context_reconciled"] == 1
        assert data["needs_review"] == 0

    def test_compare_deduplication_on_second_run(self, client, tmp_path) -> None:
        _insert_test_data(tmp_path / "test.db")

        mock_verdict = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Same metric",
            "confidence": 0.95,
            "reconciliation_factor": None,
        })

        with patch("src.comparison.LLMClient.chat", return_value=mock_verdict):
            # First run: evaluates 1 candidate
            resp1 = client.post("/compare")
            assert resp1.json()["candidates_evaluated"] == 1

            # Second run with no new docs: evaluates 0 candidates (dedup works!)
            resp2 = client.post("/compare")
            assert resp2.json()["candidates_evaluated"] == 0

    def test_get_relationships_inlined_details(self, client, tmp_path) -> None:
        _insert_test_data(tmp_path / "test.db")

        mock_verdict = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Both match",
            "confidence": 0.95,
            "reconciliation_factor": None,
        })

        with patch("src.comparison.LLMClient.chat", return_value=mock_verdict):
            client.post("/compare")

        # GET /relationships
        resp = client.get("/relationships")
        assert resp.status_code == 200
        rels = resp.json()["relationships"]
        assert len(rels) == 1
        r = rels[0]
        assert r["relationship_type"] == "corroborate"
        # Check inlined fact details & filenames
        assert r["fact_a"]["document_filename"] == "report_2023.pdf"
        assert r["fact_b"]["document_filename"] == "report_2024.pdf"
        assert r["fact_a"]["subject"] == "GDP Growth"
        assert r["fact_b"]["subject"] == "GDP Growth"

    def test_get_relationships_filtered_by_type(self, client, tmp_path) -> None:
        _insert_test_data(tmp_path / "test.db")

        mock_verdict = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Both match",
            "confidence": 0.95,
            "reconciliation_factor": None,
        })

        with patch("src.comparison.LLMClient.chat", return_value=mock_verdict):
            client.post("/compare")

        # Filter by matching type
        resp_corr = client.get("/relationships?type=corroborate")
        assert len(resp_corr.json()["relationships"]) == 1

        # Filter by non-matching type
        resp_contra = client.get("/relationships?type=contradict")
        assert len(resp_contra.json()["relationships"]) == 0

    def test_get_fact_relationships(self, client, tmp_path) -> None:
        _insert_test_data(tmp_path / "test.db")

        mock_verdict = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Both match",
            "confidence": 0.95,
            "reconciliation_factor": None,
        })

        with patch("src.comparison.LLMClient.chat", return_value=mock_verdict):
            client.post("/compare")

        resp = client.get("/facts/fact-1/relationships")
        assert resp.status_code == 200
        rels = resp.json()["relationships"]
        assert len(rels) == 1
        assert rels[0]["fact_a"]["id"] == "fact-1"

    def test_adjudicate_relationship(self, client, tmp_path) -> None:
        _insert_test_data(tmp_path / "test.db")

        mock_primary = json.dumps({
            "relationship_type": "contradict",
            "explanation": "Different growth numbers",
            "confidence": 0.65,
            "reconciliation_factor": None,
        })
        mock_second = json.dumps({
            "relationship_type": "context_reconciled",
            "explanation": "Reconciled by period",
            "confidence": 0.85,
            "reconciliation_factor": "time",
        })

        m_reasoner = MagicMock()
        m_reasoner.chat.return_value = mock_primary
        m_verifier = MagicMock()
        m_verifier.chat.return_value = mock_second

        with patch("src.main.get_reasoner_client", return_value=m_reasoner), \
             patch("src.main.get_extractor_client", return_value=m_verifier):
            client.post("/compare")

        rels = client.get("/relationships").json()["relationships"]
        assert len(rels) == 1
        rel_id = rels[0]["id"]
        assert rels[0]["relationship_type"] == "contradict"
        assert rels[0]["needs_review"] == 1

        # Human auditor overrides to context_reconciled
        adj_resp = client.post(
            f"/relationships/{rel_id}/adjudicate",
            json={
                "relationship_type": "context_reconciled",
                "status": "overruled",
                "notes": "Reconciled due to advance estimate revision",
                "reconciliation_factor": "advance_estimate",
            },
        )
        assert adj_resp.status_code == 200
        assert adj_resp.json()["status"] == "ok"

        # Verify updated relationship in DB
        updated_rels = client.get("/relationships").json()["relationships"]
        assert len(updated_rels) == 1
        assert updated_rels[0]["relationship_type"] == "context_reconciled"
        assert updated_rels[0]["needs_review"] == 0
        assert updated_rels[0]["user_adjudication_status"] == "overruled"
        assert updated_rels[0]["user_notes"] == "Reconciled due to advance estimate revision"
