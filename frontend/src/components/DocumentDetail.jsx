import React, { useState, useEffect } from "react";
import { fetchDocument, fetchDocumentFacts } from "../api";
import PageViewer from "./PageViewer";

export default function DocumentDetail({ docId, onBack, onCompareClick }) {
  const [doc, setDoc] = useState(null);
  const [facts, setFacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedFact, setSelectedFact] = useState(null);
  const [filterQuery, setFilterQuery] = useState("");
  const [pageFilter, setPageFilter] = useState("all");

  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    setError(null);

    Promise.all([fetchDocument(docId), fetchDocumentFacts(docId)])
      .then(([docData, factsData]) => {
        if (!isMounted) return;
        setDoc(docData);
        setFacts(factsData.facts || []);
        if (factsData.facts?.length > 0) {
          setSelectedFact(factsData.facts[0]);
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
  }, [docId]);

  const uniquePages = Array.from(new Set(facts.map((f) => f.pdf_page_index))).sort((a, b) => a - b);

  const filteredFacts = facts.filter((f) => {
    const matchesQuery =
      filterQuery === "" ||
      f.subject.toLowerCase().includes(filterQuery.toLowerCase()) ||
      f.predicate.toLowerCase().includes(filterQuery.toLowerCase()) ||
      f.value.toLowerCase().includes(filterQuery.toLowerCase()) ||
      f.evidence_text.toLowerCase().includes(filterQuery.toLowerCase());

    const matchesPage = pageFilter === "all" || f.pdf_page_index === Number(pageFilter);
    return matchesQuery && matchesPage;
  });

  if (loading) {
    return (
      <div className="loading-state">
        <div className="spinner"></div>
        <p>Loading document facts and evidence...</p>
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div className="card alert alert-error">
        <h3>Error loading document</h3>
        <p>{error || "Document not found."}</p>
        <button className="btn btn-outline" onClick={onBack}>← Back to Documents</button>
      </div>
    );
  }

  return (
    <div className="document-detail-view">
      <div className="detail-header view-header">
        <div>
          <button className="back-link" onClick={onBack}>← Back to Documents</button>
          <h2>📄 {doc.filename}</h2>
          <div className="doc-meta-bar">
            <span className="meta-item">Status: <strong className={`status-${doc.status}`}>{doc.status}</strong></span>
            <span className="meta-item">Pages: <strong>{doc.page_count ?? "—"}</strong></span>
            <span className="meta-item">Facts: <strong className="highlight">{facts.length}</strong></span>
            <span className="meta-item">Type: <strong>{doc.pdf_type || "text_based"}</strong></span>
          </div>
        </div>
        <div className="header-actions">
          <button className="btn btn-outline" onClick={onCompareClick}>
            🔍 Run Cross-Doc Comparison
          </button>
        </div>
      </div>

      <div className="detail-split-layout">
        {/* Left column: Facts List */}
        <div className="facts-pane card">
          <div className="pane-filter-bar">
            <input
              type="text"
              placeholder="Search facts (subject, metric, value)..."
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              className="search-input"
            />
            <select
              value={pageFilter}
              onChange={(e) => setPageFilter(e.target.value)}
              className="page-select"
            >
              <option value="all">All Pages ({uniquePages.length})</option>
              {uniquePages.map((p) => (
                <option key={p} value={p}>Page {p + 1}</option>
              ))}
            </select>
          </div>

          <div className="facts-list-count">
            Showing <strong>{filteredFacts.length}</strong> of {facts.length} facts
          </div>

          <div className="facts-scroll-list">
            {filteredFacts.map((fact) => {
              const isSelected = selectedFact?.id === fact.id;
              return (
                <div
                  key={fact.id}
                  className={`fact-card ${isSelected ? "selected" : ""}`}
                  onClick={() => setSelectedFact(fact)}
                >
                  <div className="fact-card-header">
                    <span className="fact-page-badge">Page {fact.pdf_page_index + 1}</span>
                    <span className="fact-confidence">
                      {Math.round(fact.confidence * 100)}% conf
                    </span>
                  </div>

                  <div className="fact-main-tuple">
                    <span className="fact-subject">{fact.subject}</span>
                    <span className="fact-predicate">→ {fact.predicate}</span>
                    <div className="fact-value-box">
                      <span className="fact-value">{fact.value}</span>
                      {fact.unit && <span className="fact-unit">{fact.unit}</span>}
                      {fact.time_scope && <span className="fact-time">({fact.time_scope})</span>}
                    </div>
                  </div>

                  <div className="fact-evidence-snippet" title={fact.evidence_text}>
                    "{fact.evidence_text}"
                  </div>
                </div>
              );
            })}

            {filteredFacts.length === 0 && (
              <div className="empty-substate">
                <p>No facts match the search filters.</p>
              </div>
            )}
          </div>
        </div>

        {/* Right column: Page Viewer + Selected Fact Inspection */}
        <div className="viewer-pane card">
          {selectedFact ? (
            <>
              <div className="selected-fact-detail">
                <div className="selected-fact-summary">
                  <h4>Selected Fact Grounding</h4>
                  <div className="tuple-tags">
                    <span className="tag-subject"><strong>Subject:</strong> {selectedFact.subject}</span>
                    <span className="tag-predicate"><strong>Predicate:</strong> {selectedFact.predicate}</span>
                    <span className="tag-value"><strong>Value:</strong> {selectedFact.value} {selectedFact.unit || ""}</span>
                    {selectedFact.time_scope && (
                      <span className="tag-time"><strong>Time Scope:</strong> {selectedFact.time_scope}</span>
                    )}
                  </div>
                </div>
                <div className="evidence-quote-box">
                  <span className="quote-label">Exact Verbatim Evidence:</span>
                  <p className="quote-text">"{selectedFact.evidence_text}"</p>
                </div>
              </div>

              <PageViewer
                docId={doc.id}
                pageIndex={selectedFact.pdf_page_index}
                evidenceText={selectedFact.evidence_text}
                filename={doc.filename}
              />
            </>
          ) : (
            <div className="empty-substate">
              <p>Select a fact on the left to view the source PDF page with highlighted evidence.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
