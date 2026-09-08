"""
Tests for OCR gap detection and Vision-LLM fallback (extract_facts_from_page_image)
specifically validating behavior on the 4 dataset pages identified with OCR gaps:
- 03-delhivery-q4-fy24-earnings-presentation.pdf: Pages 1, 3, 26 (0-indexed)
- 03-imf-india-2025-article-iv-excerpt.pdf: Page 0 (0-indexed)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import fitz
import pytest

from src.db import get_connection, get_document, get_facts, init_db
from src.extraction import extract_facts_from_page_image
from src.ingestion import _process_single_page, pdf_inspector, process_pdf

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DELHIVERY_PRESENTATION_PATH = (
    REPO_ROOT / "starter-datasets" / "delhivery" / "03-delhivery-q4-fy24-earnings-presentation.pdf"
)
IMF_ARTICLE_IV_PATH = (
    REPO_ROOT / "starter-datasets" / "india-macroeconomy" / "03-imf-india-2025-article-iv-excerpt.pdf"
)


class TestOCRPageDetection:
    """Verify pdf_inspector accurately flags the exact 4 pages needing OCR."""

    def test_delhivery_presentation_ocr_pages(self) -> None:
        if not DELHIVERY_PRESENTATION_PATH.exists():
            pytest.skip(f"Dataset file not found: {DELHIVERY_PRESENTATION_PATH}")

        result = pdf_inspector.extract_pages_markdown(str(DELHIVERY_PRESENTATION_PATH))
        ocr_pages = [p.page for p in result.pages if p.needs_ocr]
        assert ocr_pages == [1, 3, 26], f"Expected pages [1, 3, 26] needing OCR, got {ocr_pages}"

    def test_imf_article_iv_ocr_pages(self) -> None:
        if not IMF_ARTICLE_IV_PATH.exists():
            pytest.skip(f"Dataset file not found: {IMF_ARTICLE_IV_PATH}")

        result = pdf_inspector.extract_pages_markdown(str(IMF_ARTICLE_IV_PATH))
        ocr_pages = [p.page for p in result.pages if p.needs_ocr]
        assert ocr_pages == [0], f"Expected page [0] needing OCR, got {ocr_pages}"


class TestVisionFallbackOnFlaggedPages:
    """Verify extract_facts_from_page_image and _process_single_page on the 4 flagged pages."""

    @pytest.fixture()
    def mock_vision_llm(self) -> MagicMock:
        client = MagicMock()
        client.model = "qwen2.5-vl:7b"
        client.last_model_used = "qwen2.5-vl:7b"
        return client

    @pytest.mark.parametrize(
        ("pdf_path", "page_index", "sample_subject", "sample_predicate", "sample_value"),
        [
            (DELHIVERY_PRESENTATION_PATH, 1, "Delhivery Network", "serviceable_pincodes", "18700+"),
            (DELHIVERY_PRESENTATION_PATH, 3, "Express Parcel", "volume_growth", "12%"),
            (DELHIVERY_PRESENTATION_PATH, 26, "Financial Summary", "q4_revenue", "2076 Cr"),
            (IMF_ARTICLE_IV_PATH, 0, "IMF Report", "document_title", "India Article IV Consultation"),
        ],
    )
    def test_extract_facts_from_flagged_page_images(
        self,
        pdf_path: Path,
        page_index: int,
        sample_subject: str,
        sample_predicate: str,
        sample_value: str,
        mock_vision_llm: MagicMock,
    ) -> None:
        if not pdf_path.exists():
            pytest.skip(f"Dataset file not found: {pdf_path}")

        # Render exact page image
        with fitz.open(str(pdf_path)) as doc:
            pix = doc[page_index].get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")

        assert len(img_bytes) > 1000, "Rendered image bytes should be valid non-empty PNG"

        # Mock response from vision LLM
        mock_vision_llm.chat.return_value = json.dumps([
            {
                "subject": sample_subject,
                "predicate": sample_predicate,
                "value": sample_value,
                "value_type": "text",
                "unit": None,
                "time_scope": "FY24",
                "qualifier": "graphic slide extraction",
                "confidence": 0.96,
                "evidence_text": f"{sample_subject} - {sample_predicate}: {sample_value}",
            }
        ])

        # Test extraction function
        facts = extract_facts_from_page_image(
            page_image_bytes=img_bytes,
            page_index=page_index,
            document_id="doc-vision-test",
            llm_client=mock_vision_llm,
        )

        assert len(facts) == 1
        f = facts[0]
        assert f["subject"] == sample_subject
        assert f["predicate"] == sample_predicate
        assert f["value"] == sample_value
        assert f["pdf_page_index"] == page_index
        assert f["extraction_method"] == "vision:qwen2.5-vl:7b"
        assert f["document_id"] == "doc-vision-test"

        # Verify multimodal chat payload structure
        chat_args, _ = mock_vision_llm.chat.call_args
        messages = chat_args[0]
        user_message = next(m for m in messages if m["role"] == "user")
        assert isinstance(user_message["content"], list)
        img_part = next(part for part in user_message["content"] if part.get("type") == "image_url")
        assert img_part["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.parametrize(
        ("pdf_path", "page_index"),
        [
            (DELHIVERY_PRESENTATION_PATH, 1),
            (DELHIVERY_PRESENTATION_PATH, 3),
            (DELHIVERY_PRESENTATION_PATH, 26),
            (IMF_ARTICLE_IV_PATH, 0),
        ],
    )
    def test_process_single_page_vision_integration(
        self,
        pdf_path: Path,
        page_index: int,
        mock_vision_llm: MagicMock,
    ) -> None:
        if not pdf_path.exists():
            pytest.skip(f"Dataset file not found: {pdf_path}")

        # Create mock PageMarkdown object as returned by pdf_inspector
        page_mock = MagicMock()
        page_mock.page = page_index
        page_mock.markdown = ""
        page_mock.needs_ocr = True

        mock_vision_llm.chat.return_value = json.dumps([
            {
                "subject": f"Entity Page {page_index}",
                "predicate": "metric",
                "value": "100",
                "value_type": "numeric",
                "unit": "units",
                "time_scope": "FY24",
                "qualifier": None,
                "confidence": 0.95,
                "evidence_text": f"Entity Page {page_index} metric 100 units",
            }
        ])

        p_idx, facts, err, was_skipped = _process_single_page(
            page=page_mock,
            path_str=str(pdf_path),
            document_id="doc-test-vision",
            llm_client=mock_vision_llm,
        )

        assert p_idx == page_index
        assert was_skipped is False
        assert err is None
        assert len(facts) == 1
        assert facts[0]["extraction_method"] == "vision:qwen2.5-vl:7b"
        assert facts[0]["pdf_page_index"] == page_index

    def test_process_single_page_vision_fallback_on_failure(self, mock_vision_llm: MagicMock) -> None:
        """When vision LLM fails or is unavailable, page gracefully marks was_skipped=True."""
        page_mock = MagicMock()
        page_mock.page = 1
        page_mock.markdown = ""
        page_mock.needs_ocr = True

        mock_vision_llm.chat.side_effect = RuntimeError("Vision LLM service unavailable")

        p_idx, facts, err, was_skipped = _process_single_page(
            page=page_mock,
            path_str=str(DELHIVERY_PRESENTATION_PATH),
            document_id="doc-test-vision-fail",
            llm_client=mock_vision_llm,
        )

        assert p_idx == 1
        assert was_skipped is True
        assert facts == []
        assert err is None


class TestFullIngestionWithOCRGapPages:
    """Test full document ingestion with DB persistence for documents containing OCR gaps."""

    def test_full_ingestion_recovers_or_skips_cleanly(self, tmp_path: Path) -> None:
        if not DELHIVERY_PRESENTATION_PATH.exists():
            pytest.skip(f"Dataset file not found: {DELHIVERY_PRESENTATION_PATH}")

        db_path = tmp_path / "test_ocr.db"
        init_db(db_path)
        conn = get_connection(db_path)

        doc_id = "doc-delhivery-q4"
        conn.execute(
            """
            INSERT INTO documents
                (id, filename, storage_path, upload_time, status,
                 skipped_pages, failed_pages)
            VALUES
                (?, 'delhivery-q4.pdf', ?, '2026-01-01T00:00:00+00:00', 'pending', '[]', '[]')
            """,
            (doc_id, str(DELHIVERY_PRESENTATION_PATH)),
        )
        conn.commit()

        # Mock LLM that returns facts for all pages (both text and vision)
        mock_llm = MagicMock()
        mock_llm.model = "qwen2.5:7b"
        mock_llm.last_model_used = "qwen2.5:7b"
        mock_llm.chat.return_value = json.dumps([
            {
                "subject": "Delhivery Metric",
                "predicate": "value",
                "value": "1234",
                "value_type": "numeric",
                "unit": "INR",
                "time_scope": "FY24",
                "qualifier": None,
                "confidence": 0.95,
                "evidence_text": "Delhivery Metric: 1234 INR",
            }
        ])

        process_pdf(
            pdf_path=DELHIVERY_PRESENTATION_PATH,
            document_id=doc_id,
            conn=conn,
            llm_client=mock_llm,
            max_workers=1,
        )

        doc = get_document(conn, doc_id)
        assert doc["status"] == "done"

        facts = get_facts(conn, doc_id)
        assert len(facts) > 0

        # Verify facts extracted from vision pages (1, 3, 26) exist
        vision_page_facts = [f for f in facts if f["pdf_page_index"] in [1, 3, 26]]
        assert len(vision_page_facts) == 3
        for vf in vision_page_facts:
            assert vf["extraction_method"].startswith("vision:")

        conn.close()
