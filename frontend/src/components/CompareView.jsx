import React, { useState, useEffect } from "react";
import { runCompare, fetchRelationships } from "../api";
import PageViewer from "./PageViewer";

export default function CompareView() {
  const [relationships, setRelationships] = useState([]);
  const [loading, setLoading] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [stats, setStats] = useState(null);
  const [activeTab, setActiveTab] = useState("all");
  const [onlyNeedsReview, setOnlyNeedsReview] = useState(false);
  const [previewFact, setPreviewFact] = useState(null); // { docId, pageIndex, evidenceText, filename, title }

  const loadData = (typeFilter = activeTab) => {
    setLoading(true);
    fetchRelationships(typeFilter)
      .then((data) => {
        setRelationships(data.relationships || []);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to load relationships:", err);
        setLoading(false);
      });
  };

  useEffect(() => {
    loadData(activeTab);
  }, [activeTab]);

  const handleTriggerCompare = async () => {
    setComparing(true);
    try {
      const resp = await runCompare();
      setStats(resp);
      loadData(activeTab);
    } catch (err) {
      alert(`Comparison failed: ${err.message}`);
    } finally {
      setComparing(false);
    }
  };

  const filtered = relationships.filter((rel) => {
    if (onlyNeedsReview && !rel.needs_review) return false;
    return true;
  });

  const getBadgeClass = (type) => {
    switch (type) {
      case "corroborate": return "badge-corroborate";
      case "contradict": return "badge-contradict";
      case "context_reconciled": return "badge-reconciled";
      default: return "badge-unrelated";
    }
  };

  const getTypeIcon = (type) => {
    switch (type) {
      case "corroborate": return "🟢 Corroborate";
      case "contradict": return "🔴 Contradict";
      case "context_reconciled": return "🔵 Context Reconciled";
      default: return "⚪ Unrelated";
    }
  };

  return (
    <div className="compare-view">
      <div className="view-header">
        <div>
          <h2>Cross-Document Fact Comparison</h2>
          <p className="subtitle">Pairwise semantic reasoning, reconciliation, and contradiction detection</p>
        </div>
        <div className="header-actions">
          <button
            className="btn btn-primary"
            onClick={handleTriggerCompare}
            disabled={comparing}
          >
            {comparing ? "Comparing Facts across Docs..." : "🚀 Run POST /compare"}
          </button>
        </div>
      </div>

      {stats && (
        <div className="compare-stats-banner card">
          <div className="stats-grid">
            <div className="stat-box">
              <span className="stat-label">Candidates Evaluated</span>
              <span className="stat-val">{stats.candidates_evaluated}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Corroborate</span>
              <span className="stat-val text-green">{stats.corroborate}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Context Reconciled</span>
              <span className="stat-val text-blue">{stats.context_reconciled}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Contradict</span>
              <span className="stat-val text-red">{stats.contradict}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Flagged for Review</span>
              <span className="stat-val text-orange">{stats.needs_review}</span>
            </div>
          </div>
        </div>
      )}

      {/* Filter Tabs */}
      <div className="relationships-filter-bar">
        <div className="type-tabs">
          {[
            { id: "all", label: "All Relationships" },
            { id: "corroborate", label: "🟢 Corroborate" },
            { id: "context_reconciled", label: "🔵 Context Reconciled" },
            { id: "contradict", label: "🔴 Contradict" },
            { id: "unrelated", label: "⚪ Unrelated" },
          ].map((tab) => (
            <button
              key={tab.id}
              className={`filter-tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={onlyNeedsReview}
            onChange={(e) => setOnlyNeedsReview(e.target.checked)}
          />
          <span>⚠️ Only show Needs Review</span>
        </label>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading relationship pairs...</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state card">
          <span className="empty-icon">⚖️</span>
          <h3>No relationships found</h3>
          <p>
            {relationships.length === 0
              ? "Run comparison across your ingested documents to detect corroborate, contradict, and reconciled relationships."
              : "No relationships match the selected filter."}
          </p>
          {relationships.length === 0 && (
            <button className="btn btn-primary" onClick={handleTriggerCompare} disabled={comparing}>
              Run /compare Pipeline
            </button>
          )}
        </div>
      ) : (
        <div className="relationship-cards-grid">
          {filtered.map((rel) => (
            <div
              key={rel.id}
              className={`relationship-card card ${rel.needs_review ? "needs-review-border" : ""}`}
            >
              <div className="relationship-card-header">
                <div className="rel-type-cluster">
                  <span className={`badge ${getBadgeClass(rel.relationship_type)}`}>
                    {getTypeIcon(rel.relationship_type)}
                  </span>
                  {rel.reconciliation_factor && (
                    <span className="badge badge-factor">
                      Factor: <strong>{rel.reconciliation_factor}</strong>
                    </span>
                  )}
                  {rel.needs_review === 1 && (
                    <span className="badge badge-review">
                      ⚠️ Needs Review (Flagged)
                    </span>
                  )}
                </div>

                <div className="rel-scores">
                  <span className="score-pill">
                    Similarity: {Math.round(rel.similarity_score * 100)}%
                  </span>
                  <span className="score-pill">
                    Confidence: {Math.round(rel.confidence * 100)}%
                  </span>
                </div>
              </div>

              {/* Explanation */}
              <div className="explanation-box">
                <strong>Reasoning:</strong> {rel.explanation}
                {rel.second_opinion_verdict && (
                  <div className="second-opinion-note">
                    <em>Second Opinion:</em> {rel.second_opinion_verdict} ({rel.second_opinion_model})
                  </div>
                )}
              </div>

              {/* Side-by-Side Comparison */}
              <div className="side-by-side-facts">
                {/* Fact A */}
                <div className="fact-side-card">
                  <div className="fact-side-header">
                    <span className="fact-side-tag">Fact A</span>
                    <span className="doc-pill" title={rel.fact_a.document_filename}>
                      📄 {rel.fact_a.document_filename} (p. {rel.fact_a.pdf_page_index + 1})
                    </span>
                  </div>

                  <div className="fact-tuple">
                    <div className="tuple-row">
                      <span className="t-label">Subject:</span>
                      <span className="t-val font-semibold">{rel.fact_a.subject}</span>
                    </div>
                    <div className="tuple-row">
                      <span className="t-label">Predicate:</span>
                      <span className="t-val">{rel.fact_a.predicate}</span>
                    </div>
                    <div className="tuple-row">
                      <span className="t-label">Value:</span>
                      <span className="t-val highlight">
                        {rel.fact_a.value} {rel.fact_a.unit || ""}
                      </span>
                    </div>
                    {rel.fact_a.time_scope && (
                      <div className="tuple-row">
                        <span className="t-label">Time:</span>
                        <span className="t-val">{rel.fact_a.time_scope}</span>
                      </div>
                    )}
                  </div>

                  <div className="evidence-quote">
                    "{rel.fact_a.evidence_text}"
                  </div>

                  <button
                    className="btn-sm btn-outline view-evidence-btn"
                    onClick={() =>
                      setPreviewFact({
                        docId: rel.fact_a.document_id,
                        pageIndex: rel.fact_a.pdf_page_index,
                        evidenceText: rel.fact_a.evidence_text,
                        filename: rel.fact_a.document_filename,
                        title: `Fact A: ${rel.fact_a.subject} (${rel.fact_a.document_filename})`,
                      })
                    }
                  >
                    🔍 View Highlight on Source Page
                  </button>
                </div>

                {/* Fact B */}
                <div className="fact-side-card">
                  <div className="fact-side-header">
                    <span className="fact-side-tag">Fact B</span>
                    <span className="doc-pill" title={rel.fact_b.document_filename}>
                      📄 {rel.fact_b.document_filename} (p. {rel.fact_b.pdf_page_index + 1})
                    </span>
                  </div>

                  <div className="fact-tuple">
                    <div className="tuple-row">
                      <span className="t-label">Subject:</span>
                      <span className="t-val font-semibold">{rel.fact_b.subject}</span>
                    </div>
                    <div className="tuple-row">
                      <span className="t-label">Predicate:</span>
                      <span className="t-val">{rel.fact_b.predicate}</span>
                    </div>
                    <div className="tuple-row">
                      <span className="t-label">Value:</span>
                      <span className="t-val highlight">
                        {rel.fact_b.value} {rel.fact_b.unit || ""}
                      </span>
                    </div>
                    {rel.fact_b.time_scope && (
                      <div className="tuple-row">
                        <span className="t-label">Time:</span>
                        <span className="t-val">{rel.fact_b.time_scope}</span>
                      </div>
                    )}
                  </div>

                  <div className="evidence-quote">
                    "{rel.fact_b.evidence_text}"
                  </div>

                  <button
                    className="btn-sm btn-outline view-evidence-btn"
                    onClick={() =>
                      setPreviewFact({
                        docId: rel.fact_b.document_id,
                        pageIndex: rel.fact_b.pdf_page_index,
                        evidenceText: rel.fact_b.evidence_text,
                        filename: rel.fact_b.document_filename,
                        title: `Fact B: ${rel.fact_b.subject} (${rel.fact_b.document_filename})`,
                      })
                    }
                  >
                    🔍 View Highlight on Source Page
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal Page Viewer for Evidence Inspection */}
      {previewFact && (
        <div className="modal-backdrop" onClick={() => setPreviewFact(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{previewFact.title}</h3>
              <button className="close-btn" onClick={() => setPreviewFact(null)}>✕</button>
            </div>
            <div className="modal-evidence-text">
              <strong>Evidence Text:</strong> "{previewFact.evidenceText}"
            </div>
            <div className="modal-viewer-body">
              <PageViewer
                docId={previewFact.docId}
                pageIndex={previewFact.pageIndex}
                evidenceText={previewFact.evidenceText}
                filename={previewFact.filename}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
