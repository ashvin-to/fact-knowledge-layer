import React, { useState, useEffect, useMemo } from "react";
import { runCompare, fetchRelationships, adjudicateRelationship } from "../api";
import PageViewer from "./PageViewer";
import {
  CompareIcon,
  AlertTriangleIcon,
  CloseIcon,
  EyeIcon,
  DocumentIcon,
  SearchIcon,
  DownloadIcon,
  ColumnsIcon,
  ShieldIcon,
  CheckIcon,
} from "./Icons";

const TABS = [
  { id: "all", label: "All Relationships" },
  { id: "corroborate", label: "Corroborate", dot: "ok" },
  { id: "context_reconciled", label: "Context Reconciled", dot: "info" },
  { id: "contradict", label: "Contradict", dot: "bad" },
  { id: "unrelated", label: "Unrelated", dot: "neutral" },
];

function badgeClass(type) {
  switch (type) {
    case "corroborate":
      return "badge--ok";
    case "contradict":
      return "badge--bad";
    case "context_reconciled":
      return "badge--info";
    default:
      return "badge--neutral";
  }
}

function FactSide({ fact, highlightClass, onPreview }) {
  return (
    <div className="fact-side">
      <div className="side-header">
        <span className="doc-label">
          <DocumentIcon size={14} /> <span className="truncate">{fact.document_filename || fact.doc_filename}</span>
        </span>
        <span className="chip">Page {fact.pdf_page_index + 1}</span>
      </div>

      <div className="fact-tuple">
        <span className="fact-subject">{fact.subject}</span>
        <span className="fact-predicate">{fact.predicate}</span>
        <div className="fact-value-box">
          <span className={`fact-value ${highlightClass || ""}`}>
            {fact.value}
          </span>
          {fact.unit && <span className="fact-unit">{fact.unit}</span>}
          {fact.time_scope && (
            <span className="fact-time">({fact.time_scope})</span>
          )}
        </div>
      </div>

      <div className="evidence-quote-box" title={fact.evidence_text}>
        <span className="quote-label">Verbatim evidence</span>
        <p className="quote-text">"{fact.evidence_text}"</p>
      </div>

      <button className="btn btn--outline btn--sm mt-2" onClick={onPreview}>
        <EyeIcon size={14} /> View on PDF page
      </button>
    </div>
  );
}

export default function CompareView() {
  const [relationships, setRelationships] = useState([]);
  const [loading, setLoading] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [stats, setStats] = useState(null);
  const [activeTab, setActiveTab] = useState("all");
  const [onlyNeedsReview, setOnlyNeedsReview] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [previewFact, setPreviewFact] = useState(null);
  const [dualPreviewRel, setDualPreviewRel] = useState(null);
  const [adjudicatingRel, setAdjudicatingRel] = useState(null);
  const [adjudicationForm, setAdjudicationForm] = useState({
    relationship_type: "corroborate",
    status: "accepted",
    notes: "",
    reconciliation_factor: "time",
  });
  const [adjudicatingLoading, setAdjudicatingLoading] = useState(false);

  const loadData = (typeFilter) => {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const handleOpenAdjudicate = (rel) => {
    setAdjudicatingRel(rel);
    setAdjudicationForm({
      relationship_type: rel.relationship_type,
      status: rel.user_adjudication_status || "accepted",
      notes: rel.user_notes || "",
      reconciliation_factor: rel.reconciliation_factor || "time",
    });
  };

  const handleQuickAccept = async (rel) => {
    try {
      await adjudicateRelationship(rel.id, {
        relationship_type: rel.relationship_type,
        status: "accepted",
        notes: "Quick accepted by auditor",
        reconciliation_factor: rel.reconciliation_factor || null,
      });
      // Update local state
      setRelationships((prev) =>
        prev.map((r) =>
          r.id === rel.id
            ? {
                ...r,
                needs_review: 0,
                user_adjudication_status: "accepted",
                user_notes: "Quick accepted by auditor",
                user_adjudicated_at: new Date().toISOString(),
              }
            : r
        )
      );
    } catch (err) {
      alert(`Adjudication error: ${err.message}`);
    }
  };

  const handleSubmitAdjudication = async (e) => {
    e.preventDefault();
    if (!adjudicatingRel) return;
    setAdjudicatingLoading(true);
    try {
      const payload = {
        relationship_type: adjudicationForm.relationship_type,
        status: adjudicationForm.status,
        notes: adjudicationForm.notes || null,
        reconciliation_factor:
          adjudicationForm.relationship_type === "context_reconciled"
            ? adjudicationForm.reconciliation_factor
            : null,
      };
      await adjudicateRelationship(adjudicatingRel.id, payload);

      setRelationships((prev) =>
        prev.map((r) =>
          r.id === adjudicatingRel.id
            ? {
                ...r,
                relationship_type: payload.relationship_type,
                reconciliation_factor: payload.reconciliation_factor,
                needs_review: 0,
                user_adjudication_status: payload.status,
                user_notes: payload.notes,
                user_adjudicated_at: new Date().toISOString(),
              }
            : r
        )
      );
      setAdjudicatingRel(null);
    } catch (err) {
      alert(`Adjudication failed: ${err.message}`);
    } finally {
      setAdjudicatingLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return relationships.filter((rel) => {
      if (onlyNeedsReview && !rel.needs_review) return false;
      if (!q) return true;
      const fnA = rel.fact_a.document_filename || rel.fact_a.doc_filename || "";
      const fnB = rel.fact_b.document_filename || rel.fact_b.doc_filename || "";
      return (
        rel.fact_a.subject.toLowerCase().includes(q) ||
        rel.fact_a.predicate.toLowerCase().includes(q) ||
        rel.fact_a.value.toLowerCase().includes(q) ||
        rel.fact_b.value.toLowerCase().includes(q) ||
        (rel.explanation && rel.explanation.toLowerCase().includes(q)) ||
        fnA.toLowerCase().includes(q) ||
        fnB.toLowerCase().includes(q)
      );
    });
  }, [relationships, onlyNeedsReview, searchQuery]);

  const handleExportJSON = () => {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `fact_relationships_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="compare-view">
      <div className="view-head">
        <div>
          <div className="view-title">
            <CompareIcon size={20} /> Cross-Document Comparison & Audit
          </div>
          <p className="view-desc">
            Pairwise semantic reasoning, dual-source grounding, contradiction
            detection, and human-in-the-loop adjudication.
          </p>
        </div>
        <div className="view-actions">
          {filtered.length > 0 && (
            <button className="btn btn--outline" onClick={handleExportJSON}>
              <DownloadIcon size={16} /> Export JSON
            </button>
          )}
          <button
            className="btn btn--primary"
            onClick={handleTriggerCompare}
            disabled={comparing}
          >
            {comparing ? (
              <>
                <span className="spinner spinner--sm" /> Evaluating pairs…
              </>
            ) : (
              <>
                <CompareIcon size={16} /> Run Comparison
              </>
            )}
          </button>
        </div>
      </div>

      {stats && (
        <div className="card card--pad stats-banner">
          <div className="stats-grid">
            <div className="stat-box">
              <span className="stat-label">Evaluated</span>
              <span className="stat-val">{stats.candidates_evaluated}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Corroborate</span>
              <span className="stat-val c-ok">{stats.corroborate}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Reconciled</span>
              <span className="stat-val c-info">{stats.context_reconciled}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Contradict</span>
              <span className="stat-val c-bad">{stats.contradict}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Unrelated</span>
              <span className="stat-val">{stats.unrelated}</span>
            </div>
            <div className="stat-box">
              <span className="stat-label">Flagged / Review</span>
              <span className="stat-val c-warn">{stats.needs_review}</span>
            </div>
          </div>
        </div>
      )}

      <div className="card card--pad compare-toolbar">
        <div className="seg seg--wrap">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`seg-btn ${activeTab === t.id ? "active" : ""}`}
              onClick={() => setActiveTab(t.id)}
            >
              {t.dot && <span className={`dot dot--${t.dot}`} />} {t.label}
            </button>
          ))}
        </div>

        <div className="compare-toolbar-right">
          <div className="field-wrap">
            <SearchIcon size={14} />
            <input
              type="text"
              className="field"
              placeholder="Filter by metric, subject, explanation…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <label className="check">
            <input
              type="checkbox"
              checked={onlyNeedsReview}
              onChange={(e) => setOnlyNeedsReview(e.target.checked)}
            />
            <AlertTriangleIcon size={14} className="c-warn" /> Needs review only
          </label>
        </div>
      </div>

      <div className="results-count t-sm lo">
        Showing <b>{filtered.length}</b> relationships
      </div>

      {loading ? (
        <div className="state-box">
          <div className="spinner" />
          <p>Loading pairwise cross-document relationships…</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card state-box">
          <h3>No relationships found</h3>
          <p>
            No relationship records match the current filter. Run the
            comparison above to evaluate candidate pairs across documents.
          </p>
        </div>
      ) : (
        <div className="relationship-cards-grid">
          {filtered.map((rel) => {
            const fnA = rel.fact_a.document_filename || rel.fact_a.doc_filename;
            const fnB = rel.fact_b.document_filename || rel.fact_b.doc_filename;
            const isValueDifferent =
              rel.fact_a.value.trim().toLowerCase() !==
              rel.fact_b.value.trim().toLowerCase();

            return (
              <div
                key={rel.id}
                className={`card card--pad relationship-card ${
                  rel.needs_review ? "needs-review" : ""
                }`}
              >
                <div className="card-top-bar">
                  <div className="row gap-2 align-center">
                    <span className={`badge ${badgeClass(rel.relationship_type)}`}>
                      {rel.relationship_type.replace("_", " ")}
                    </span>
                    {rel.needs_review ? (
                      <span className="badge badge--warn" title="Flagged for human auditor review">
                        <AlertTriangleIcon size={12} /> Needs Review
                      </span>
                    ) : null}
                    {rel.user_adjudication_status && (
                      <span
                        className={`badge ${
                          rel.user_adjudication_status === "overruled"
                            ? "badge--bad"
                            : "badge--ok"
                        }`}
                        title={rel.user_notes || "Audited by human reviewer"}
                      >
                        <ShieldIcon size={12} /> Audited: {rel.user_adjudication_status}
                      </span>
                    )}
                  </div>
                  <div className="row gap-2 align-center">
                    <button
                      className="btn btn--outline btn--sm"
                      onClick={() => setDualPreviewRel(rel)}
                      title="Open side-by-side synchronized view of both PDF source pages"
                    >
                      <ColumnsIcon size={14} /> Compare Side-by-Side on PDF
                    </button>
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => handleOpenAdjudicate(rel)}
                      title="Audit or override this relationship verdict"
                    >
                      <ShieldIcon size={14} /> Adjudicate
                    </button>
                    {rel.needs_review ? (
                      <button
                        className="btn btn--primary btn--sm"
                        onClick={() => handleQuickAccept(rel)}
                        title="Accept current reasoner verdict"
                      >
                        <CheckIcon size={14} /> Accept Verdict
                      </button>
                    ) : null}
                    <span className="t-xs faint">
                      Sim {Math.round(rel.similarity_score * 100)}% · Conf{" "}
                      {Math.round(rel.confidence * 100)}%
                    </span>
                  </div>
                </div>

                <div className="facts-comparison-row">
                  <FactSide
                    fact={rel.fact_a}
                    highlightClass={isValueDifferent ? "val-highlight-a" : ""}
                    onPreview={() =>
                      setPreviewFact({
                        docId: rel.fact_a.document_id,
                        pageIndex: rel.fact_a.pdf_page_index,
                        evidenceText: rel.fact_a.evidence_text,
                        filename: fnA,
                        title: `${rel.fact_a.subject} → ${rel.fact_a.predicate}`,
                      })
                    }
                  />

                  <div className="comparison-divider">
                    <div className="vs-badge">VS</div>
                  </div>

                  <FactSide
                    fact={rel.fact_b}
                    highlightClass={isValueDifferent ? "val-highlight-b" : ""}
                    onPreview={() =>
                      setPreviewFact({
                        docId: rel.fact_b.document_id,
                        pageIndex: rel.fact_b.pdf_page_index,
                        evidenceText: rel.fact_b.evidence_text,
                        filename: fnB,
                        title: `${rel.fact_b.subject} → ${rel.fact_b.predicate}`,
                      })
                    }
                  />
                </div>

                <div className="reasoning-box">
                  <div className="reasoning-header">
                    <strong>Reasoner Explanation</strong>
                    <span className="t-xs faint">Model: {rel.reasoner_model}</span>
                  </div>
                  <p className="explanation-text">{rel.explanation}</p>
                  {rel.reconciliation_factor && (
                    <div className="reconciliation-factor-box">
                      <span className="faint">Reconciliation factor:</span>
                      <span className="c-accent">{rel.reconciliation_factor}</span>
                    </div>
                  )}
                  {rel.user_notes && (
                    <div className="reconciliation-factor-box">
                      <span className="faint">Auditor Note:</span>
                      <span className="c-info">{rel.user_notes}</span>
                    </div>
                  )}
                </div>

                {rel.second_opinion_verdict && (
                  <div className="second-opinion-box t-sm">
                    <span className="faint">
                      Second opinion ({rel.second_opinion_model}):
                    </span>{" "}
                    <b>{rel.second_opinion_verdict}</b>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Single PDF Page Preview Modal */}
      {previewFact && (
        <div className="modal-overlay" onClick={() => setPreviewFact(null)}>
          <div className="modal-content card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h3>{previewFact.title}</h3>
                <span className="t-sm lo">
                  {previewFact.filename} — Page {previewFact.pageIndex + 1}
                </span>
              </div>
              <button className="btn btn--ghost btn--icon btn--sm" onClick={() => setPreviewFact(null)}>
                <CloseIcon size={16} />
              </button>
            </div>

            <div className="modal-body">
              <div className="evidence-quote-box mb-3">
                <span className="quote-label">Target evidence to highlight</span>
                <p className="quote-text">"{previewFact.evidenceText}"</p>
              </div>

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

      {/* Dual Synchronized PDF Viewer Modal */}
      {dualPreviewRel && (
        <div className="modal-overlay" onClick={() => setDualPreviewRel(null)}>
          <div className="modal-content card dual-pdf-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="row gap-3 align-center">
                <ColumnsIcon size={18} />
                <div>
                  <h3 className="m-0">
                    Dual Synchronized Evidence Viewer:{" "}
                    <span className={`badge ${badgeClass(dualPreviewRel.relationship_type)}`}>
                      {dualPreviewRel.relationship_type.replace("_", " ")}
                    </span>
                  </h3>
                  <span className="t-xs lo">
                    Comparing {dualPreviewRel.fact_a.document_filename || dualPreviewRel.fact_a.doc_filename} (p.
                    {dualPreviewRel.fact_a.pdf_page_index + 1}) with{" "}
                    {dualPreviewRel.fact_b.document_filename || dualPreviewRel.fact_b.doc_filename} (p.
                    {dualPreviewRel.fact_b.pdf_page_index + 1})
                  </span>
                </div>
              </div>
              <button className="btn btn--ghost btn--icon btn--sm" onClick={() => setDualPreviewRel(null)}>
                <CloseIcon size={16} />
              </button>
            </div>

            <div className="dual-pdf-grid">
              <div className="dual-pane left-pane">
                <div className="dual-pane-head">
                  <div className="truncate">
                    <strong>Document A:</strong> {dualPreviewRel.fact_a.document_filename || dualPreviewRel.fact_a.doc_filename} (p.{dualPreviewRel.fact_a.pdf_page_index + 1})
                  </div>
                  <div className="dual-fact-claim">
                    <b>{dualPreviewRel.fact_a.subject}</b> {dualPreviewRel.fact_a.predicate}: <span className="c-ok">{dualPreviewRel.fact_a.value} {dualPreviewRel.fact_a.unit || ""}</span>
                  </div>
                </div>
                <div className="dual-viewer-wrapper">
                  <PageViewer
                    docId={dualPreviewRel.fact_a.document_id}
                    pageIndex={dualPreviewRel.fact_a.pdf_page_index}
                    evidenceText={dualPreviewRel.fact_a.evidence_text}
                    filename={dualPreviewRel.fact_a.document_filename || dualPreviewRel.fact_a.doc_filename}
                  />
                </div>
              </div>

              <div className="dual-pane right-pane">
                <div className="dual-pane-head">
                  <div className="truncate">
                    <strong>Document B:</strong> {dualPreviewRel.fact_b.document_filename || dualPreviewRel.fact_b.doc_filename} (p.{dualPreviewRel.fact_b.pdf_page_index + 1})
                  </div>
                  <div className="dual-fact-claim">
                    <b>{dualPreviewRel.fact_b.subject}</b> {dualPreviewRel.fact_b.predicate}: <span className="c-info">{dualPreviewRel.fact_b.value} {dualPreviewRel.fact_b.unit || ""}</span>
                  </div>
                </div>
                <div className="dual-viewer-wrapper">
                  <PageViewer
                    docId={dualPreviewRel.fact_b.document_id}
                    pageIndex={dualPreviewRel.fact_b.pdf_page_index}
                    evidenceText={dualPreviewRel.fact_b.evidence_text}
                    filename={dualPreviewRel.fact_b.document_filename || dualPreviewRel.fact_b.doc_filename}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Human-in-the-Loop Adjudication Modal */}
      {adjudicatingRel && (
        <div className="modal-overlay" onClick={() => setAdjudicatingRel(null)}>
          <div className="modal-content card adjudication-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="row gap-2 align-center">
                <ShieldIcon size={18} />
                <h3>Adjudicate & Override Relationship</h3>
              </div>
              <button className="btn btn--ghost btn--icon btn--sm" onClick={() => setAdjudicatingRel(null)}>
                <CloseIcon size={16} />
              </button>
            </div>

            <form onSubmit={handleSubmitAdjudication} className="modal-body">
              <div className="adjudication-summary-box mb-3">
                <div className="row justify-between mb-2">
                  <span className="t-xs faint">CURRENT AI VERDICT</span>
                  <span className={`badge ${badgeClass(adjudicatingRel.relationship_type)}`}>
                    {adjudicatingRel.relationship_type}
                  </span>
                </div>
                <p className="t-sm m-0">{adjudicatingRel.explanation}</p>
              </div>

              <div className="field-group mb-3">
                <label className="field-label">Auditor Verdict</label>
                <select
                  className="field"
                  value={adjudicationForm.relationship_type}
                  onChange={(e) =>
                    setAdjudicationForm((prev) => ({
                      ...prev,
                      relationship_type: e.target.value,
                    }))
                  }
                >
                  <option value="corroborate">Corroborate (Same metric and finding)</option>
                  <option value="context_reconciled">Context Reconciled (Different due to time/scope/unit)</option>
                  <option value="contradict">Contradict (Direct conflict)</option>
                  <option value="unrelated">Unrelated (Different concepts)</option>
                </select>
              </div>

              {adjudicationForm.relationship_type === "context_reconciled" && (
                <div className="field-group mb-3">
                  <label className="field-label">Reconciliation Factor</label>
                  <select
                    className="field"
                    value={adjudicationForm.reconciliation_factor}
                    onChange={(e) =>
                      setAdjudicationForm((prev) => ({
                        ...prev,
                        reconciliation_factor: e.target.value,
                      }))
                    }
                  >
                    <option value="time">Time Scope (e.g. FY23 vs FY24, Q1 vs Annual)</option>
                    <option value="scope">Scope / Methodology (e.g. Advanced Estimate vs Actuals)</option>
                    <option value="unit">Reporting Unit (e.g. USD vs INR, Millions vs Billions)</option>
                    <option value="other">Other contextual factor</option>
                  </select>
                </div>
              )}

              <div className="field-group mb-3">
                <label className="field-label">Action Status</label>
                <div className="row gap-3">
                  <label className="radio-label">
                    <input
                      type="radio"
                      name="status"
                      value="accepted"
                      checked={adjudicationForm.status === "accepted"}
                      onChange={() =>
                        setAdjudicationForm((prev) => ({ ...prev, status: "accepted" }))
                      }
                    />{" "}
                    Accept / Confirm
                  </label>
                  <label className="radio-label">
                    <input
                      type="radio"
                      name="status"
                      value="overruled"
                      checked={adjudicationForm.status === "overruled"}
                      onChange={() =>
                        setAdjudicationForm((prev) => ({ ...prev, status: "overruled" }))
                      }
                    />{" "}
                    Overrule AI Verdict
                  </label>
                </div>
              </div>

              <div className="field-group mb-3">
                <label className="field-label">Auditor Justification & Notes</label>
                <textarea
                  className="field textarea"
                  rows={3}
                  placeholder="Explain why this relationship was verified or overruled…"
                  value={adjudicationForm.notes}
                  onChange={(e) =>
                    setAdjudicationForm((prev) => ({ ...prev, notes: e.target.value }))
                  }
                />
              </div>

              <div className="row justify-end gap-2 pt-2">
                <button
                  type="button"
                  className="btn btn--outline"
                  onClick={() => setAdjudicatingRel(null)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn--primary"
                  disabled={adjudicatingLoading}
                >
                  {adjudicatingLoading ? "Saving…" : "Save Adjudication"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

