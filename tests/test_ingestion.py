"""Tests for the ingestion pipeline (src/ingestion.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.db import get_connection, get_document, get_facts, init_db
from src.ingestion import process_pdf


@pytest.fixture()
def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)

    # Insert a stub document row
    conn.execute(
        """
        INSERT INTO documents
            (id, filename, storage_path, upload_time, status,
             skipped_pages, failed_pages)
        VALUES
            ('doc-1', 'test.pdf', '/tmp/test.pdf',
             '2026-01-01T00:00:00+00:00', 'pending', '[]', '[]')
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _mock_classification(pdf_type="text_based", page_count=2, confidence=0.99):
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


def _mock_llm(facts_per_page=None):
    """LLM client that returns one fact per page."""
    client = MagicMock()
    client.model = "test-model"
    if facts_per_page is None:
        facts_per_page = [
            json.dumps([{
                "subject": "S", "predicate": "P", "value": "V",
                "value_type": "text", "unit": None, "time_scope": None,
                "qualifier": None, "confidence": 0.9, "evidence_text": "V",
            }])
        ]
    client.chat.side_effect = facts_per_page
    return client


class TestProcessPdfFullyScanned:
    def test_image_based_sets_failed(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification(pdf_type="image_based")),
        ):
            process_pdf(pdf, "doc-1", db, MagicMock())

        doc = get_document(db, "doc-1")
        assert doc["status"] == "failed"
        assert "OCR not supported" in doc["error_message"]
        assert get_facts(db, "doc-1") == []

    def test_scanned_sets_failed(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification(pdf_type="scanned")),
        ):
            process_pdf(pdf, "doc-1", db, MagicMock())

        doc = get_document(db, "doc-1")
        assert doc["status"] == "failed"


class TestProcessPdfTextBased:
    def test_happy_path_extracts_facts(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        pages = [
            _mock_page(0, "Revenue: 500M USD"),
            _mock_page(1, "Net profit: 50M USD"),
        ]
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification(page_count=2)),
            patch("src.ingestion.pdf_inspector.extract_pages_markdown",
                  return_value=_mock_extraction_result(pages)),
        ):
            llm = _mock_llm([
                json.dumps([{
                    "subject": "Revenue", "predicate": "amount", "value": "500M",
                    "value_type": "numeric", "unit": "USD", "time_scope": None,
                    "qualifier": None, "confidence": 0.95,
                    "evidence_text": "Revenue: 500M USD",
                }]),
                json.dumps([{
                    "subject": "Net profit", "predicate": "amount", "value": "50M",
                    "value_type": "numeric", "unit": "USD", "time_scope": None,
                    "qualifier": None, "confidence": 0.9,
                    "evidence_text": "Net profit: 50M USD",
                }]),
            ])
            process_pdf(pdf, "doc-1", db, llm)

        doc = get_document(db, "doc-1")
        assert doc["status"] == "done"
        facts = get_facts(db, "doc-1")
        assert len(facts) == 2

    def test_ocr_pages_go_to_skipped(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        pages = [
            _mock_page(0, "Good text", needs_ocr=False),
            _mock_page(1, "", needs_ocr=True),
        ]
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification(pdf_type="mixed", page_count=2)),
            patch("src.ingestion.pdf_inspector.extract_pages_markdown",
                  return_value=_mock_extraction_result(pages)),
        ):
            llm = _mock_llm([json.dumps([{
                "subject": "S", "predicate": "P", "value": "V",
                "value_type": "text", "unit": None, "time_scope": None,
                "qualifier": None, "confidence": 0.9, "evidence_text": "Good text",
            }])])
            process_pdf(pdf, "doc-1", db, llm)

        doc = get_document(db, "doc-1")
        assert doc["status"] == "done"
        assert json.loads(doc["skipped_pages"]) == [1]
        assert json.loads(doc["failed_pages"]) == []

    def test_blank_page_silently_skipped(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        pages = [
            _mock_page(0, "   \n  "),  # blank
            _mock_page(1, "Real content"),
        ]
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification(page_count=2)),
            patch("src.ingestion.pdf_inspector.extract_pages_markdown",
                  return_value=_mock_extraction_result(pages)),
        ):
            llm = _mock_llm([json.dumps([{
                "subject": "X", "predicate": "Y", "value": "Z",
                "value_type": "text", "unit": None, "time_scope": None,
                "qualifier": None, "confidence": 0.8, "evidence_text": "Real content",
            }])])
            process_pdf(pdf, "doc-1", db, llm)

        doc = get_document(db, "doc-1")
        assert doc["status"] == "done"
        # blank page not in skipped or failed
        assert json.loads(doc["skipped_pages"]) == []
        assert json.loads(doc["failed_pages"]) == []

    def test_bad_llm_response_goes_to_failed_pages(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        pages = [_mock_page(0, "Some text")]
        with (
            patch("src.ingestion.pdf_inspector.classify_pdf",
                  return_value=_mock_classification(page_count=1)),
            patch("src.ingestion.pdf_inspector.extract_pages_markdown",
                  return_value=_mock_extraction_result(pages)),
        ):
            bad_client = MagicMock()
            bad_client.model = "test-model"
            bad_client.chat.side_effect = ["bad json", "still bad json"]
            process_pdf(pdf, "doc-1", db, bad_client)

        doc = get_document(db, "doc-1")
        assert doc["status"] == "done"  # doc still completes
        assert json.loads(doc["failed_pages"]) == [0]
        assert get_facts(db, "doc-1") == []

    def test_classify_exception_sets_failed(self, db, tmp_path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF fake")
        with patch("src.ingestion.pdf_inspector.classify_pdf",
                   side_effect=RuntimeError("disk error")):
            process_pdf(pdf, "doc-1", db, MagicMock())

        doc = get_document(db, "doc-1")
        assert doc["status"] == "failed"
        assert "Classification error" in doc["error_message"]
