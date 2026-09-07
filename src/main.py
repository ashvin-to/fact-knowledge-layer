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
    adjudicate_relationship,
)
from .ingestion import process_pdf
from .llm_client import LLMClient, get_extractor_client, get_reasoner_client
from .models import (
    AdjudicateRequest,
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
    SynthesisResponse,
)
from .synthesis import run_multi_hop_synthesis


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


@app.get("/health", summary="Health check endpoint")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": str(_DB_PATH),
    }



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


def _find_evidence_bboxes(page: pymupdf.Page, text: str) -> tuple[list[pymupdf.Rect], str, str]:
    """Robustly locate evidence text bounding boxes on a page using multi-strategy search.
    
    Strategies:
      1. Exact full-string match (cleaned of markdown and outer quotes)
      2. Direct prefix search for long inputs
      3. Word token sequence proximity search (handles multi-line wraps & formatting drift)
      4. Sliding phrase chunks
      5. Distinctive numeric / keyword anchor search
    """
    if not text or not text.strip():
        return [], "", "none"

    # 1. Clean markdown formatting
    cleaned = _normalize_for_search(text)
    if not cleaned:
        return [], "", "none"

    hits = page.search_for(cleaned)
    if hits:
        return hits, cleaned, "exact"

    stripped = cleaned.strip(" \t\n\r\"'“”«»`~:;.,()[]{}")
    if stripped and stripped != cleaned:
        hits = page.search_for(stripped)
        if hits:
            return hits, stripped, "exact"

    # 2. Fallback prefix search for long inputs
    if len(cleaned) > 10:
        prefix = cleaned[:40].strip()
        hits = page.search_for(prefix)
        if hits:
            return hits, prefix, "fallback_prefix"

    # 3. Token-level consecutive sequence matching (multi-line aware)
    page_words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if page_words:
        target_tokens = [re.sub(r"[^\w]", "", w.lower()) for w in text.split() if re.sub(r"[^\w]", "", w.lower())]
        page_tokens = [re.sub(r"[^\w]", "", w[4].lower()) for w in page_words]
        n_target = len(target_tokens)

        if n_target >= 2 and len(page_tokens) > 0:
            best_match: list[tuple] = []
            best_score = 0

            for i in range(len(page_tokens)):
                match_curr: list[tuple] = []
                t_idx = 0
                p_idx = i
                while p_idx < len(page_tokens) and t_idx < n_target:
                    if page_tokens[p_idx] == target_tokens[t_idx]:
                        match_curr.append(page_words[p_idx])
                        t_idx += 1
                        p_idx += 1
                    elif page_tokens[p_idx] in target_tokens[t_idx : t_idx + 3]:
                        idx_in_sub = target_tokens[t_idx : t_idx + 3].index(page_tokens[p_idx])
                        match_curr.append(page_words[p_idx])
                        t_idx += idx_in_sub + 1
                        p_idx += 1
                    else:
                        p_idx += 1
                        if p_idx - i > n_target + 5:
                            break

                if len(match_curr) > best_score:
                    best_score = len(match_curr)
                    best_match = match_curr

            if best_score >= min(3, n_target):
                line_groups: dict[tuple[int, int], list[tuple]] = {}
                for w in best_match:
                    key = (w[5], w[6])
                    line_groups.setdefault(key, []).append(w)
                rects = [
                    pymupdf.Rect(
                        min(w[0] for w in words_in_line),
                        min(w[1] for w in words_in_line),
                        max(w[2] for w in words_in_line),
                        max(w[3] for w in words_in_line),
                    )
                    for words_in_line in line_groups.values()
                ]
                return rects, cleaned, "token_sequence"

    # 4. Distinctive numeric anchor fallback (e.g. 21,342, 81417)
    numbers = re.findall(r"\b\d+(?:[.,]\d+)*\b", cleaned)
    for num in numbers:
        if len(num) >= 3 or (len(num) >= 2 and "." in num):
            hits = page.search_for(num)
            if hits:
                return hits, num, "numeric_anchor"

    return [], cleaned, "none"


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

        hits, normalized, match_type = _find_evidence_bboxes(page, text)

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
        rects=bboxes,
        page_width=page_w,
        page_height=page_h,
        normalized_query=normalized,
        match_type=match_type,
        match_method=match_type,
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


@app.post(
    "/relationships/{relationship_id}/adjudicate",
    summary="Human-in-the-loop audit adjudication and relationship override",
)
async def adjudicate_relationship_route(
    relationship_id: str,
    request: AdjudicateRequest,
):
    conn = get_connection(_DB_PATH)
    try:
        rel = conn.execute(
            "SELECT * FROM fact_relationships WHERE id = ?", (relationship_id,)
        ).fetchone()
        if not rel:
            raise HTTPException(status_code=404, detail="Relationship not found.")

        rel_type_str = request.relationship_type.value if hasattr(request.relationship_type, "value") else str(request.relationship_type)
        adjudicate_relationship(
            conn=conn,
            relationship_id=relationship_id,
            relationship_type=rel_type_str,
            user_adjudication_status=request.status,
            user_notes=request.notes,
            reconciliation_factor=request.reconciliation_factor,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "relationship_id": relationship_id,
        "relationship_type": request.relationship_type,
        "user_adjudication_status": request.status,
    }


@app.get(
    "/synthesis/trajectories",
    response_model=SynthesisResponse,
    summary="Discover and synthesize multi-hop fact trajectories across documents",
)
async def get_synthesis_trajectories_route(
    min_docs: int = Query(2, description="Minimum number of distinct documents required for a trajectory"),
    refresh: bool = Query(False, description="Force re-synthesis instead of reading cached narratives"),
):
    conn = get_connection(_DB_PATH)
    try:
        reasoner = get_reasoner_client()
        trajectories = run_multi_hop_synthesis(
            conn, min_docs=min_docs, reasoner_client=reasoner, force_refresh=refresh
        )
    finally:
        conn.close()


    return SynthesisResponse(
        total_trajectories=len(trajectories),
        trajectories=trajectories,
    )


@app.get(
    "/graph",
    summary="Get complete knowledge graph data (nodes and links) for visualization",
)
async def get_knowledge_graph_route(
    document_id: Optional[str] = Query(None, description="Optional filter by document ID"),
    relationship_type: Optional[str] = Query(None, description="Optional filter by relationship type"),
):
    conn = get_connection(_DB_PATH)
    try:
        # 1. Fetch documents
        doc_rows = conn.execute("SELECT id, filename, status, page_count, pdf_type FROM documents").fetchall()
        docs_map = {r["id"]: dict(r) for r in doc_rows}

        # 2. Fetch facts
        if document_id:
            fact_rows = conn.execute(
                "SELECT * FROM facts WHERE document_id = ?", (document_id,)
            ).fetchall()
        else:
            fact_rows = conn.execute("SELECT * FROM facts").fetchall()

        facts_list = [dict(r) for r in fact_rows]
        fact_id_set = {f["id"] for f in facts_list}

        # 3. Fetch relationships
        rel_rows = get_relationships_inlined(conn, relationship_type=relationship_type)

        nodes = []
        links = []

        # Add document nodes
        for d_id, d_data in docs_map.items():
            if document_id and d_id != document_id:
                continue
            nodes.append({
                "id": f"doc-{d_id}",
                "node_type": "document",
                "label": d_data["filename"],
                "document_id": d_id,
                "filename": d_data["filename"],
                "page_count": d_data["page_count"],
                "status": d_data["status"],
            })

        # Add fact nodes
        for f in facts_list:
            nodes.append({
                "id": f"fact-{f['id']}",
                "node_type": "fact",
                "label": f"{f['subject']}: {f['predicate']}",
                "fact_id": f["id"],
                "document_id": f["document_id"],
                "filename": docs_map.get(f["document_id"], {}).get("filename", "Unknown"),
                "subject": f["subject"],
                "predicate": f["predicate"],
                "value": f["value"],
                "unit": f["unit"],
                "time_scope": f["time_scope"],
                "confidence": f["confidence"],
                "pdf_page_index": f["pdf_page_index"],
                "evidence_text": f["evidence_text"],
            })
            # Link document -> fact
            links.append({
                "source": f"doc-{f['document_id']}",
                "target": f"fact-{f['id']}",
                "link_type": "contains",
                "label": "contains",
            })

        # Add cross-fact relationship links
        for r in rel_rows:
            f_a = r.get("fact_id_a") or (r.get("fact_a") or {}).get("id")
            f_b = r.get("fact_id_b") or (r.get("fact_b") or {}).get("id")
            if f_a in fact_id_set and f_b in fact_id_set:
                links.append({
                    "id": r.get("id"),
                    "source": f"fact-{f_a}",
                    "target": f"fact-{f_b}",
                    "link_type": r.get("relationship_type", "unrelated"),
                    "label": r.get("relationship_type"),
                    "explanation": r.get("explanation"),
                    "reconciliation_factor": r.get("reconciliation_factor"),
                    "confidence": r.get("confidence"),
                    "similarity_score": r.get("similarity_score"),
                    "needs_review": bool(r.get("needs_review")),
                    "user_adjudication_status": r.get("user_adjudication_status"),
                    "user_notes": r.get("user_notes"),
                })

        return {
            "nodes": nodes,
            "links": links,
            "summary": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "document_count": len(docs_map),
                "fact_count": len(facts_list),
                "relationship_count": len(rel_rows),
            }
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start() -> None:
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
