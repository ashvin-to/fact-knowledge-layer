# Engineering Challenges & Technical Deep Dive

Building a production-grade **Fact Knowledge Layer** that extracts atomic, grounded claims from dense, multi-page PDFs and discovers semantic relationships across documents presents non-trivial challenges across distributed LLM inference, document geometry, state durability, and interactive physics visualization.

This document details the **5 major engineering challenges** encountered during development, the root causes identified, alternative approaches evaluated, and the final architectural solutions implemented.

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

## The Four Required Cases (Grounding & Reasoning)

Below are the four concrete case studies demonstrating how the system grounds, compares, and explains facts across diverse documents:

### Case 1: Corroboration Across Documents (Stated Differently)
- **Doc A** (`Economic_Survey_2023_24.pdf`, p. 2):
  - Claim: `[India Real GDP]` · `growth_rate`: **`8.2%`** `(FY24)`
  - Verbatim: *"India’s real GDP grew by a robust 8.2 per cent in FY24, exceeding the 7.2 per cent growth in the previous fiscal year."*
- **Doc B** (`RBI_Annual_Report_2024.pdf`, p. 14):
  - Claim: `[Indian Economy]` · `expansion_rate`: **`8.2%`** `(2023-24)`
  - Verbatim: *"The domestic economy registered an expansion of 8.2 per cent during 2023-24 supported by sustained momentum in capital expenditure."*
- **Verdict**: `corroborate` (Confidence: `0.96`, Similarity: `0.91`)
- **System Reasoning**: Both independent sources affirm the identical 8.2% expansion for the Indian economy during FY24 (2023–24). The differences in phrasing ('real GDP grew' vs 'domestic economy registered an expansion') are semantically equivalent.

---

### Case 2: Genuine / Direct Contradiction
- **Doc A** (`Company_Q3_Investor_Deck.pdf`, p. 8):
  - Claim: `[Express Parcel Tonnage]` · `yoy_growth`: **`34%`** `(Q3 FY24)`
  - Verbatim: *"Our Express Parcel tonnage witnessed a 34% YoY growth during Q3 FY24, outperforming market peers."*
- **Doc B** (`Industry_Analyst_Report_Q3.pdf`, p. 3):
  - Claim: `[Express Parcel Tonnage]` · `yoy_growth`: **`21%`** `(Q3 FY24)`
  - Verbatim: *"Express Parcel tonnage grew by only 21% YoY in Q3 FY24 as macroeconomic headwind dampened volume growth."*
- **Verdict**: `contradict` (Confidence: `0.94`, `needs_review=1`)
- **System Reasoning**: Both documents report conflicting YoY growth rates (34% vs 21%) for the identical business segment in the identical fiscal quarter without any reconciling adjustment noted. Flagged for human auditor review.

---

### Case 3: Apparent Contradiction Reconciled by Context
- **Doc A** (`Annual_Report_FY24.pdf`, p. 28):
  - Claim: `[Total Revenue]` · `amount`: **`₹7,225.3 Cr`** `(FY24 Full Year)`
  - Verbatim: *"Revenue from operations reached ₹7,225.3 Cr in FY24, representing an increase of 31% over the previous fiscal."*
- **Doc B** (`Quarterly_Review_Q1_FY25.pdf`, p. 4):
  - Claim: `[Total Revenue]` · `amount`: **`₹2,042.8 Cr`** `(Q1 FY25 Single Quarter)`
  - Verbatim: *"Total quarterly revenue for Q1 FY25 stood at ₹2,042.8 Cr compared to ₹1,780.2 Cr in Q1 FY24."*
- **Verdict**: `context_reconciled` (Factor: `time`, Confidence: `0.93`)
- **System Reasoning**: The revenue numbers (₹7,225.3 Cr vs ₹2,042.8 Cr) appear contradictory at face value, but are reconciled by temporal duration. Document A reports cumulative annual revenue for 12 months (FY24), while Document B reports single-quarter revenue for 3 months (Q1 FY25).

---

### Case 4: Extraction & Reasoning Failure Handled
- **Failure**: Token budget truncation on 45-fact balance sheets caused `JSONDecodeError: Unterminated string`, which previously resulted in losing all facts on that page. Additionally, small models skipped prose paragraphs lacking explicit markdown table syntax.
- **How We Handled & Improved It**:
  1. Implemented regex backward-scraping in `_salvage_json_objects()` to seal the array at the last complete JSON object, salvaging 100% of generated facts prior to cutoff.
  2. Added multi-domain few-shot exemplars instructing the extractor to treat narrative growth commentary with equal priority to table rows.
  3. Added Vision-LLM fallback (`extract_facts_from_page_image`) for scanned and image-heavy pages.

---

## Brownie Points & Architectural Extensions

- **Large PDFs Scalability**: Document ingestion uses chunked per-page commits and parallel thread pools. Pages are streamed and rendered on-demand, preventing GPU memory exhaustion on 100+ page documents.
- **Incremental Knowledge Layer**: Uploading a new PDF processes only that document and compares newly extracted facts against existing vector embeddings using cosine similarity thresholds (`MATCH_SIMILARITY_THRESHOLD=0.60`), avoiding $O(N^2)$ re-evaluation of historical documents.
- **Dynamic Schema Evolution**: Facts use an open entity-predicate-value model (`subject`, `predicate`, `value`, `value_type`, `unit`, `time_scope`, `qualifier`) that dynamically accommodates corporate finance, macroeconomics, tech specs, or legal filings without hardcoded schemas.
