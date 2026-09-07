#!/usr/bin/env python3
"""
Batch ingestion and cross-document comparison script for starter datasets.

Usage:
  uv run python scripts/ingest_all.py [dataset_name]

Examples:
  uv run python scripts/ingest_all.py delhivery
  uv run python scripts/ingest_all.py india-macroeconomy
  uv run python scripts/ingest_all.py all
"""

import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.db import get_connection, init_db
from src.main import app
from starlette.testclient import TestClient

DATASETS_DIR = Path("/mnt/Storage/superjoin/starter-datasets")


def ingest_dataset(target: str = "delhivery"):
    init_db("facts.db")

    if target == "all":
        pdf_files = sorted(list(DATASETS_DIR.glob("**/*.pdf")))
    else:
        dataset_path = DATASETS_DIR / target
        if not dataset_path.exists():
            print(f"Error: Dataset directory not found: {dataset_path}")
            print(f"Available: {[d.name for d in DATASETS_DIR.iterdir() if d.is_dir()]}")
            sys.exit(1)
        pdf_files = sorted(list(dataset_path.glob("*.pdf")))

    if not pdf_files:
        print("No PDF files found to ingest.")
        sys.exit(1)

    print(f"\n=======================================================")
    print(f" 🚀 INGESTING {len(pdf_files)} PDF(S) FROM: {target.upper()}")
    print(f"=======================================================\n")

    uploaded_doc_ids = []
    conn = get_connection("facts.db")
    force_reingest = "--force" in sys.argv

    with TestClient(app) as client:
        for idx, pdf in enumerate(pdf_files, 1):
            if not force_reingest:
                existing = conn.execute(
                    "SELECT id, page_count FROM documents WHERE filename = ? AND status = 'done' ORDER BY upload_time DESC LIMIT 1",
                    (pdf.name,),
                ).fetchone()
                if existing:
                    fact_count = conn.execute(
                        "SELECT COUNT(*) FROM facts WHERE document_id = ?",
                        (existing["id"],),
                    ).fetchone()[0]
                    if fact_count > 0:
                        print(f"[{idx}/{len(pdf_files)}] ⚡ Already ingested: {pdf.name} ({fact_count} facts, {existing['page_count']} pages) — skipping upload (use --force to re-run).")
                        uploaded_doc_ids.append(existing["id"])
                        continue

            print(f"[{idx}/{len(pdf_files)}] Ingesting: {pdf.name} ({pdf.stat().st_size / 1024:.1f} KB)...")
            start = time.time()
            with open(pdf, "rb") as f:
                resp = client.post(
                    "/documents",
                    files={"file": (pdf.name, f, "application/pdf")},
                )

            dur = time.time() - start
            data = resp.json()

            if resp.status_code == 201 and data.get("status") == "done":
                doc_id = data["document_id"]
                uploaded_doc_ids.append(doc_id)
                print(f"  ✓ Extracted {data['fact_count']} facts across {data['page_count']} pages in {dur:.2f}s")
                if data["skipped_pages"]:
                    print(f"    (Skipped image/scanned pages: {data['skipped_pages']})")
            else:
                print(f"  ✗ Failed ({resp.status_code}): {data.get('error_message') or data}")

        # Run comparison across all ingested documents
        print(f"\n=======================================================")
        print(f" 🔍 RUNNING CROSS-DOCUMENT COMPARISON (/compare)")
        print(f"=======================================================\n")

        start_c = time.time()
        comp_resp = client.post("/compare")
        comp = comp_resp.json()
        dur_c = time.time() - start_c

        print(f"Comparison completed in {dur_c:.2f}s:")
        print(f"  • Candidate pairs evaluated: {comp.get('candidates_evaluated', 0)}")
        print(f"  • Corroborate:              {comp.get('corroborate', 0)}")
        print(f"  • Context Reconciled:       {comp.get('context_reconciled', 0)}")
        print(f"  • Contradict:               {comp.get('contradict', 0)}")
        print(f"  • Unrelated:                {comp.get('unrelated', 0)}")
        print(f"  • Flagged for Review:       {comp.get('needs_review', 0)}")

        # Fetch sample relationships
        rel_resp = client.get("/relationships")
        rels = rel_resp.json().get("relationships", [])
        if rels:
            print(f"\nTop Reconciled Relationships:")
            for r in rels[:5]:
                print(f"\n  [{r['relationship_type'].upper()}] Factor: {r.get('reconciliation_factor')} | Confidence: {r['confidence']}")
                print(f"    Doc A ({r['fact_a']['document_filename']}): {r['fact_a']['subject']} = {r['fact_a']['value']} {r['fact_a']['unit'] or ''} ({r['fact_a']['time_scope'] or ''})")
                print(f"    Doc B ({r['fact_b']['document_filename']}): {r['fact_b']['subject']} = {r['fact_b']['value']} {r['fact_b']['unit'] or ''} ({r['fact_b']['time_scope'] or ''})")
                print(f"    Explanation: {r['explanation']}")


if __name__ == "__main__":
    target_dataset = sys.argv[1] if len(sys.argv) > 1 else "delhivery"
    ingest_dataset(target_dataset)
