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

from .db import encode_int_list, insert_fact, insert_failed_page, update_document
from .extraction import FactExtractionPageError, extract_facts_from_page
from .llm_client import LLMClient

log = logging.getLogger(__name__)

# pdf_type values that mean "entire doc is image/scanned, no usable text"
_FULLY_IMAGE_TYPES = {"scanned", "image_based"}


def process_pdf(
    pdf_path: Path,
    document_id: str,
    conn: sqlite3.Connection,
    llm_client: LLMClient,
) -> None:
    """
    Full ingestion pipeline for one PDF.

    Reads the PDF, classifies it, extracts facts page-by-page, and persists
    everything.  The document row must already exist in the DB with
    status='pending'.  This function updates it to 'processing' → 'done'
    (or 'failed').

    All DB writes are committed after each page so partial progress is
    durable.
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
    # 5. Iterate pages
    # ------------------------------------------------------------------
    skipped_pages: list[int] = []
    failed_pages: list[int] = []

    # Resume support: skip directly to the next unprocessed page after last saved fact
    max_page_row = conn.execute(
        "SELECT MAX(pdf_page_index) FROM facts WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    last_processed_page = (
        max_page_row[0] if (max_page_row and max_page_row[0] is not None) else -1
    )
    if last_processed_page >= 0:
        log.info(
            "Document %s has facts up to page %d — resuming from page %d.",
            document_id,
            last_processed_page,
            last_processed_page + 1,
        )

    for page in pages:
        page_index: int = page.page  # 0-indexed

        # Fast forward directly past already-processed pages
        if page_index <= last_processed_page:
            continue

        # Skip OCR-needed pages (unreliable / image-only text)
        if page.needs_ocr:
            log.info("Page %d needs OCR — skipping.", page_index)
            skipped_pages.append(page_index)
            continue

        # Skip blank / whitespace-only pages silently
        if not page.markdown or not page.markdown.strip():
            log.debug("Page %d is blank — skipping silently.", page_index)
            continue

        # Extract facts
        try:
            facts = extract_facts_from_page(
                page_markdown=page.markdown,
                page_index=page_index,
                document_id=document_id,
                llm_client=llm_client,
            )
        except FactExtractionPageError as exc:
            log.warning("Page %d fact extraction failed: %s", page_index, exc)
            failed_pages.append(page_index)
            insert_failed_page(
                conn,
                document_id=document_id,
                pdf_page_index=page_index,
                error_message=str(exc),
                page_markdown=page.markdown,
            )
            # Persist skipped/failed progress so far and continue
            update_document(
                conn,
                document_id,
                skipped_pages=encode_int_list(skipped_pages),
                failed_pages=encode_int_list(failed_pages),
            )
            conn.commit()
            continue
        except Exception as exc:
            log.exception(
                "Unexpected error on page %d for document %s", page_index, document_id
            )
            failed_pages.append(page_index)
            insert_failed_page(
                conn,
                document_id=document_id,
                pdf_page_index=page_index,
                error_message=f"Unexpected error: {exc}",
                page_markdown=page.markdown,
            )
            update_document(
                conn,
                document_id,
                skipped_pages=encode_int_list(skipped_pages),
                failed_pages=encode_int_list(failed_pages),
            )
            conn.commit()
            continue

        # Persist facts and update progress atomically
        for fact in facts:
            insert_fact(conn, fact)

        update_document(
            conn,
            document_id,
            skipped_pages=encode_int_list(skipped_pages),
            failed_pages=encode_int_list(failed_pages),
        )
        conn.commit()
        log.info("Page %d: inserted %d fact(s).", page_index, len(facts))

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
