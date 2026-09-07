import React, { useState, useEffect, useMemo, useCallback } from "react";
import { fetchDocument, fetchDocumentFacts } from "../api";
import PageViewer from "./PageViewer";
import {
  ArrowLeftIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  SearchIcon,
  LayersIcon,
  CompareIcon,
  CheckIcon,
  CopyIcon,
} from "./Icons";

function FactCard({ fact, selected, copied, onSelect, onCopy }) {
  return (
    <div
      className={`fact-card ${selected ? "selected" : ""}`}
      onClick={onSelect}
    >
      <div className="fact-card-header">
        <span className="chip">Page {fact.pdf_page_index + 1}</span>
        <div className="fact-header-right">
          <button
            className="btn btn--ghost btn--icon btn--sm"
            title="Copy fact JSON"
            onClick={(e) => {
              e.stopPropagation();
              onCopy(fact);
            }}
          >
            {copied ? (
              <CheckIcon size={13} className="c-ok" />
            ) : (
              <CopyIcon size={13} />
            )}
          </button>
          <span className="t-xs faint">
            {Math.round(fact.confidence * 100)}% conf
          </span>
        </div>
      </div>

      <div className="fact-main-tuple">
        <span className="fact-subject">{fact.subject}</span>
        <span className="fact-predicate">{fact.predicate}</span>
        <div className="fact-value-box">
          <span className="fact-value">{fact.value}</span>
          {fact.unit && <span className="fact-unit">{fact.unit}</span>}
          {fact.time_scope && (
            <span className="fact-time">({fact.time_scope})</span>
          )}
        </div>
      </div>

      <div className="fact-evidence-snippet" title={fact.evidence_text}>
        "{fact.evidence_text}"
      </div>
    </div>
  );
}

export default function DocumentDetail({
  docId,
  initialPageIndex = null,
  initialFactId = null,
  onBack,
  onCompareClick,
}) {
  const [doc, setDoc] = useState(null);
  const [facts, setFacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [currentPageIndex, setCurrentPageIndex] = useState(
    initialPageIndex !== null ? initialPageIndex : 0
  );
  const [selectedFact, setSelectedFact] = useState(null);
  const [copiedFactId, setCopiedFactId] = useState(null);
  const [viewMode, setViewMode] = useState("page");
  const [filterQuery, setFilterQuery] = useState("");
  const [onlyPagesWithFacts, setOnlyPagesWithFacts] = useState(false);

  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setError(null);

    Promise.all([fetchDocument(docId), fetchDocumentFacts(docId)])
      .then(([docData, factsData]) => {
        if (!isMounted) return;
        setDoc(docData);
        const fetchedFacts = factsData.facts || [];
        setFacts(fetchedFacts);

        if (initialFactId) {
          const match = fetchedFacts.find((f) => f.id === initialFactId);
          if (match) {
            setSelectedFact(match);
            setCurrentPageIndex(match.pdf_page_index);
          }
        } else if (fetchedFacts.length > 0) {
          if (initialPageIndex !== null) {
            const pageMatch = fetchedFacts.find(
              (f) => f.pdf_page_index === initialPageIndex
            );
            setSelectedFact(pageMatch || fetchedFacts[0]);
            setCurrentPageIndex(initialPageIndex);
          } else {
            setSelectedFact(fetchedFacts[0]);
            setCurrentPageIndex(fetchedFacts[0].pdf_page_index);
          }
        }
        setLoading(false);
      })
      .catch((err) => {
        if (!isMounted) return;
        setError(err.message);
        setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [docId, initialPageIndex, initialFactId]);

  const factsByPage = useMemo(() => {
    const map = new Map();
    facts.forEach((f) => {
      const p = f.pdf_page_index;
      if (!map.has(p)) map.set(p, []);
      map.get(p).push(f);
    });
    return map;
  }, [facts]);

  const totalPages = doc?.page_count || 1;

  const maxFactsOnAnyPage = useMemo(() => {
    let maxCount = 1;
    factsByPage.forEach((list) => {
      if (list.length > maxCount) maxCount = list.length;
    });
    return maxCount;
  }, [factsByPage]);

  const pageList = useMemo(() => {
    const list = [];
    for (let i = 0; i < totalPages; i++) {
      const count = factsByPage.get(i)?.length || 0;
      if (!onlyPagesWithFacts || count > 0) {
        list.push({ index: i, count });
      }
    }
    return list;
  }, [totalPages, factsByPage, onlyPagesWithFacts]);

  const currentPageFacts = useMemo(() => {
    const list = factsByPage.get(currentPageIndex) || [];
    if (!filterQuery) return list;
    const q = filterQuery.toLowerCase();
    return list.filter(
      (f) =>
        f.subject.toLowerCase().includes(q) ||
        f.predicate.toLowerCase().includes(q) ||
        f.value.toLowerCase().includes(q) ||
        f.evidence_text.toLowerCase().includes(q)
    );
  }, [factsByPage, currentPageIndex, filterQuery]);

  const allFilteredFacts = useMemo(() => {
    if (!filterQuery) return facts;
    const q = filterQuery.toLowerCase();
    return facts.filter(
      (f) =>
        f.subject.toLowerCase().includes(q) ||
        f.predicate.toLowerCase().includes(q) ||
        f.value.toLowerCase().includes(q) ||
        f.evidence_text.toLowerCase().includes(q)
    );
  }, [facts, filterQuery]);

  const selectPage = useCallback(
    (pageIdx) => {
      setCurrentPageIndex(pageIdx);
      const pageFacts = factsByPage.get(pageIdx) || [];
      if (pageFacts.length > 0) setSelectedFact(pageFacts[0]);
    },
    [factsByPage]
  );

  const handlePrevPage = useCallback(() => {
    if (currentPageIndex > 0) selectPage(currentPageIndex - 1);
  }, [currentPageIndex, selectPage]);

  const handleNextPage = useCallback(() => {
    if (currentPageIndex < totalPages - 1) selectPage(currentPageIndex + 1);
  }, [currentPageIndex, totalPages, selectPage]);

  // Keyboard page navigation
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (
        e.target.tagName === "INPUT" ||
        e.target.tagName === "SELECT" ||
        e.target.tagName === "TEXTAREA"
      ) {
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        handlePrevPage();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        handleNextPage();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handlePrevPage, handleNextPage]);

  const handleCopyFact = (fact) => {
    navigator.clipboard.writeText(JSON.stringify(fact, null, 2));
    setCopiedFactId(fact.id);
    setTimeout(() => setCopiedFactId(null), 2000);
  };

  if (loading) {
    return (
      <div className="state-box">
        <div className="spinner" />
        <p>Loading document facts and page grounding…</p>
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div className="alert alert--bad">
        <div>
          <h3>Error loading document</h3>
          <p>{error || "Document not found."}</p>
          <button className="btn btn--outline mt-3" onClick={onBack}>
            <ArrowLeftIcon size={14} /> Back to Documents
          </button>
        </div>
      </div>
    );
  }

  const shownFacts =
    viewMode === "page" ? currentPageFacts : allFilteredFacts;

  return (
    <div className="document-detail-view fill-view">
      <div className="view-head">
        <div className="doc-meta-bar">
          <span className="chip">
            Status <b className={`c-status-${doc.status}`}>{doc.status}</b>
          </span>
          <span className="chip">
            <b>{doc.page_count ?? "—"}</b> pages
          </span>
          <span className="chip">
            <b>{facts.length}</b> facts
          </span>
          <span className="chip">{doc.pdf_type || "text_based"}</span>
        </div>
        <div className="view-actions">
          <button className="btn btn--outline" onClick={onCompareClick}>
            <CompareIcon size={16} /> Run Cross-Doc Comparison
          </button>
        </div>
      </div>

      {/* Page navigator */}
      <div className="card card--pad page-strip">
        <div className="page-strip-header">
          <div className="page-strip-title">
            <LayersIcon size={16} />
            <span>
              Page <b>{currentPageIndex + 1}</b> of {totalPages}
            </span>
            <span className="t-xs faint">
              (← / → arrow keys flip pages)
            </span>
          </div>

          <div className="page-strip-controls">
            <label className="check t-xs">
              <input
                type="checkbox"
                checked={onlyPagesWithFacts}
                onChange={(e) => setOnlyPagesWithFacts(e.target.checked)}
              />
              Pages with facts ({factsByPage.size})
            </label>

            <div className="row gap-2">
              <button
                className="btn btn--outline btn--sm"
                onClick={handlePrevPage}
                disabled={currentPageIndex <= 0}
                title="Previous page (←)"
              >
                <ChevronLeftIcon size={14} /> Prev
              </button>
              <button
                className="btn btn--outline btn--sm"
                onClick={handleNextPage}
                disabled={currentPageIndex >= totalPages - 1}
                title="Next page (→)"
              >
                Next <ChevronRightIcon size={14} />
              </button>
            </div>
          </div>
        </div>

        <div className="page-ribbon">
          {pageList.map(({ index, count }) => {
            const isCurrent = index === currentPageIndex;
            const hasFacts = count > 0;
            const density = Math.min(
              100,
              Math.round((count / maxFactsOnAnyPage) * 100)
            );

            return (
              <button
                key={index}
                className={`page-ribbon-item ${isCurrent ? "active" : ""} ${
                  hasFacts ? "has-facts" : "empty-page"
                }`}
                onClick={() => selectPage(index)}
                title={`Page ${index + 1} — ${count} fact(s)`}
              >
                <span className="ribbon-page-num">p.{index + 1}</span>
                {hasFacts ? (
                  <>
                    <span className="ribbon-fact-pill">{count}</span>
                    <span className="ribbon-density-track">
                      <span
                        className="ribbon-density-fill"
                        style={{ width: `${Math.max(15, density)}%` }}
                      />
                    </span>
                  </>
                ) : (
                  <span className="ribbon-zero-pill">0</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Split layout: facts pane + viewer pane */}
      <div className="detail-split-layout">
        <div className="facts-pane card">
          <div className="pane-toolbar">
            <div className="field-wrap grow">
              <SearchIcon size={14} />
              <input
                type="text"
                className="field"
                placeholder="Search facts (subject, metric, value)…"
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
              />
            </div>
            <div className="seg">
              <button
                className={`seg-btn ${viewMode === "page" ? "active" : ""}`}
                onClick={() => setViewMode("page")}
              >
                Page {currentPageIndex + 1} ({currentPageFacts.length})
              </button>
              <button
                className={`seg-btn ${viewMode === "all" ? "active" : ""}`}
                onClick={() => setViewMode("all")}
              >
                All ({allFilteredFacts.length})
              </button>
            </div>
          </div>

          <div className="facts-scroll-list">
            {shownFacts.map((fact) => (
              <FactCard
                key={fact.id}
                fact={fact}
                selected={selectedFact?.id === fact.id}
                copied={copiedFactId === fact.id}
                onSelect={() => {
                  setSelectedFact(fact);
                  if (viewMode === "all") {
                    setCurrentPageIndex(fact.pdf_page_index);
                  }
                }}
                onCopy={handleCopyFact}
              />
            ))}

            {shownFacts.length === 0 && (
              <div className="state-box state-box--slim">
                <p>
                  No facts{" "}
                  {viewMode === "page"
                    ? `on page ${currentPageIndex + 1}`
                    : "match your filter"}
                  .
                </p>
                {viewMode === "page" && currentPageIndex < totalPages - 1 && (
                  <button
                    className="btn btn--outline btn--sm mt-2"
                    onClick={handleNextPage}
                  >
                    Go to next page →
                  </button>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="viewer-pane card">
          {selectedFact && (
            <div className="selected-fact-detail">
              <div className="row gap-3 flex-between mb-2">
                <h4 className="t-sm lo uppercase-label">
                  Selected Fact Grounding
                </h4>
                <button
                  className="btn btn--outline btn--sm"
                  onClick={() => handleCopyFact(selectedFact)}
                >
                  {copiedFactId === selectedFact.id ? (
                    <>
                      <CheckIcon size={12} className="c-ok" /> Copied
                    </>
                  ) : (
                    <>
                      <CopyIcon size={12} /> Copy JSON
                    </>
                  )}
                </button>
              </div>
              <div className="tuple-tags">
                <span className="chip">
                  <b>{selectedFact.subject}</b>
                </span>
                <span className="chip">{selectedFact.predicate}</span>
                <span className="chip">
                  <b>
                    {selectedFact.value} {selectedFact.unit || ""}
                  </b>
                </span>
                {selectedFact.time_scope && (
                  <span className="chip">{selectedFact.time_scope}</span>
                )}
              </div>
              <div className="evidence-quote-box">
                <span className="quote-label">Exact verbatim grounding</span>
                <p className="quote-text">"{selectedFact.evidence_text}"</p>
              </div>
            </div>
          )}

          <PageViewer
            docId={doc.id}
            pageIndex={currentPageIndex}
            evidenceText={
              selectedFact && selectedFact.pdf_page_index === currentPageIndex
                ? selectedFact.evidence_text
                : null
            }
            filename={doc.filename}
          />
        </div>
      </div>
    </div>
  );
}
