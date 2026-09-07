"""
SQLite persistence layer — documents, facts, and fact_relationships.

Schema
------
  documents          — one row per uploaded PDF
  facts              — one row per extracted fact, FK → documents.id, with embedding columns
  fact_relationships — pairwise cross-document comparison verdicts

All datetime values are stored as ISO-8601 UTC strings.
skipped_pages / failed_pages are JSON arrays stored as TEXT.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema & Migrations
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    id                        TEXT PRIMARY KEY,
    filename                  TEXT NOT NULL,
    storage_path              TEXT NOT NULL,
    upload_time               TEXT NOT NULL,
    page_count                INTEGER,
    pdf_type                  TEXT,
    classification_confidence REAL,
    status                    TEXT NOT NULL DEFAULT 'pending',
    skipped_pages             TEXT,
    failed_pages              TEXT,
    error_message             TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id                 TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL REFERENCES documents(id),
    subject            TEXT NOT NULL,
    predicate          TEXT NOT NULL,
    value              TEXT NOT NULL,
    value_type         TEXT NOT NULL CHECK (value_type IN ('numeric','categorical','boolean','text')),
    unit               TEXT,
    time_scope         TEXT,
    qualifier          TEXT,
    confidence         REAL NOT NULL,
    pdf_page_index     INTEGER NOT NULL,
    evidence_text      TEXT NOT NULL,
    extraction_method  TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    embedding          BLOB,
    embedding_model    TEXT
);

CREATE TABLE IF NOT EXISTS fact_relationships (
    id                      TEXT PRIMARY KEY,
    fact_id_a               TEXT NOT NULL REFERENCES facts(id),
    fact_id_b               TEXT NOT NULL REFERENCES facts(id),
    relationship_type       TEXT NOT NULL CHECK (relationship_type IN
                                ('corroborate','contradict','context_reconciled','unrelated')),
    explanation             TEXT NOT NULL,
    reconciliation_factor   TEXT,
    confidence              REAL NOT NULL,
    similarity_score        REAL NOT NULL,
    reasoner_model          TEXT NOT NULL,
    needs_review            INTEGER NOT NULL DEFAULT 0,
    second_opinion_verdict  TEXT,
    second_opinion_model    TEXT,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS failed_page_contents (
    id                 TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    pdf_page_index     INTEGER NOT NULL,
    error_message      TEXT,
    page_markdown      TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
"""


def init_db(db_path: str | Path) -> None:
    """Create tables and apply migrations if they do not already exist."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_DDL)
        # Migrate existing facts table if missing embedding columns
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()
        }
        if "embedding" not in cols:
            conn.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")
        if "embedding_model" not in cols:
            conn.execute("ALTER TABLE facts ADD COLUMN embedding_model TEXT")
        conn.commit()


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return a sqlite3 connection with tables initialized and row_factory set to dict-like rows."""
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def insert_document(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:
    """Insert a new document row. Caller must commit."""
    full_doc = {
        "page_count": None,
        "pdf_type": None,
        "classification_confidence": None,
        "status": "pending",
        "skipped_pages": "[]",
        "failed_pages": "[]",
        "error_message": None,
        **doc,
    }
    conn.execute(
        """
        INSERT INTO documents
            (id, filename, storage_path, upload_time, page_count, pdf_type,
             classification_confidence, status, skipped_pages, failed_pages, error_message)
        VALUES
            (:id, :filename, :storage_path, :upload_time, :page_count, :pdf_type,
             :classification_confidence, :status, :skipped_pages, :failed_pages, :error_message)
        """,
        full_doc,
    )


def update_document(conn: sqlite3.Connection, doc_id: str, **fields: Any) -> None:
    """Update arbitrary columns on a document row. Caller must commit."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["_id"] = doc_id
    conn.execute(f"UPDATE documents SET {set_clause} WHERE id = :_id", fields)


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
    """Return a document row as a plain dict, or None if not found."""
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return dict(row) if row else None


def get_done_documents(conn: sqlite3.Connection, doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return documents currently in status='done'."""
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        rows = conn.execute(
            f"SELECT * FROM documents WHERE status = 'done' AND id IN ({placeholders})",
            doc_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM documents WHERE status = 'done'"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

def insert_fact(conn: sqlite3.Connection, fact: dict[str, Any]) -> None:
    """Insert a single fact row. Caller must commit."""
    conn.execute(
        """
        INSERT INTO facts
            (id, document_id, subject, predicate, value, value_type, unit,
             time_scope, qualifier, confidence, pdf_page_index, evidence_text,
             extraction_method, created_at, embedding, embedding_model)
        VALUES
            (:id, :document_id, :subject, :predicate, :value, :value_type, :unit,
             :time_scope, :qualifier, :confidence, :pdf_page_index, :evidence_text,
             :extraction_method, :created_at, :embedding, :embedding_model)
        """,
        {
            "embedding": None,
            "embedding_model": None,
            **fact,
        },
    )


def get_facts(conn: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
    """Return all facts for a document as a list of plain dicts."""
    rows = conn.execute(
        "SELECT * FROM facts WHERE document_id = ? ORDER BY pdf_page_index, created_at",
        (doc_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_facts_for_documents(
    conn: sqlite3.Connection, doc_ids: list[str]
) -> list[dict[str, Any]]:
    """Return all facts belonging to the given document IDs."""
    if not doc_ids:
        return []
    placeholders = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        f"""
        SELECT f.*, d.filename AS document_filename
        FROM facts f
        JOIN documents d ON f.document_id = d.id
        WHERE f.document_id IN ({placeholders})
        ORDER BY f.document_id, f.pdf_page_index, f.created_at
        """,
        doc_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def count_facts(conn: sqlite3.Connection, doc_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE document_id = ?", (doc_id,)
    ).fetchone()
    return row[0] if row else 0


def update_fact_embedding(
    conn: sqlite3.Connection,
    fact_id: str,
    embedding: bytes,
    embedding_model: str,
) -> None:
    """Update embedding BLOB and model name for a fact."""
    conn.execute(
        """
        UPDATE facts
        SET embedding = ?, embedding_model = ?
        WHERE id = ?
        """,
        (embedding, embedding_model, fact_id),
    )


# ---------------------------------------------------------------------------
# Failed Pages
# ---------------------------------------------------------------------------

def insert_failed_page(
    conn: sqlite3.Connection,
    document_id: str,
    pdf_page_index: int,
    error_message: str,
    page_markdown: str,
) -> None:
    """Record a failed page and its raw content for inspection / retry."""
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO failed_page_contents
            (id, document_id, pdf_page_index, error_message, page_markdown, created_at)
        VALUES
            (?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), document_id, pdf_page_index, error_message, page_markdown, now),
    )


def get_failed_pages(
    conn: sqlite3.Connection, document_id: str
) -> list[dict[str, Any]]:
    """Return all recorded failed page contents for a document."""
    rows = conn.execute(
        "SELECT * FROM failed_page_contents WHERE document_id = ? ORDER BY pdf_page_index",
        (document_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Fact Relationships
# ---------------------------------------------------------------------------

def insert_fact_relationship(
    conn: sqlite3.Connection, rel: dict[str, Any]
) -> None:
    """Insert a fact relationship verdict row."""
    conn.execute(
        """
        INSERT INTO fact_relationships
            (id, fact_id_a, fact_id_b, relationship_type, explanation,
             reconciliation_factor, confidence, similarity_score,
             reasoner_model, needs_review, second_opinion_verdict,
             second_opinion_model, created_at)
        VALUES
            (:id, :fact_id_a, :fact_id_b, :relationship_type, :explanation,
             :reconciliation_factor, :confidence, :similarity_score,
             :reasoner_model, :needs_review, :second_opinion_verdict,
             :second_opinion_model, :created_at)
        """,
        rel,
    )


def get_existing_relationship_pairs(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """
    Return all existing relationship pairs as canonical (min_id, max_id) tuples
    so candidate generation skips previously evaluated pairs in either direction.
    """
    rows = conn.execute(
        "SELECT fact_id_a, fact_id_b FROM fact_relationships"
    ).fetchall()
    pairs: set[tuple[str, str]] = set()
    for r in rows:
        a, b = r[0], r[1]
        pairs.add((min(a, b), max(a, b)))
    return pairs


def get_relationships_inlined(
    conn: sqlite3.Connection,
    relationship_type: str | None = None,
    fact_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return relationship rows with full details of both fact_a and fact_b inlined,
    including their source document filenames.
    """
    query = """
        SELECT
            r.id,
            r.relationship_type,
            r.explanation,
            r.reconciliation_factor,
            r.confidence,
            r.similarity_score,
            r.reasoner_model,
            r.needs_review,
            r.second_opinion_verdict,
            r.second_opinion_model,
            r.created_at,
            -- Fact A
            fa.id AS fa_id,
            fa.document_id AS fa_doc_id,
            da.filename AS fa_doc_filename,
            fa.subject AS fa_subject,
            fa.predicate AS fa_predicate,
            fa.value AS fa_value,
            fa.value_type AS fa_value_type,
            fa.unit AS fa_unit,
            fa.time_scope AS fa_time_scope,
            fa.qualifier AS fa_qualifier,
            fa.confidence AS fa_confidence,
            fa.pdf_page_index AS fa_pdf_page_index,
            fa.evidence_text AS fa_evidence_text,
            -- Fact B
            fb.id AS fb_id,
            fb.document_id AS fb_doc_id,
            db.filename AS fb_doc_filename,
            fb.subject AS fb_subject,
            fb.predicate AS fb_predicate,
            fb.value AS fb_value,
            fb.value_type AS fb_value_type,
            fb.unit AS fb_unit,
            fb.time_scope AS fb_time_scope,
            fb.qualifier AS fb_qualifier,
            fb.confidence AS fb_confidence,
            fb.pdf_page_index AS fb_pdf_page_index,
            fb.evidence_text AS fb_evidence_text
        FROM fact_relationships r
        JOIN facts fa ON r.fact_id_a = fa.id
        JOIN documents da ON fa.document_id = da.id
        JOIN facts fb ON r.fact_id_b = fb.id
        JOIN documents db ON fb.document_id = db.id
    """
    conditions = []
    params: list[Any] = []

    if relationship_type:
        conditions.append("r.relationship_type = ?")
        params.append(relationship_type)

    if fact_id:
        conditions.append("(r.fact_id_a = ? OR r.fact_id_b = ?)")
        params.extend([fact_id, fact_id])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY r.created_at DESC"

    rows = conn.execute(query, params).fetchall()

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "relationship_type": r["relationship_type"],
            "explanation": r["explanation"],
            "reconciliation_factor": r["reconciliation_factor"],
            "confidence": r["confidence"],
            "similarity_score": r["similarity_score"],
            "reasoner_model": r["reasoner_model"],
            "needs_review": r["needs_review"],
            "second_opinion_verdict": r["second_opinion_verdict"],
            "second_opinion_model": r["second_opinion_model"],
            "created_at": r["created_at"],
            "fact_a": {
                "id": r["fa_id"],
                "document_id": r["fa_doc_id"],
                "document_filename": r["fa_doc_filename"],
                "subject": r["fa_subject"],
                "predicate": r["fa_predicate"],
                "value": r["fa_value"],
                "value_type": r["fa_value_type"],
                "unit": r["fa_unit"],
                "time_scope": r["fa_time_scope"],
                "qualifier": r["fa_qualifier"],
                "confidence": r["fa_confidence"],
                "pdf_page_index": r["fa_pdf_page_index"],
                "evidence_text": r["fa_evidence_text"],
            },
            "fact_b": {
                "id": r["fb_id"],
                "document_id": r["fb_doc_id"],
                "document_filename": r["fb_doc_filename"],
                "subject": r["fb_subject"],
                "predicate": r["fb_predicate"],
                "value": r["fb_value"],
                "value_type": r["fb_value_type"],
                "unit": r["fb_unit"],
                "time_scope": r["fb_time_scope"],
                "qualifier": r["fb_qualifier"],
                "confidence": r["fb_confidence"],
                "pdf_page_index": r["fb_pdf_page_index"],
                "evidence_text": r["fb_evidence_text"],
            },
        })

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def encode_int_list(values: list[int]) -> str:
    """Serialize a list of ints to a JSON string for storage."""
    return json.dumps(values)


def decode_int_list(text: str | None) -> list[int]:
    """Deserialize a JSON string back to a list of ints."""
    if not text:
        return []
    return json.loads(text)
