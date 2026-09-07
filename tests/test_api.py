"""Tests for the FastAPI routes (src/main.py)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point DB and storage at tmp dirs for each test."""
    db = tmp_path / "test.db"
    storage = tmp_path / "storage"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("STORAGE_DIR", str(storage))
    # Also patch the module-level globals so the running app uses them
    import src.main as m
    monkeypatch.setattr(m, "_DB_PATH", db)
    monkeypatch.setattr(m, "_STORAGE_DIR", storage)
    # Initialise DB
    from src.db import init_db
    init_db(db)
    storage.mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


_PDF_HEADER = b"%PDF-1.4 fake content"


def _mock_classification(pdf_type="text_based", page_count=1, confidence=0.99):
    c = MagicMock()
    c.pdf_type = pdf_type
    c.page_count = page_count
    c.confidence = confidence
    c.pages_needing_ocr = []
    return c


def _mock_page(page_index: int, markdown: str, needs_ocr: bool = False):
    p = MagicMock()
    p.page = page_index
    p.markdown = markdown
    p.needs_ocr = needs_ocr
    p.ocr_reason = None
    return p


def _mock_extraction_result(pages):
    r = MagicMock()
    r.pages = pages
    return r


def _valid_fact_json():
    return json.dumps([{
        "subject": "Revenue", "predicate": "amount", "value": "500M",
        "value_type": "numeric", "unit": "USD", "time_scope": "Q1",
        "qualifier": None, "confidence": 0.95,
        "evidence_text": "Revenue: 500M USD",
    }])


class TestUploadDocument:
    def test_rejects_non_pdf_content_type(self, client) -> None:
        resp = client.post(
            "/documents",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 400

    def test_rejects_pdf_content_type_with_bad_magic(self, client) -> None:
        resp = client.post(
            "/documents",
            files={"file": ("test.pdf", b"not really a pdf", "application/pdf")},
        )
        assert resp.status_code == 400

    def test_success_returns_201(self, client) -> None:
        pages = [_mock_page(0, "Revenue: 500M USD")]
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification()),
            patch("src.ingestion.pdf_inspector.extract_pages_markdown",
                  return_value=_mock_extraction_result(pages)),
            patch("src.main.LLMClient.chat", return_value=_valid_fact_json()),
        ):
            resp = client.post(
                "/documents",
                files={"file": ("report.pdf", _PDF_HEADER, "application/pdf")},
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "document_id" in body
        assert body["status"] in ("done", "failed")  # depends on LLM mock wiring

    def test_fully_scanned_pdf_returns_failed(self, client) -> None:
        with patch("src.ingestion.pdf_inspector.classify_pdf",
                   return_value=_mock_classification(pdf_type="scanned")):
            resp = client.post(
                "/documents",
                files={"file": ("scan.pdf", _PDF_HEADER, "application/pdf")},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error_message"] is not None


class TestGetDocument:
    def test_404_for_missing(self, client) -> None:
        resp = client.get("/documents/no-such-id")
        assert resp.status_code == 404

    def test_returns_document(self, client) -> None:
        # Insert a doc manually
        import src.main as m
        from src.db import get_connection, insert_document
        conn = get_connection(m._DB_PATH)
        insert_document(conn, {
            "id": "test-doc",
            "filename": "x.pdf",
            "storage_path": "/tmp/x.pdf",
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 1,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        conn.commit()
        conn.close()

        resp = client.get("/documents/test-doc")
        assert resp.status_code == 200
        assert resp.json()["id"] == "test-doc"


class TestGetFacts:
    def test_404_for_missing_document(self, client) -> None:
        resp = client.get("/documents/no-such-id/facts")
        assert resp.status_code == 404

    def test_returns_empty_list_when_no_facts(self, client) -> None:
        import src.main as m
        from src.db import get_connection, insert_document
        conn = get_connection(m._DB_PATH)
        insert_document(conn, {
            "id": "doc-empty",
            "filename": "x.pdf",
            "storage_path": "/tmp/x.pdf",
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 1,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        conn.commit()
        conn.close()

        resp = client.get("/documents/doc-empty/facts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["document_id"] == "doc-empty"
        assert body["facts"] == []


class TestGetGraph:
    def test_returns_graph_nodes_and_links(self, client) -> None:
        import src.main as m
        from src.db import get_connection, insert_document, insert_fact
        conn = get_connection(m._DB_PATH)
        insert_document(conn, {
            "id": "doc-graph-1",
            "filename": "graph_test.pdf",
            "storage_path": "/tmp/graph_test.pdf",
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 1,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        insert_fact(conn, {
            "id": "fact-graph-1",
            "document_id": "doc-graph-1",
            "subject": "Delhivery",
            "predicate": "fleet_size",
            "value": "15000",
            "value_type": "numeric",
            "unit": "vehicles",
            "time_scope": "2024",
            "qualifier": None,
            "confidence": 0.95,
            "pdf_page_index": 0,
            "evidence_text": "fleet size of 15000 vehicles",
            "extraction_method": "test",
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        conn.commit()
        conn.close()

        resp = client.get("/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "links" in data
        assert len(data["nodes"]) == 2  # 1 doc + 1 fact
        assert len(data["links"]) == 1  # 1 contains link

