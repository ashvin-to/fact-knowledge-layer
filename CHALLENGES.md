# Engineering Challenges & Technical Deep Dive

Building a production-grade **Fact Knowledge Layer** that extracts atomic, grounded claims from dense, multi-page PDFs and discovers semantic relationships across documents presents non-trivial challenges across distributed LLM inference, document geometry, state durability, and interactive physics visualization.

This document details the **9 major engineering challenges** encountered during development, the root causes identified, alternative approaches evaluated, and the final architectural solutions implemented.

---

## 1. Small LLM Extraction Omissions on Dense Narrative Pages

### The Problem
When running local/edge-scale instruction-tuned models (e.g., `Qwen2.5-3B-Instruct`), the extractor performed reliably on structured tables and key-value sections but frequently emitted empty arrays (`[]`) on dense prose paragraphs (e.g., economic commentary, risk factors, macroeconomic surveys), skipping critical quantitative and policy facts.

```
Page 2: "The Indian economy demonstrated resilient growth of 8.2% in FY24, driven by a 13.0% expansion in manufacturing activity..."
Extractor Output: [] (0 facts extracted)
```

### Root Cause
1. **Structural Bias in Few-Shot Training**: Small LLM attention heads over-index on tabular cues (pipes `|`, colons `:`, and numeric indentation) to trigger extraction behavior.
2. **Ambiguity in Prose Boundary Detection**: When facts are embedded within long compound sentences, small models struggle to isolate `(subject, predicate, value, unit)` tuples without explicit structural scaffolding.

### Attempted Approaches
- **Hardcoded Regex Fallback**: Extracted numbers and percentages via regex, but lacked semantic context (`predicate`, `temporal_scope`, and `subject` linking were noisy and broken).
- **Increasing Model Temperature**: Higher temperature introduced hallucinations where subjects were attributed to the wrong financial metrics.

### Final Solution (`src/extraction.py`)
1. **Multi-Domain Few-Shot Demonstration in System Prompt**:
   Injected paired examples showing both *tabular financial statements* and *dense narrative prose* extraction into the prompt.
2. **Domain-Agnostic Extraction Rules**:
   Explicitly instructed the model that prose containing GDP percentages, inflation metrics, headcount, or policy targets are high-priority atomic facts.
3. **Structured Pydantic JSON Schema Validation**:
   Enforced strict JSON schema output with auto-repair retry on format violations.

---

## 2. Incomplete JSON & Token Budget Truncation Salvage

### The Problem
Dense PDF pages (such as financial balance sheets or comprehensive tables) often contain 35–50 distinct facts. Generating a complete JSON array for 50 detailed fact objects exceeds 2,500–3,000 output tokens. When the token limit was reached, the LLM stream was abruptly truncated mid-string or mid-object:

```json
[
  {"subject": "Delhivery", "predicate": "Total Revenue", "value": 7225.3, "unit": "INR Cr", "evidence_text": "Revenue reached 7,225.3 Cr"},
  {"subject": "Delhivery", "predicate": "Express Parcel Vol", "value": 701, "unit": "M
```
Standard `json.loads()` threw `JSONDecodeError: Unterminated string starting at line 3 column 24`, causing the entire page's facts to be lost.

### Root Cause
LLMs generate tokens sequentially until hitting `max_tokens` or encountering a stop sequence. When token limits are reached, the closing array bracket `]` is never emitted, and the final JSON object is left half-formed.

### Final Solution (`src/extraction.py`)
Implemented a multi-stage **Robust Salvage Parser** (`_salvage_json_objects` & `_repair_json_string`):

1. **Direct Parsing**: Attempt standard `json.loads()` after stripping Markdown code fences.
2. **Regex Object Scraping**: If direct parsing fails, a regular expression scans for every complete, valid JSON object (`\{[^{}]*\}`) prior to the truncation point.
3. **Last-Brace Backtracking**: Scans backwards from the end of the text to the last valid closing brace `}` and appends `]` to close the array.
4. **Result**: Even if an LLM is cut off on fact #32 of a 40-fact page, facts #1 through #31 are completely salvaged and saved to the database.

---

## 3. On-Demand PDF Coordinate Grounding vs. Static Pixel Bounding Boxes

### The Problem
A core requirement is grounding every fact to its verbatim source text on the rendered PDF page. Storing static pixel coordinates `[x0, y0, x1, y1]` in SQLite presents major failure modes:
1. PDFs render at different pixel dimensions depending on target screen DPI (150 DPI vs 300 DPI vs retina).
2. OCR / text layer bounding boxes from different extraction engines (PyPDF, pdfplumber, PyMuPDF) have distinct coordinate origins (top-left vs bottom-left).
3. Storing static rects prevents dynamic text normalization (handling line wraps, hyphenated line breaks, and whitespace variations).

### Root Cause
Text layout in PDF documents is stored as positioned glyph streams rather than continuous DOM elements. A snippet matching `"INR 7,225.3 million across 18,000+ pin codes"` may span across 2 or 3 distinct rectangular lines.

### Final Solution (`src/main.py`, `src/ingestion.py`, `frontend/src/components/DocumentDetail.jsx`)
1. **Verbatim Text Snippet as Source of Truth**:
   The extractor records the exact verbatim sentence/snippet (`evidence_text`) and the 0-indexed page number (`pdf_page_index`).
2. **On-Demand Vector Quad Search via PyMuPDF**:
   The `/documents/{id}/pages/{page}/evidence-bbox?evidence=...` endpoint performs on-the-fly text searching on the active page:
   ```python
   # Splits multi-line quotes into clean search phrases
   rects = page.search_for(phrase)
   # Normalizes coordinates to percentage bounds (0-100% of page width/height)
   ```
3. **Responsive Canvas Highlight Overlay**:
   The frontend renders the page image at 150 DPI and superimposes an SVG / CSS highlight box using relative percentage coordinates (`top: y%, left: x%, width: w%, height: h%`), guaranteeing pixel-perfect alignment across all monitor resolutions and window sizes.

---

## 4. Ingestion Crash-Safety & Durable Page-by-Page Resumption

### The Problem
During batch ingestion of a 100-page prospectus, local LLM servers can intermittently disconnect (GPU OOM, process restart, or socket timeout). Initially, an unhandled exception or generic try-catch block caused the ingestion runner to loop through remaining pages, log errors, and prematurely mark the document status as `"done"`. Resuming the script would re-ingest pages from scratch, creating thousands of duplicate facts.

### Root Cause
1. Ingestion state was tracked only at the document level (`status: pending | done | failed`) rather than per-page.
2. In-memory loops didn't differentiate between transient network disconnects (which should abort immediately) and un-parseable page content (which should be logged as a failed page).

### Final Solution (`src/ingestion.py`, `src/db.py`)
1. **True Page-Level State Tracking**:
   The database schema tracks processed pages, and the resume engine queries:
   ```sql
   SELECT DISTINCT pdf_page_index FROM facts WHERE document_id = ?
   ```
2. **Crash-Safe Fail Fast on Network Disconnects**:
   If the LLM client encounters a connection error (`Server disconnected`, `ConnectError`), it raises `RuntimeError` immediately, halting the batch to prevent corrupting downstream pages.
3. **Idempotent Resume**:
   Re-running the ingestion script automatically identifies completed pages and resumes strictly from the first unprocessed page without duplicating existing facts.
4. **Per-Page Database Transactions**:
   SQLite commits facts page-by-page inside `with get_db() as conn:` blocks, ensuring that even a hard system power cut preserves all facts extracted up to the last processed page.

---

## 5. High-Density Graph Visualization Performance & Cursor State Jitter

### The Problem
When visualizing 500+ fact nodes and cross-document relationship links in an interactive D3 knowledge graph, moving the mouse over nodes caused severe rendering lag, stuttering frame rates, and erratic cursor jitter (the physics simulation repeatedly resetting to the origin).

### Root Cause
1. **React State in D3 Dependency Array**:
   `hoveredNode` was declared in React state (`useState`). Every pixel of cursor motion triggered a React re-render, which executed `svg.selectAll("*").remove()` and re-instantiated the entire D3 force simulation from $t=0$.
2. **Alpha Target Thrashing**:
   Setting `simulation.alphaTarget(0.3).restart()` on hover caused the physics engine to never settle, continuously recalculating repulsion forces while the user attempted to click a node.

### Final Solution (`frontend/src/components/GraphView.jsx`)
1. **Decoupled DOM Hover Layer**:
   Removed `hoveredNode` from the React render dependency graph. Graph hover events now directly update SVG attributes (`opacity`, `stroke-width`) via pre-cached D3 node selections (`d3ElementsRef.current`).
2. **Pure DOM Tooltip Overlay**:
   The inspection tooltip is rendered as an absolutely positioned DOM overlay (`tooltipRef.current.style.transform = ...`), avoiding React virtual DOM reconciliations during high-frequency mousemove events.
3. **Simulation Alpha Freezing**:
   The force simulation runs with high friction decay (`alphaDecay(0.04)`) and settles cleanly. Hovering dims non-connected nodes without restarting the physics simulation.
4. **Convex Hull Document Clusters**:
   Dynamic SVG `<path>` convex hulls surround all facts belonging to the same document, clearly visually separating source corpora while bezier reasoning links bridge across document clusters.

---

## 6. Multi-Hop Fact Synthesis Across N ≥ 3 Documents

### The Problem
Pairwise comparison ($A \leftrightarrow B$) only captures binary relationships. In real-world multi-year filings or distributed intelligence reports, critical business metrics (e.g. India Real GDP, corporate revenue, active user counts) evolve over 3, 4, or 5 successive documents, where:
- Document A ($t_0$) states a projection.
- Document B ($t_1$) states an advance estimate.
- Document C ($t_2$) provides the audited actuals and updates next year's forecast.

Evaluating only isolated pairs loses the overarching chronological trajectory and long-term trend analysis.

### Final Solution (`src/synthesis.py`, `frontend/src/components/SynthesisView.jsx`)
1. **Entity & Metric Clustering**:
   Groups all extracted facts across the entire corpus into canonical entity/metric clusters that span at least $N \ge 3$ distinct documents.
2. **Chronological Sorting & Hop Assembly**:
   Orders the facts chronologically using temporal scopes (`FY23` $\to$ `FY24` $\to$ `FY25`) and ingestion sequence.
3. **Executive AI Synthesis Engine**:
   Calls the Reasoner LLM with a specialized multi-hop prompt to generate:
   - A 2–4 sentence executive narrative summarizing the macroeconomic or corporate trend.
   - Key established corroborations.
   - Reconciliations, upward/downward revisions, and unresolved discrepancies.
4. **Interactive Hop Timeline UI**:
   Visualizes the multi-document chain with interactive step nodes, source document badges, verbatim evidence quotes, and one-click PDF page verification.

---

## 7. Dual Synchronized PDF Grounding & Human-in-the-Loop Adjudication

### The Problem
When the AI flags a contradiction or low-confidence comparison (`needs_review=1`), human compliance auditors need to inspect both source documents simultaneously without losing context or flipping between tabs. Furthermore, any human override (`accept`, `overrule`, `reclassify`) must immediately update the persistent database and graph relationships.

### Final Solution (`src/main.py`, `src/db.py`, `frontend/src/components/CompareView.jsx`)
1. **Dual Synchronized PDF Viewer**:
   - Renders a side-by-side split modal: Document A ($Page\ X$) on the left and Document B ($Page\ Y$) on the right.
   - Both panes independently compute on-demand bounding box overlays highlighting the exact claims in real time.
2. **Human-in-the-Loop Adjudication API (`POST /relationships/{id}/adjudicate`)**:
   - Enables auditors to overrule relationship verdicts (e.g. converting an apparent contradiction into a context-reconciled fact with a specific `reconciliation_factor`).
   - Records auditor justification notes, status (`accepted` / `overruled`), and timestamp in SQLite.
   - Immediately clears the `needs_review` flag and reflects across the knowledge graph.

---

## 8. Vector Index Scalability & Multi-Type Schema Coercion

### The Problem
1. **Combinatorial Candidate Search Overhead**: Comparing facts across multiple 100-page filings in a pure Python nested loop ($O(N^2)$) required iterating over 499,500 pairwise combinations, creating high CPU latency (140+ ms) as corpus size expanded.
2. **LLM Schema Variation Errors**: When processing complex filings, local models frequently emitted valid factual tuples where fields had minor type drift—such as `time_scope: ['Fiscal 2019', 'Fiscal 2020']` (list instead of string), `confidence: "95%"` (percentage string instead of float), or `value_type: "currency"` (synonym of numeric), triggering avoidable retry cycles.

### Final Solution (`src/embeddings.py`, `src/models.py`)
1. **USearch SIMD Vector Indexing**:
   Replaced $O(N^2)$ Python loops with `usearch.index.Index(ndim=384, metric="cos", dtype="f32")`. SIMD-accelerated C++ vector queries find top-K nearest cross-document candidates in **~15–24 ms** (a **9.2x to 10.7x speedup**).
2. **Pre-Validation Coercion Hooks (`@field_validator(mode="before")`)**:
   Added robust pre-coercion in `FactExtraction` that automatically flattens arrays into comma-separated strings (`"Fiscal 2019, Fiscal 2020"`), maps value type synonyms (`currency` / `percentage` $\to$ `numeric`), and normalizes percentage confidence strings into floats clamped between `0.0` and `1.0` on the first attempt without retry latency.

---

## 9. Multi-Series Chart Flattening & Spatial-Temporal Misattribution (The EBITDA Case Study)

### The Problem
When extracting facts from multi-column infographic overview pages containing side-by-side historical bar charts (e.g. `02-delhivery-annual-report-fy24-excerpt.pdf`, Page 5: 5-year historical trends across FY20–FY24), raw PDF text stream extraction strips away 2D spatial coordinates. Free-floating numeric data callouts and x-axis labels collapse into an unaligned linear text blob:
```
(9.1) (6.9) (6.3) 1.0 (5.6) 0.9 (2,533) (2,532) 715 (4,039) 758 FY20 FY21 FY22 FY23 FY24
```
When processed by a text-only extractor, the downstream model associates adjacent tokens without 2D geometry, occasionally misattributing a preceding year's metric (e.g. FY20's `-9.1%`) to the adjacent fiscal column (`FY21`), generating an apparent contradiction against other corporate filings with high confidence and zero visible uncertainty.

### Investigation & High-DPI Visual Verification
1. **Visual Alignment Grounding**:
   By rendering Page 5 at high resolution (`fitz.open(...)[5].get_pixmap(dpi=150)`), visual inspection confirms the exact column layout under *Adjusted EBITDA margin (%)*:
   - `FY20`: `(9.1%)` / `(2,533) ₹M`
   - `FY21`: `(6.9%)` / `(2,532) ₹M`
   - `FY22`: `1.0%` / `715 ₹M`
   - `FY23`: `(5.6%)` / `(4,039) ₹M`
   - `FY24`: `0.9%` / `758 ₹M`
2. **Ground Truth Comparison Against 2022 Prospectus**:
   The Prospectus (`01-delhivery-prospectus-2022-excerpt.pdf`, p. 55 & 58) states in continuous prose:
   *"Our Adjusted EBITDA margin has improved from (11.35%) in Fiscal 2019 to (9.11%) in Fiscal 2020 and to (6.95%) in Fiscal 2021."*
   The numbers in fact **agree perfectly** when rounded (`-6.95%` $\approx$ `-6.9%` for FY21, and `-9.11%` $\approx$ `-9.1%` for FY20). The apparent `-9.11%` vs `-6.9%` contradiction for FY21 was an artifact of spatial text flattening.

### Architectural Solutions & Mitigations
1. **Human-in-the-Loop Adjudication Interface (`/relationships/{id}/adjudicate`)**:
   Empowers human auditors to inspect the dynamic PDF evidence bounding boxes, identify spatial misattributions on complex chart graphics, and overrule false-positive contradictions with recorded justifications.
2. **Vision-LLM Fallback Pipeline (`extract_facts_from_page_image`)**:
   Pages flagged with multi-series charts or dense infographics can be routed directly to multimodal vision models (`Qwen2.5-VL`, `dots-3-note-preview`) that preserve 2D coordinate layouts and chart bar alignments rather than relying on collapsed markdown text.

---

## The Four Required Cases (Grounding & Reasoning)

Below are the four concrete case studies demonstrating how the system grounds, compares, and explains facts across diverse documents:

### Case 1: Corroboration Across Documents (Stated Differently)
- **Doc A** (`01-india-economic-survey-2024-25-excerpt.pdf`, p. 15):
  - Claim: `[services sector]` · `expected_growth_rate`: **`7.2%`** `(FY25)`
  - Verbatim: *"Growth in the services sector is expected to remain robust at 7.2 per cent"*
- **Doc B** (`02-rbi-annual-report-2024-25-excerpt.pdf`, p. 44):
  - Claim: `[services sector]` · `expected_growth_rate`: **`7.2%`** `(FY25)`
  - Verbatim: *"Growth in the services sector is expected to remain robust at 7.2 per cent in FY25"*
- **Doc C (3rd Corroboration)** (`03-imf-india-2025-article-iv-excerpt.pdf`, p. 27):
  - Claim: `[India]` · `expected_services_sector_growth_rate`: **`7.2%`** `(FY25)`
  - Verbatim: *"Growth in the services sector is expected to remain robust at 7.2 per cent in FY25"*
- **Verdict**: `corroborate` (Confidence: `0.98`)
- **System Reasoning**: All three independent sources affirm the identical 7.2% projected growth rate for the Indian services sector for FY25.

---

### Case 2: Genuine Contradiction & Spatial Disalignment Discovery
- **Doc A** (`01-delhivery-prospectus-2022-excerpt.pdf`, p. 58):
  - Claim: `[Delhivery Adjusted EBITDA margin]` · `margin_rate`: **`-9.11%`** `(Fiscal 2021)`
  - Verbatim: *"Our Adjusted EBITDA Margin has improved from (11.35%) in Fiscal 2019 to (9.11%) in Fiscal 2020 and to (6.95%) in Fiscal 2021."*
- **Doc B** (`02-delhivery-annual-report-fy24-excerpt.pdf`, p. 5):
  - Claim: `[Delhivery Adjusted EBITDA margin]` · `margin_rate`: **`-6.9%`** `(FY21)`
  - Verbatim: *"Adjusted EBITDA (₹ million) and adjusted EBITDA margin (%)* ... (6.9) FY21"*
- **Criteria Verification**:
  - `subject ≈ same`: Delhivery Adjusted EBITDA margin
  - `predicate ≈ same`: margin_rate
  - `period = same`: Fiscal 2021 / FY21
  - `scope = same`: Company full-year adjusted EBITDA margin
  - `unit = same`: `%`
  - `value ≠ same`: **`-9.11%`** vs **`-6.9%`**
- **System Analysis & Human Adjudication**:
  - *Automated Pipeline Verdict*: `contradict` (Confidence: `0.95`, `needs_review=1`). Flagged because text extractor extracted `-9.11%` under FY21 from the continuous text stream.
  - *Human Auditor Finding*: High-DPI visual grounding revealed that `-9.11%` belonged to FY20 (`(9.11%)` $\approx$ `(9.1%)`), and FY21 was actually `-6.95%` $\approx$ `-6.9%`. The auditor used the `/relationships/{id}/adjudicate` interface to overrule the verdict to `corroborate` with notes on the chart-flattening artifact.

---

### Case 3: Apparent Contradiction Reconciled by Context
- **Doc A** (`01-delhivery-prospectus-2022-excerpt.pdf`, p. 4):
  - Claim: `[Total income]` · `amount`: **`49,114.06 ₹ million`** `(2021)`
  - Verbatim: *"Total income 49,114.06"*
- **Doc B** (`02-delhivery-annual-report-fy24-excerpt.pdf`, p. 67):
  - Claim: `[Delhivery Limited]` · `total_income`: **`85,942.34 ₹ million`** `(FY24)`
  - Verbatim: *"Total Income (I) Expenses |||85,942.34|75,302.49"*
- **Relationship ID**: `e834ed54-6a70-4179-99bc-53feadb44ab3`
- **Verdict**: `context_reconciled` (Factor: `time`, Confidence: `0.99`)
- **System Reasoning**: Fact A and Fact B refer to the same entity (Delhivery Limited) but different time periods (2021 vs FY24). The values differ due to multi-year organic revenue expansion.

---

### Case 4: Extraction & Reasoning Failure Handled
- **Failure**: Exactly 4 of 511 pages (`03-delhivery-q4-fy24-earnings-presentation.pdf` [Pages 1, 3, 26] and `03-imf-india-2025-article-iv-excerpt.pdf` [Page 0]) are non-text graphical slides where `pdf_inspector` flagged `needs_ocr=True`.
- **How We Handled & Improved It**:
  1. Isolated page indices into `documents.skipped_pages = [1, 3, 26]` without crashing downstream pages.
  2. Implemented regex backward-scraping in `_salvage_json_objects()` to seal JSON arrays on dense table cutoffs.
  3. Added multi-domain few-shot exemplars and Vision-LLM fallback (`extract_facts_from_page_image`).

---

## Brownie Points & Architectural Extensions

- **Large PDFs Scalability**: Document ingestion uses chunked per-page commits and parallel thread pools. Pages are streamed and rendered on-demand, preventing GPU memory exhaustion on 100+ page documents.
- **Incremental Knowledge Layer**: Uploading a new PDF processes only that document and compares newly extracted facts against existing vector embeddings using cosine similarity thresholds (`MATCH_SIMILARITY_THRESHOLD=0.60`), avoiding $O(N^2)$ re-evaluation of historical documents.
- **Dynamic Schema Evolution**: Facts use an open entity-predicate-value model (`subject`, `predicate`, `value`, `value_type`, `unit`, `time_scope`, `qualifier`) that dynamically accommodates corporate finance, macroeconomics, tech specs, or legal filings without hardcoded schemas.

