"""Tests for page image rendering and evidence bbox endpoints in src/main.py."""

from __future__ import annotations

from pathlib import Path
import pymupdf
import pytest
from fastapi.testclient import TestClient

from src.db import init_db, insert_document, insert_fact
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


def _create_sample_pdf(path: Path) -> None:
    """Create a minimal real 2-page PDF for testing PyMuPDF rendering."""
    doc = pymupdf.open()
    # Page 0
    p0 = doc.new_page(width=595, height=842)
    p0.insert_text((50, 100), "Delhivery Limited Revenue 49114.06 million", fontsize=12)
    # Page 1
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 100), "Operating cash flow 5090.97 million", fontsize=12)
    doc.save(str(path))
    doc.close()


class TestPageEndpoints:
    def test_list_documents(self, client: TestClient, tmp_path: Path) -> None:
        from src.db import get_connection
        conn = get_connection(tmp_path / "test.db")
        insert_document(conn, {
            "id": "doc-list-1",
            "filename": "sample.pdf",
            "storage_path": "/tmp/sample.pdf",
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 2,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        insert_fact(conn, {
            "id": "f-1",
            "document_id": "doc-list-1",
            "subject": "Revenue",
            "predicate": "value",
            "value": "49114.06",
            "value_type": "numeric",
            "unit": "million",
            "time_scope": "FY22",
            "qualifier": None,
            "confidence": 0.95,
            "pdf_page_index": 0,
            "evidence_text": "Revenue 49114.06 million",
            "extraction_method": "test",
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        conn.commit()
        conn.close()

        resp = client.get("/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert "documents" in data
        assert len(data["documents"]) == 1
        assert data["documents"][0]["id"] == "doc-list-1"
        assert data["documents"][0]["fact_count"] == 1

    def test_get_page_image(self, client: TestClient, tmp_path: Path) -> None:
        pdf_file = tmp_path / "storage" / "doc-img-1.pdf"
        _create_sample_pdf(pdf_file)

        from src.db import get_connection
        conn = get_connection(tmp_path / "test.db")
        insert_document(conn, {
            "id": "doc-img-1",
            "filename": "sample.pdf",
            "storage_path": str(pdf_file),
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 2,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        conn.commit()
        conn.close()

        resp = client.get("/documents/doc-img-1/pages/0/image")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(b"\x89PNG")

    def test_get_page_image_out_of_range(self, client: TestClient, tmp_path: Path) -> None:
        pdf_file = tmp_path / "storage" / "doc-img-2.pdf"
        _create_sample_pdf(pdf_file)

        from src.db import get_connection
        conn = get_connection(tmp_path / "test.db")
        insert_document(conn, {
            "id": "doc-img-2",
            "filename": "sample.pdf",
            "storage_path": str(pdf_file),
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 2,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        conn.commit()
        conn.close()

        resp = client.get("/documents/doc-img-2/pages/99/image")
        assert resp.status_code == 400

    def test_get_evidence_bbox_exact_and_fallback(self, client: TestClient, tmp_path: Path) -> None:
        pdf_file = tmp_path / "storage" / "doc-bbox-1.pdf"
        _create_sample_pdf(pdf_file)

        from src.db import get_connection
        conn = get_connection(tmp_path / "test.db")
        insert_document(conn, {
            "id": "doc-bbox-1",
            "filename": "sample.pdf",
            "storage_path": str(pdf_file),
            "upload_time": "2026-01-01T00:00:00+00:00",
            "page_count": 2,
            "pdf_type": "text_based",
            "classification_confidence": 0.99,
            "status": "done",
            "skipped_pages": "[]",
            "failed_pages": "[]",
            "error_message": None,
        })
        conn.commit()
        conn.close()

        # 1. Exact match test with markdown formatting stripped
        resp = client.get(
            "/documents/doc-bbox-1/pages/0/evidence-bbox",
            params={"text": "**Revenue** `49114.06` million"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["match_type"] == "exact"
        assert len(data["bboxes"]) > 0
        assert data["page_width"] == 595.0
        assert data["page_height"] == 842.0

        # 2. Fallback prefix test (first ~40 chars matches "Delhivery Limited Revenue 49114.06 milli")
        resp_fb = client.get(
            "/documents/doc-bbox-1/pages/0/evidence-bbox",
            params={"text": "Delhivery Limited Revenue 49114.06 million followed by trailing markdown hallucination"},
        )
        assert resp_fb.status_code == 200
        data_fb = resp_fb.json()
        assert data_fb["match_type"] == "fallback_prefix"
        assert len(data_fb["bboxes"]) > 0

        # 3. No match test
        resp_none = client.get(
            "/documents/doc-bbox-1/pages/0/evidence-bbox",
            params={"text": "completely missing string that is not on this page"},
        )
        assert resp_none.status_code == 200
        data_none = resp_none.json()
        assert data_none["match_type"] == "none"
        assert data_none["bboxes"] == []
