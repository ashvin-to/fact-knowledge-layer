"""
FastAPI application — fact-extraction and cross-document comparison service.

Routes
------
  POST /documents
      Upload a PDF. Runs classification + extraction synchronously.
      Returns 201 DocumentUploadResponse.

  GET /documents/{document_id}
      Returns the document row. 404 if not found.

  GET /documents/{document_id}/facts
      Returns {document_id, facts:[...]}. 404 if document not found.

  POST /compare
      Compare facts across already-processed documents (status='done').
      Accepts optional JSON body {"document_ids": [...]}.
      Returns summary counts of evaluated relationships.

  GET /relationships
      Returns all relationships with inlined fact details.
      Accepts optional ?type=corroborate|contradict|context_reconciled|unrelated filter.

  GET /facts/{fact_id}/relationships
      Returns all relationships involving a specific fact with inlined details.

Startup
-------
  uv run uvicorn src.main:app --reload
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pymupdf
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .comparison import run_comparison_pipeline
from .db import (
    count_facts,
    decode_int_list,
    get_connection,
    get_document,
    get_facts,
    get_relationships_inlined,
    init_db,
    insert_document,
    update_document,
)
from .ingestion import process_pdf
from .llm_client import LLMClient, get_extractor_client, get_reasoner_client
from .models import (
    BBoxItem,
    CompareRequest,
    CompareResponse,
    Document,
    DocumentListItem,
    DocumentListResponse,
    DocumentUploadResponse,
    EvidenceBBoxResponse,
    Fact,
    FactRelationshipItem,
    FactsResponse,
    RelationshipsResponse,
)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

_DB_PATH = Path(os.environ.get("DB_PATH", "facts.db"))
_STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "storage"))

_PDF_MAGIC = b"%PDF"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    init_db(_DB_PATH)
    log.info("DB initialised at %s", _DB_PATH)
    yield


app = FastAPI(
    title="Fact Extraction & Comparison Service",
    description="Ingest PDFs, extract facts, ground evidence, and perform cross-document comparison.",
    version="0.3.0",
    lifespan=lifespan,
)

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_pdf(file: UploadFile, header: bytes) -> bool:
    """Check content-type header AND magic bytes."""
    ct = (file.content_type or "").lower()
    return ct == "application/pdf" and header.startswith(_PDF_MAGIC)


def _normalize_for_search(text: str) -> str:
    """Normalize markdown and whitespace for PDF text layer search."""
    cleaned = re.sub(r"[\*_#\|\x60]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Document Routes
# ---------------------------------------------------------------------------

@app.post(
    "/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentUploadResponse,
    summary="Upload a PDF and extract facts",
)
async def upload_document(file: UploadFile = File(...)):
    content = await file.read()

    if not _is_pdf(file, content[:4]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted (application/pdf with valid PDF magic bytes).",
        )

    filename = file.filename or "document.pdf"
    conn = get_connection(_DB_PATH)

    # Check if this document already exists in DB so we can resume progress
    existing = conn.execute(
        "SELECT id, storage_path FROM documents WHERE filename = ? ORDER BY upload_time DESC LIMIT 1",
        (filename,),
    ).fetchone()

    try:
        if existing:
            document_id = existing["id"]
            dest = Path(existing["storage_path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            log.info("Found existing document record %s for '%s' — resuming.", document_id, filename)
        else:
            document_id = str(uuid.uuid4())
            upload_time = datetime.now(timezone.utc).isoformat()
            dest = _STORAGE_DIR / f"{document_id}.pdf"
            dest.write_bytes(content)

            doc_row: dict = {
                "id": document_id,
                "filename": filename,
                "storage_path": str(dest),
                "upload_time": upload_time,
                "page_count": None,
                "pdf_type": None,
                "classification_confidence": None,
                "status": "pending",
                "skipped_pages": "[]",
                "failed_pages": "[]",
                "error_message": None,
            }
            insert_document(conn, doc_row)
            conn.commit()

        extractor_client = get_extractor_client()

        try:
            process_pdf(
                pdf_path=dest,
                document_id=document_id,
                conn=conn,
                llm_client=extractor_client,
            )
        except Exception as exc:
            log.exception("Unexpected error processing document %s", document_id)
            update_document(
                conn,
                document_id,
                status="failed",
                error_message=f"Unexpected processing error: {exc}",
            )
            conn.commit()

        doc = get_document(conn, document_id)
        fact_count = count_facts(conn, document_id)
    finally:
        conn.close()

    if doc is None:
        raise HTTPException(status_code=500, detail="Document record lost after processing.")

    return DocumentUploadResponse(
        document_id=doc["id"],
        filename=doc["filename"],
        page_count=doc["page_count"],
        pdf_type=doc["pdf_type"],
        status=doc["status"],
        fact_count=fact_count,
        skipped_pages=decode_int_list(doc["skipped_pages"]),
        failed_pages=decode_int_list(doc["failed_pages"]),
        error_message=doc["error_message"],
    )


@app.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List all uploaded documents",
)
async def list_documents_route():
    conn = get_connection(_DB_PATH)
    try:
        rows = conn.execute("SELECT * FROM documents ORDER BY upload_time DESC").fetchall()
        docs = []
        for r in rows:
            d = dict(r)
            d["fact_count"] = count_facts(conn, d["id"])
            d["skipped_pages"] = decode_int_list(d["skipped_pages"])
            d["failed_pages"] = decode_int_list(d["failed_pages"])
            docs.append(DocumentListItem.model_validate(d))
    finally:
        conn.close()

    return DocumentListResponse(documents=docs)


@app.get(
    "/documents/{document_id}",
    response_model=Document,
    summary="Get document metadata",
)
async def get_document_route(document_id: str):
    conn = get_connection(_DB_PATH)
    try:
        doc = get_document(conn, document_id)
    finally:
        conn.close()

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    return Document.model_validate(doc)


@app.get(
    "/documents/{document_id}/facts",
    response_model=FactsResponse,
    summary="Get all facts for a document",
)
async def get_facts_route(document_id: str):
    conn = get_connection(_DB_PATH)
    try:
        doc = get_document(conn, document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        rows = get_facts(conn, document_id)
    finally:
        conn.close()

    return FactsResponse(
        document_id=document_id,
        facts=[Fact.model_validate(r) for r in rows],
    )


@app.get(
    "/documents/{document_id}/pages/{page_index}/image",
    summary="Render and return a PDF page as PNG image",
)
async def get_page_image_route(document_id: str, page_index: int):
    conn = get_connection(_DB_PATH)
    try:
        doc_row = get_document(conn, document_id)
    finally:
        conn.close()

    if not doc_row:
        raise HTTPException(status_code=404, detail="Document not found.")

    pdf_path = Path(doc_row["storage_path"])
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF storage file not found on server.")

    try:
        pdf_doc = pymupdf.open(str(pdf_path))
        if page_index < 0 or page_index >= len(pdf_doc):
            raise HTTPException(
                status_code=400,
                detail=f"Page index {page_index} out of range (total pages: {len(pdf_doc)}).",
            )
        page = pdf_doc[page_index]
        pix = page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        pdf_doc.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to render page image: {exc}")

    return Response(content=png_bytes, media_type="image/png")


@app.get(
    "/documents/{document_id}/pages/{page_index}/evidence-bbox",
    response_model=EvidenceBBoxResponse,
    summary="Locate evidence text bounding boxes on a rendered page",
)
async def get_evidence_bbox_route(
    document_id: str,
    page_index: int,
    text: str = Query(..., description="Evidence text substring to locate on page"),
):
    conn = get_connection(_DB_PATH)
    try:
        doc_row = get_document(conn, document_id)
    finally:
        conn.close()

    if not doc_row:
        raise HTTPException(status_code=404, detail="Document not found.")

    pdf_path = Path(doc_row["storage_path"])
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF storage file not found on server.")

    try:
        pdf_doc = pymupdf.open(str(pdf_path))
        if page_index < 0 or page_index >= len(pdf_doc):
            raise HTTPException(
                status_code=400,
                detail=f"Page index {page_index} out of range (total pages: {len(pdf_doc)}).",
            )
        page = pdf_doc[page_index]
        rect = page.rect
        page_w = float(rect.width)
        page_h = float(rect.height)

        normalized = _normalize_for_search(text)
        match_type = "exact"
        hits = page.search_for(normalized) if normalized else []

        # Fallback: search first ~40 characters if exact normalized search has no hits
        if not hits and len(normalized) > 10:
            prefix = normalized[:40].strip()
            hits = page.search_for(prefix)
            if hits:
                match_type = "fallback_prefix"
            else:
                match_type = "none"
        elif not hits:
            match_type = "none"

        bboxes = [
            BBoxItem(
                x0=float(h.x0),
                y0=float(h.y0),
                x1=float(h.x1),
                y1=float(h.y1),
                page_width=page_w,
                page_height=page_h,
            )
            for h in hits
        ]
        pdf_doc.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to calculate evidence bbox: {exc}")

    return EvidenceBBoxResponse(
        bboxes=bboxes,
        page_width=page_w,
        page_height=page_h,
        normalized_query=normalized,
        match_type=match_type,
    )


# ---------------------------------------------------------------------------
# Cross-Document Comparison Routes
# ---------------------------------------------------------------------------

@app.post(
    "/compare",
    response_model=CompareResponse,
    summary="Compare facts across processed documents",
)
async def compare_documents_route(request: Optional[CompareRequest] = None):
    doc_ids = request.document_ids if request else None
    conn = get_connection(_DB_PATH)
    try:
        reasoner = get_reasoner_client()
        verifier = get_extractor_client()
        counts = run_comparison_pipeline(
            conn=conn,
            document_ids=doc_ids,
            reasoner_client=reasoner,
            verifier_client=verifier,
        )
    finally:
        conn.close()

    return CompareResponse(**counts)


@app.get(
    "/relationships",
    response_model=RelationshipsResponse,
    summary="Get fact relationships with inlined fact details",
)
async def get_relationships_route(type: Optional[str] = Query(None, description="Filter by relationship_type")):
    conn = get_connection(_DB_PATH)
    try:
        rows = get_relationships_inlined(conn, relationship_type=type)
    finally:
        conn.close()

    return RelationshipsResponse(
        relationships=[FactRelationshipItem.model_validate(r) for r in rows]
    )


@app.get(
    "/facts/{fact_id}/relationships",
    response_model=RelationshipsResponse,
    summary="Get all relationships involving a specific fact",
)
async def get_fact_relationships_route(fact_id: str):
    conn = get_connection(_DB_PATH)
    try:
        rows = get_relationships_inlined(conn, fact_id=fact_id)
    finally:
        conn.close()

    return RelationshipsResponse(
        relationships=[FactRelationshipItem.model_validate(r) for r in rows]
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start() -> None:
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
