# Fact Knowledge Layer: Fact Discovery, Grounding, and Cross-Document Semantic Comparison

A production-grade **Fact Knowledge Layer** that extracts structured atomic facts from arbitrary multi-page PDF documents, grounds every claim to verbatim source evidence with on-demand visual bounding-box highlights on rendered PDF pages, and performs cross-document semantic comparison (corroboration, contradiction detection, and context-driven reconciliation) with step-by-step LLM reasoning chains.

---

## 🌟 Key Capabilities

1. **Domain-Agnostic Fact Extraction**:
   - Parses arbitrary PDFs (financial disclosures, IPO prospectuses, macroeconomic surveys, corporate reports) page-by-page.
   - Extracts structured atomic tuples: `(subject, predicate, value, unit, temporal_scope, measurement_scope, evidence_text, confidence)`.
   - Handles both structured tabular balance sheets and dense narrative prose without hardcoded document schemas or regex rules.
   - Resilient multi-stage JSON salvage parser rescues valid facts from token-truncated LLM outputs.

2. **Verbatim Evidence Grounding & Visual Highlights**:
   - Every fact is grounded in its source document, 0-indexed page number, and verbatim snippet.
   - Dynamic 150 DPI PyMuPDF page renderer with on-demand vector quad bounding box search (`/documents/{id}/pages/{page}/evidence-bbox`).
   - Interactive UI overlay displays responsive, glowing bounding boxes highlighting the exact sentence on the physical PDF page.

3. **Cross-Document Semantic Comparison Engine**:
   - High-performance candidate pair generation using local `all-MiniLM-L6-v2` cosine similarity embeddings.
   - Two-stage LLM reasoning cascade classifying relationships into:
     - 🟢 **`corroborate`**: Claims agree under identical temporal and measurement scope.
     - 🔴 **`contradict`**: Conflicting numbers or statements under identical scope without explanatory context.
     - 🟡 **`context_reconciled`**: Divergent figures explained by temporal drift (e.g. FY22 vs FY24), accounting scope (Standalone vs Consolidated), or unit restatements (`reconciliation_factor`).
     - ⚪ **`unrelated`**: Orthogonal metrics or disparate entities.
   - Second-opinion adjudication: Automatically triggers a secondary verification pass on contradictions and low-confidence evaluations.

4. **Interactive D3 Knowledge Graph**:
   - Document-wise orbital clustering with dynamic SVG convex hulls.
   - 3 exploration modes: **Orbit Clusters**, **Entity Hubs**, and **Reasoning Bridges**.
   - Zero-lag, jitter-free interaction with decoupled SVG rendering and floating DOM tooltips.

---

## 🏗️ Architecture

```
                                    ┌────────────────────────┐
                                    │    PDF Ingestion       │
                                    │  (PyMuPDF Page Split)  │
                                    └───────────┬────────────┘
                                                │
                                                ▼
                                    ┌────────────────────────┐
                                    │  LLM Fact Extraction   │
                                    │  (Schema + JSON Repair)│
                                    └───────────┬────────────┘
                                                │
                     ┌──────────────────────────┴──────────────────────────┐
                     ▼                                                     ▼
        ┌─────────────────────────┐                           ┌─────────────────────────┐
        │   SQLite Persistence    │                           │ Local Sentence Embeddings│
        │ (Docs, Facts, Relns)    │                           │   (all-MiniLM-L6-v2)    │
        └────────────┬────────────┘                           └────────────┬────────────┘
                     │                                                     │
                     │                     ┌───────────────────────────────┘
                     │                     ▼
                     │        ┌─────────────────────────┐
                     │        │ Candidate Pair Cosine   │
                     │        │ Similarity Filtering    │
                     │        └────────────┬────────────┘
                     │                     │
                     │                     ▼
                     │        ┌─────────────────────────┐
                     │        │ Reasoner & Verifier LLM │
                     │        │ (Pairwise Adjudication) │
                     │        └────────────┬────────────┘
                     │                     │
                     ▼                     ▼
        ┌──────────────────────────────────────────────────────────────┐
        │                       FastAPI Backend                        │
        │  • /documents  • /compare  • /relationships  • /graph  • /bbox│
        └──────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────────────┐
        │              React 19 + D3 Interactive Frontend              │
        │  • PDF Grounding Viewer  • Diff Comparator  • Knowledge Graph│
        └──────────────────────────────────────────────────────────────┘
```

---

## 🔍 Concrete Case Studies & Examples

The system was evaluated against real-world complex corporate filings (Delhivery IPO Prospectus 2022 vs FY24 Annual Report vs Q4 2024 Presentation):

### 1. Corroborated Fact (`corroborate`)
*When independent documents report identical metrics under identical scope.*

- **Fact A** (*01-delhivery-prospectus-2022-excerpt.pdf*, Page 14):
  - **Subject**: `Delhivery Limited`
  - **Predicate**: `Active Customer Base`
  - **Value**: `21,342` | **Unit**: `Customers` | **Temporal Scope**: `FY21`
  - **Evidence**: *"As of March 31, 2021, we served 21,342 active customers across India."*
- **Fact B** (*02-delhivery-annual-report-fy24-excerpt.pdf*, Page 8):
  - **Subject**: `Delhivery`
  - **Predicate**: `Historical Active Customers`
  - **Value**: `21,342` | **Unit**: `Customers` | **Temporal Scope**: `FY21`
  - **Evidence**: *"Our active client base expanded from 21,342 in Fiscal 2021 to over 30,000 in Fiscal 2024."*
- **Relationship Verdict**: `corroborate` (Confidence: `0.98`)
- **Reasoning Explanation**: Both documents state an identical active customer count of 21,342 for the fiscal year ending March 31, 2021.

---

### 2. Genuine Contradiction (`contradict`)
*When documents present conflicting numbers for the same metric without reconcilable factors.*

- **Fact A** (*Draft Filing Version*, Page 45):
  - **Subject**: `Delhivery`
  - **Predicate**: `FY22 Express Parcel Volume`
  - **Value**: `580` | **Unit**: `Million Shipments` | **Temporal Scope**: `FY22`
  - **Evidence**: *"The company handled 580 million express parcel shipments in FY22."*
- **Fact B** (*02-delhivery-annual-report-fy24-excerpt.pdf*, Page 32):
  - **Subject**: `Delhivery`
  - **Predicate**: `FY22 Express Parcel Volume`
  - **Value**: `573` | **Unit**: `Million Shipments` | **Temporal Scope**: `FY22`
  - **Evidence**: *"Express parcel volumes stood at 573 million parcels in FY22 compared to 701 million in FY24."*
- **Relationship Verdict**: `contradict` (Confidence: `0.94`, `needs_review=1`)
- **Reasoning Explanation**: Both claims refer to total express parcel shipments for the identical FY22 fiscal period, but state divergent counts (580M vs 573M) with no adjustment noted for discontinued operations.

---

### 3. Context-Reconciled Fact (`context_reconciled`)
*When divergent numbers appear contradictory on surface, but are resolved by temporal drift, accounting perimeter, or units.*

- **Fact A** (*01-delhivery-prospectus-2022-excerpt.pdf*, Page 1):
  - **Subject**: `Delhivery Limited`
  - **Predicate**: `Revenue from Operations`
  - **Value**: `3,646.5` | **Unit**: `INR Cr` | **Temporal Scope**: `FY21`
  - **Evidence**: *"Revenue from operations was ₹36,465.3 million in Fiscal 2021."*
- **Fact B** (*02-delhivery-annual-report-fy24-excerpt.pdf*, Page 4):
  - **Subject**: `Delhivery Limited`
  - **Predicate**: `Revenue from Operations`
  - **Value**: `8,141.7` | **Unit**: `INR Cr` | **Temporal Scope**: `FY24`
  - **Evidence**: *"Revenue from operations reached ₹81,417 million in FY24, an increase of 12.7% YoY."*
- **Relationship Verdict**: `context_reconciled` (Confidence: `0.96`)
- **Reconciliation Factor**: `temporal_scope` (FY21 vs FY24)
- **Reasoning Explanation**: The revenue discrepancy (₹3,646.5 Cr vs ₹8,141.7 Cr) reflects 3 years of multi-year top-line organic growth rather than conflicting accounting representations.

---

### 4. Extraction Boundary & Limitation Case
*How the system handles ambiguous narrative statements or graphical non-text artifacts.*

- **Input Passage** (*Page containing an infographic map with no text table*):
  - **Visual Content**: A map graphic with icons indicating fulfillment centers.
  - **Extractor Output**: Emits structured facts only for textual legends containing clear quantifier bindings (e.g., *"Covering 18,700+ PIN codes"*).
  - **Graceful Degradation**: Pure vector icons without OCR-extractable glyphs are skipped rather than hallucinated with guessed numbers.

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.11+ with `uv` package manager
- Node.js 18+ and `npm`
- (Optional) Local OpenAI-compatible LLM server (e.g. `llama.cpp` / `vLLM` running Qwen2.5) or cloud API key (Groq / OpenRouter)

> [!NOTE]
> **LLM Runtime & Performance Trade-offs: Local vs. Cloud Models**
> - **Local Models (e.g., Qwen2.5-3B via `llama.cpp` / Ollama at `127.0.0.1:8080`)**:
>   - *Advantages*: 100% data privacy, zero API costs, no external rate limits, and full offline operation.
>   - *Trade-offs*: Slower inference throughput on local consumer hardware (longer processing times for dense multi-page PDFs and batch cross-document comparisons), with smaller parameter models having narrower context/reasoning capacity.
> - **Cloud Models (e.g., Groq, OpenRouter, OpenAI)**:
>   - *Advantages*: Sub-second token generation speeds (substantially faster), higher extraction accuracy, and stronger multi-hop reasoning over subtle numerical reconciliations.
>   - *Trade-offs*: Bound by external API rate limits (requests/tokens per minute on free tiers) and requires active internet access.
> - **Cascade & Offline Resilience**: The system uses a multi-provider fallback cascade. If local models are unavailable or stopped, it cascades to cloud providers; if all LLM endpoints are unreachable, it gracefully activates the deterministic **Offline Structural Synthesizer** so ingestion, visual grounding, and trajectory navigation never crash.


### 2. Backend Setup

```bash
cd fact-extraction-service

# Copy and configure environment variables
cp .env.example .env

# Run FastAPI server
uv run uvicorn src.main:app --reload --port 8000
```

### 3. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

---

## 📥 Ingesting PDFs

### Batch CLI Ingestion
You can ingest folders of starter PDFs directly using the durable ingestion script:

```bash
# Ingest all starter PDFs in data/
uv run python scripts/ingest_all.py

# Ingest specific dataset directory
uv run python scripts/ingest_all.py data/starter_pdfs
```

### Web UI Ingestion
1. Navigate to the **Upload Document** tab in the UI.
2. Drag and drop any PDF file.
3. The server renders page previews and extracts atomic facts page-by-page.

---

## 🛠️ API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/documents` | Upload a PDF and synchronously extract grounded facts. |
| `GET` | `/documents/{id}` | Get document metadata, page counts, and processing status. |
| `GET` | `/documents/{id}/facts` | Retrieve all grounded facts extracted from a document. |
| `GET` | `/documents/{id}/pages/{page}/image` | Render a 150 DPI JPEG of any document page. |
| `GET` | `/documents/{id}/pages/{page}/evidence-bbox` | On-demand vector quad search returning exact highlight coordinates. |
| `POST` | `/compare` | Trigger cross-document semantic comparison & reasoning cascade. |
| `GET` | `/relationships` | List all discovered fact relationships with filters. |
| `GET` | `/graph` | D3-optimized payload containing document clusters, nodes, and reasoning links. |

---

## 🧪 Testing & Verification

Run the complete backend test suite (67 unit & integration tests):

```bash
uv run pytest tests/ -v
```

Verify frontend production build:

```bash
cd frontend
npm run build
```

---

## 📑 Detailed Engineering Challenges

For an in-depth breakdown of technical hurdles encountered (including small LLM prose extraction omissions, token truncation salvage, and D3 physics rendering optimizations), read [CHALLENGES.md](file:///mnt/Storage/superjoin/fact-extraction-service/CHALLENGES.md).
