# Fact Extraction & Cross-Document Comparison Service

Ingests PDFs, extracts discrete facts page-by-page using an LLM, grounds each fact to a verbatim text snippet and source page, persists everything to SQLite, and performs cross-document fact comparison (corroboration, contradiction detection, and context-reconciliation) using local embeddings and LLM reasoning.

## Architecture

```
src/
  models.py       — Pydantic schemas (LLM outputs, DB rows, API requests & responses)
  db.py           — SQLite persistence & migrations (documents, facts, fact_relationships)
  llm_client.py   — Multi-provider OpenAI-compatible client (Groq / OpenRouter failover)
  extraction.py   — Page-level fact extraction with JSON validation & retry
  ingestion.py    — PDF classification & per-page extraction pipeline
  embeddings.py   — Local sentence-transformers embeddings & candidate generation
  comparison.py   — Pairwise LLM reasoning & second-opinion verification
  main.py         — FastAPI app and endpoints
tests/            — Full unit & integration test suite (mocked & live-compatible)
```

---

## Quick Start

### 1. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `EXTRACTOR_LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Base URL for document fact extraction |
| `EXTRACTOR_LLM_API_KEY` | `gsk_...` | API key for extraction |
| `EXTRACTOR_LLM_MODEL` | `qwen/qwen3.8-27b` | Model for extraction |
| `REASONER_LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Base URL for pairwise comparison reasoning |
| `REASONER_LLM_API_KEY` | `gsk_...` | API key for reasoning |
| `REASONER_LLM_MODEL` | `qwen/qwen3.8-27b` | Model for reasoning |
| `FALLBACK_BASE_URL` | `https://openrouter.ai/api/v1` | Fallback URL on rate-limiting (429) |
| `FALLBACK_API_KEY` | `sk-or-v1-...` | Fallback API key |
| `FALLBACK_MODEL` | `minimax/minimax-m3:free` | Fallback free model |
| `MATCH_SIMILARITY_THRESHOLD`| `0.6` | Cosine similarity cutoff for candidate fact pairs |
| `DB_PATH` | `facts.db` | SQLite database path |
| `STORAGE_DIR` | `storage` | Directory for uploaded PDFs |

### 2. Start the Server

```bash
uv run uvicorn src.main:app --reload
```

Interactive documentation is available at `http://localhost:8000/docs`.

---

## API Endpoints

### Documents & Extraction

- `POST /documents`: Upload a PDF (multipart `file`). Runs classification and page-by-page fact extraction synchronously. Returns `201` with document metadata and fact counts.
- `GET /documents/{document_id}`: Retrieve document metadata.
- `GET /documents/{document_id}/facts`: Retrieve all facts extracted from the document, grounded with `pdf_page_index` and verbatim `evidence_text`.

### Cross-Document Comparison

- `POST /compare`: Run pairwise cross-document comparison across all processed documents (or a subset via `{"document_ids": [...]}`).
  - Computes local `all-MiniLM-L6-v2` embeddings for facts (`f"{subject} — {predicate} ({unit})"`)
  - Identifies candidate pairs across documents with cosine similarity $\ge 0.6$
  - Skips previously evaluated pairs (bidirectional deduplication)
  - Evaluates each pair via the Reasoner LLM into one of:
    - `corroborate`: Identical or agreeing facts under the same scope
    - `contradict`: Conflicting claims under the same scope
    - `context_reconciled`: Different values explained by time scope, measurement scope, or units (`reconciliation_factor`)
    - `unrelated`: Distinct metrics/entities
  - Triggers a second-opinion check with the Verifier LLM on `contradict` verdicts or confidence $< 0.7$, flagging disagreements with `needs_review=1`.
  - Returns evaluation summary counts.

- `GET /relationships`: List all fact relationships with full inlined details of both facts and source document filenames. Optional filter: `?type=corroborate|contradict|context_reconciled|unrelated`.
- `GET /facts/{fact_id}/relationships`: List all relationships involving a specific fact.

---

## Running Tests

```bash
uv run pytest tests/ -v
```
