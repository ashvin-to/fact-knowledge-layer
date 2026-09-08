"""
PDF ingestion pipeline.

Public API
----------
  process_pdf(pdf_path, document_id, conn, llm_client)
      Classifies the PDF, iterates pages, extracts facts, persists everything.
      Updates the document row in-place (status, page_count, skipped_pages, …).
      Commits are done per-page so partial progress survives crashes.

pdf_inspector notes (verified against 1.17.0)
----------------------------------------------
  PdfClassification
    .pdf_type         str  — "text_based" | "scanned" | "image_based" | "mixed"
    .confidence       float
    .page_count       int
    .pages_needing_ocr list[int]  — 0-indexed

  PagesExtractionResult
    .pages            list[PageMarkdown]  — in page order

  PageMarkdown
    .page             int   — 0-indexed
    .markdown         str
    .needs_ocr        bool  — True when text on this page is unreliable
    .ocr_reason       str | None
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pdf_inspector

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import fitz

from .db import decode_int_list, encode_int_list, get_document, insert_fact, insert_failed_page, update_document
from .extraction import (
    FactExtractionPageError,
    extract_facts_from_page,
    extract_facts_from_page_image,
)
from .llm_client import LLMClient

log = logging.getLogger(__name__)

# pdf_type values that mean "entire doc is image/scanned, no usable text"
_FULLY_IMAGE_TYPES = {"scanned", "image_based"}


def _process_single_page(
    page: Any,
    path_str: str,
    document_id: str,
    llm_client: LLMClient,
) -> tuple[int, list[dict], str | None, bool]:
    """
    Process one page.
    Returns (page_index, facts_list, error_message_or_None, was_skipped).
    """
    page_index: int = page.page

    # Check if page needs vision fallback
    if page.needs_ocr:
        try:
            img_bytes: bytes | None = None
            with fitz.open(path_str) as doc:
                if page_index < len(doc):
                    pix = doc[page_index].get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")

            if img_bytes:
                facts = extract_facts_from_page_image(
                    page_image_bytes=img_bytes,
                    page_index=page_index,
                    document_id=document_id,
                    llm_client=llm_client,
                )
                if facts:
                    return page_index, facts, None, False
        except Exception as exc:
            log.info("Vision fallback for page %d yielded no facts: %s", page_index, exc)

        return page_index, [], None, True  # Skipped OCR

    if not page.markdown or not page.markdown.strip():
        return page_index, [], None, False  # Blank page ignored silently

    try:

        facts = extract_facts_from_page(
            page_markdown=page.markdown,
            page_index=page_index,
            document_id=document_id,
            llm_client=llm_client,
        )
        return page_index, facts, None, False
    except FactExtractionPageError as exc:
        return page_index, [], str(exc), False


def process_pdf(
    pdf_path: Path,
    document_id: str,
    conn: sqlite3.Connection,
    llm_client: LLMClient,
    max_workers: int | None = None,
) -> None:
    """
    Full ingestion pipeline for one PDF.

    Reads the PDF, classifies it, extracts facts page-by-page, and persists
    everything. The document row must already exist in the DB with
    status='pending'. This function updates it to 'processing' → 'done'
    (or 'failed').

    All DB writes are committed per-page/batch so partial progress is durable.
    """
    path_str = str(pdf_path)

    # ------------------------------------------------------------------
    # 1. Mark processing
    # ------------------------------------------------------------------
    update_document(conn, document_id, status="processing")
    conn.commit()

    # ------------------------------------------------------------------
    # 2. Classify
    # ------------------------------------------------------------------
    try:
        classification = pdf_inspector.classify_pdf(path_str)
    except Exception as exc:
        log.exception("classify_pdf failed for %s", pdf_path)
        update_document(
            conn,
            document_id,
            status="failed",
            error_message=f"Classification error: {exc}",
        )
        conn.commit()
        return

    pdf_type: str = classification.pdf_type
    page_count: int = classification.page_count
    confidence: float = classification.confidence

    update_document(
        conn,
        document_id,
        page_count=page_count,
        pdf_type=pdf_type,
        classification_confidence=confidence,
    )
    conn.commit()

    # ------------------------------------------------------------------
    # 3. Bail out if entirely image/scanned (no usable text at all)
    # ------------------------------------------------------------------
    if pdf_type in _FULLY_IMAGE_TYPES:
        log.warning(
            "Document %s is %s — no extractable text; stopping.", document_id, pdf_type
        )
        update_document(
            conn,
            document_id,
            status="failed",
            error_message="no extractable text; OCR not supported in this phase",
        )
        conn.commit()
        return

    # ------------------------------------------------------------------
    # 4. Extract page markdowns
    # ------------------------------------------------------------------
    try:
        extraction_result = pdf_inspector.extract_pages_markdown(path_str)
    except Exception as exc:
        log.exception("extract_pages_markdown failed for %s", pdf_path)
        update_document(
            conn,
            document_id,
            status="failed",
            error_message=f"Page extraction error: {exc}",
        )
        conn.commit()
        return

    pages = extraction_result.pages  # list[PageMarkdown], 0-indexed .page

    # ------------------------------------------------------------------
    # 5. Process pages with parallel worker pool & per-page durability
    # ------------------------------------------------------------------
    processed_pages = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT pdf_page_index FROM facts WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    }

    existing_doc = get_document(conn, document_id)
    skipped_pages: list[int] = (
        decode_int_list(existing_doc["skipped_pages"]) if existing_doc and existing_doc["skipped_pages"] else []
    )
    failed_pages: list[int] = []

    pages_to_process = [p for p in pages if p.page not in processed_pages]

    if max_workers is None:
        env_w = os.environ.get("MAX_INGESTION_WORKERS", "1")
        try:
            max_workers = max(1, int(env_w))
        except ValueError:
            max_workers = 1

    # Sequential or thread pool processing
    if max_workers <= 1 or len(pages_to_process) <= 1:
        for page in pages_to_process:
            page_index, facts, error_msg, was_skipped = _process_single_page(
                page, path_str, document_id, llm_client
            )
            if was_skipped:
                if page_index not in skipped_pages:
                    skipped_pages.append(page_index)
            elif error_msg:
                log.warning("Page %d extraction failed: %s", page_index, error_msg)
                failed_pages.append(page_index)
                insert_failed_page(
                    conn,
                    document_id=document_id,
                    pdf_page_index=page_index,
                    error_message=error_msg,
                    page_markdown=page.markdown or "",
                )
            else:
                for fact in facts:
                    insert_fact(conn, fact)
                conn.execute(
                    "DELETE FROM failed_page_contents WHERE document_id = ? AND pdf_page_index = ?",
                    (document_id, page_index),
                )
                log.info("Page %d: inserted %d fact(s).", page_index, len(facts))

            update_document(
                conn,
                document_id,
                skipped_pages=encode_int_list(skipped_pages),
                failed_pages=encode_int_list(failed_pages),
            )
            conn.commit()
    else:
        # Multi-worker parallel processing
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_page = {
                executor.submit(_process_single_page, p, path_str, document_id, llm_client): p
                for p in pages_to_process
            }
            for future in as_completed(future_to_page):
                page_index, facts, error_msg, was_skipped = future.result()
                if was_skipped:
                    if page_index not in skipped_pages:
                        skipped_pages.append(page_index)
                elif error_msg:
                    failed_pages.append(page_index)
                    insert_failed_page(
                        conn,
                        document_id=document_id,
                        pdf_page_index=page_index,
                        error_message=error_msg,
                        page_markdown=future_to_page[future].markdown or "",
                    )
                else:
                    for fact in facts:
                        insert_fact(conn, fact)
                    conn.execute(
                        "DELETE FROM failed_page_contents WHERE document_id = ? AND pdf_page_index = ?",
                        (document_id, page_index),
                    )

                update_document(
                    conn,
                    document_id,
                    skipped_pages=encode_int_list(skipped_pages),
                    failed_pages=encode_int_list(failed_pages),
                )
                conn.commit()

    # ------------------------------------------------------------------
    # 6. Finalise document status
    # ------------------------------------------------------------------
    update_document(
        conn,
        document_id,
        status="done",
        skipped_pages=encode_int_list(skipped_pages),
        failed_pages=encode_int_list(failed_pages),
    )
    conn.commit()
    log.info(
        "Document %s done. skipped=%s failed=%s", document_id, skipped_pages, failed_pages
    )


