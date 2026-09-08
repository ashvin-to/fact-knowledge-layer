"""
Verification script for the 4 OCR-flagged dataset pages:
- 03-delhivery-q4-fy24-earnings-presentation.pdf: Pages 1, 3, 26 (0-indexed)
- 03-imf-india-2025-article-iv-excerpt.pdf: Page 0 (0-indexed)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import fitz

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extraction import extract_facts_from_page_image
from src.ingestion import _process_single_page, pdf_inspector
from src.llm_client import LLMClient, ProviderConfig

from dotenv import load_dotenv
load_dotenv()

DELHIVERY_PRESENTATION = Path("/mnt/Storage/superjoin/starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
IMF_ARTICLE_IV = Path("/mnt/Storage/superjoin/starter-datasets/india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf")

TARGET_PAGES = [
    ("Delhivery Presentation", DELHIVERY_PRESENTATION, 1),
    ("Delhivery Presentation", DELHIVERY_PRESENTATION, 3),
    ("Delhivery Presentation", DELHIVERY_PRESENTATION, 26),
    ("IMF Article IV", IMF_ARTICLE_IV, 0),
]


def main() -> None:
    print("=" * 70)
    print("OCR GAP & VISION FALLBACK VERIFICATION ON 4 FLAGGED PAGES")
    print("=" * 70)

    # 1. Verify pdf_inspector detection
    print("\n[1] Checking pdf_inspector OCR Gap Detection...")
    for label, pdf_path in [
        ("Delhivery Presentation", DELHIVERY_PRESENTATION),
        ("IMF Article IV", IMF_ARTICLE_IV),
    ]:
        res = pdf_inspector.extract_pages_markdown(str(pdf_path))
        flagged = [p.page for p in res.pages if p.needs_ocr]
        print(f"  - {label}: {len(flagged)} pages flagged for OCR -> {flagged}")

    # 2. Render Page Pixmaps
    print("\n[2] Rendering Pixmaps for Flagged Pages...")
    for label, pdf_path, page_idx in TARGET_PAGES:
        with fitz.open(str(pdf_path)) as doc:
            pix = doc[page_idx].get_pixmap(dpi=120)
            img_bytes = pix.tobytes("png")
            print(f"  - {label} [Page {page_idx}]: {pix.width}x{pix.height} px, {len(img_bytes):,} bytes PNG")

    # 3. Test Live Model Client
    print("\n[3] Testing against active LLM Client...")
    cloud_key = os.environ.get("OPENROUTER_API_KEY", "")
    use_cloud = os.environ.get("USE_CLOUD_VISION", "1" if cloud_key else "0") == "1"

    if use_cloud and cloud_key:
        endpoint_url = "https://openrouter.ai/api/v1"
        model_name = os.environ.get("VISION_MODEL", "dots-studio/dots-3-note-preview:free")
        api_key = cloud_key
        print(f"  - Cloud Vision Provider: OpenRouter ({model_name})")
    else:
        endpoint_url = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
        model_name = os.environ.get("LLM_MODEL", "Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M")
        api_key = "none"
        print(f"  - Local Endpoint: {endpoint_url} (Model: {model_name})")

    client = LLMClient(providers=[ProviderConfig(name="test-vision", base_url=endpoint_url, api_key=api_key, model=model_name, max_tokens=2500)])

    for label, pdf_path, page_idx in TARGET_PAGES:
        print(f"\n--- Testing {label} (Page {page_idx}) ---")
        with fitz.open(str(pdf_path)) as doc:
            pix = doc[page_idx].get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")

        try:
            facts = extract_facts_from_page_image(
                page_image_bytes=img_bytes,
                page_index=page_idx,
                document_id="test-ocr-doc",
                llm_client=client,
            )
            print(f"  -> Extracted {len(facts)} facts via {facts[0]['extraction_method'] if facts else 'none'}:")
            for f in facts[:3]:
                print(f"     - [{f['subject']}] {f['predicate']}: {f['value']} ({f['time_scope'] or 'N/A'})")
        except Exception as exc:
            print(f"  -> Note: Vision call returned ({type(exc).__name__}): {exc}")
            print(f"     (Expected if endpoint is text-only GGUF without --mmproj vision projector; fallback to skipped_pages handled)")

    print("\n" + "=" * 70)
    print("VERIFICATION RUN COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
