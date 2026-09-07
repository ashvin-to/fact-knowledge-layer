"""Tests for the SQLite persistence layer (src/db.py)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.db import (
    count_facts,
    decode_int_list,
    encode_int_list,
    get_connection,
    get_document,
    get_facts,
    init_db,
    insert_document,
    insert_fact,
    update_document,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture()
def conn(db_path: Path):
    c = get_connection(db_path)
    yield c
    c.close()


def _doc(doc_id: str = "doc-1") -> dict:
    return {
        "id": doc_id,
        "filename": "test.pdf",
        "storage_path": "/tmp/test.pdf",
        "upload_time": "2026-01-01T00:00:00+00:00",
        "page_count": None,
        "pdf_type": None,
        "classification_confidence": None,
        "status": "pending",
        "skipped_pages": "[]",
        "failed_pages": "[]",
        "error_message": None,
    }


def _fact(doc_id: str = "doc-1", fact_id: str = "fact-1") -> dict:
    return {
        "id": fact_id,
        "document_id": doc_id,
        "subject": "Revenue",
        "predicate": "amount",
        "value": "100",
        "value_type": "numeric",
        "unit": "USD",
        "time_scope": "Q1 2026",
        "qualifier": None,
        "confidence": 0.95,
        "pdf_page_index": 0,
        "evidence_text": "Revenue: 100 USD",
        "extraction_method": "local:qwen2.5:7b-instruct",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


class TestInitDb:
    def test_creates_tables(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "documents" in tables
        assert "facts" in tables

    def test_idempotent(self, db_path: Path) -> None:
        # Second call must not raise
        init_db(db_path)


class TestDocuments:
    def test_insert_and_get(self, conn, db_path) -> None:
        doc = _doc()
        insert_document(conn, doc)
        conn.commit()
        result = get_document(conn, "doc-1")
        assert result is not None
        assert result["filename"] == "test.pdf"
        assert result["status"] == "pending"

    def test_get_missing_returns_none(self, conn) -> None:
        assert get_document(conn, "no-such-id") is None

    def test_update_status(self, conn) -> None:
        insert_document(conn, _doc())
        conn.commit()
        update_document(conn, "doc-1", status="done", page_count=5)
        conn.commit()
        result = get_document(conn, "doc-1")
        assert result["status"] == "done"
        assert result["page_count"] == 5

    def test_update_no_fields_is_noop(self, conn) -> None:
        insert_document(conn, _doc())
        conn.commit()
        update_document(conn, "doc-1")  # no kwargs — must not raise
        conn.commit()


class TestFacts:
    def test_insert_and_get(self, conn) -> None:
        insert_document(conn, _doc())
        conn.commit()
        insert_fact(conn, _fact())
        conn.commit()
        facts = get_facts(conn, "doc-1")
        assert len(facts) == 1
        assert facts[0]["subject"] == "Revenue"

    def test_get_facts_empty(self, conn) -> None:
        insert_document(conn, _doc())
        conn.commit()
        assert get_facts(conn, "doc-1") == []

    def test_count_facts(self, conn) -> None:
        insert_document(conn, _doc())
        conn.commit()
        insert_fact(conn, _fact(fact_id="f1"))
        insert_fact(conn, _fact(fact_id="f2"))
        conn.commit()
        assert count_facts(conn, "doc-1") == 2

    def test_value_type_constraint(self, conn) -> None:
        insert_document(conn, _doc())
        conn.commit()
        bad = _fact()
        bad["value_type"] = "invalid"
        with pytest.raises(sqlite3.IntegrityError):
            insert_fact(conn, bad)
            conn.commit()


class TestHelpers:
    def test_encode_decode_roundtrip(self) -> None:
        lst = [0, 3, 7]
        assert decode_int_list(encode_int_list(lst)) == lst

    def test_decode_none(self) -> None:
        assert decode_int_list(None) == []

    def test_decode_empty_string(self) -> None:
        assert decode_int_list("") == []


class TestFailedPages:
    def test_insert_and_get_failed_pages(self, conn) -> None:
        from src.db import get_failed_pages, insert_failed_page

        insert_document(conn, _doc("doc-failed-1"))
        conn.commit()

        insert_failed_page(
            conn,
            document_id="doc-failed-1",
            pdf_page_index=27,
            error_message="JSON parse error: Invalid control character",
            page_markdown="# Sample page 27 markdown content",
        )
        conn.commit()

        failed = get_failed_pages(conn, "doc-failed-1")
        assert len(failed) == 1
        assert failed[0]["pdf_page_index"] == 27
        assert "Invalid control character" in failed[0]["error_message"]
        assert failed[0]["page_markdown"] == "# Sample page 27 markdown content"
