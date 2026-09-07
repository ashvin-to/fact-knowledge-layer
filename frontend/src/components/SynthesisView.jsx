import React, { useState, useEffect } from "react";
import { fetchTrajectories } from "../api";
import PageViewer from "./PageViewer";
import {
  GitCommitIcon,
  DocumentIcon,
  EyeIcon,
  CloseIcon,
  RefreshIcon,
  CheckIcon,
  AlertTriangleIcon,
} from "./Icons";

export default function SynthesisView() {
  const [trajectories, setTrajectories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [minDocs, setMinDocs] = useState(2);
  const [previewFact, setPreviewFact] = useState(null);

  const loadData = (forceRefresh = false) => {
    setLoading(true);
    fetchTrajectories(forceRefresh)
      .then((data) => {
        setTrajectories(data.trajectories || []);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch trajectories:", err);
        setLoading(false);
      });
  };

  useEffect(() => {
    loadData(false);
  }, [minDocs]);

  const filtered = trajectories.filter((t) => t.document_count >= minDocs);

  return (
    <div className="synthesis-view">
      <div className="view-head">
        <div>
          <div className="view-title">
            <GitCommitIcon size={20} /> Multi-Hop Fact Synthesis & Trajectories
          </div>
          <p className="view-desc">
            Autonomous multi-document reasoning chains across N ≥ 3 sources,
            synthesizing chronological metric evolutions, corroboration paths,
            and revisions.
          </p>
        </div>
        <div className="view-actions">
          <div className="seg">
            <button
              className={`seg-btn ${minDocs === 2 ? "active" : ""}`}
              onClick={() => setMinDocs(2)}
            >
              N ≥ 2 Documents
            </button>
            <button
              className={`seg-btn ${minDocs === 3 ? "active" : ""}`}
              onClick={() => setMinDocs(3)}
            >
              N ≥ 3 Documents
            </button>
          </div>
          <button className="btn btn--outline" onClick={() => loadData(true)} disabled={loading}>
            <RefreshIcon size={16} /> Re-Synthesize
          </button>
        </div>
      </div>


      {loading ? (
        <div className="state-box">
          <div className="spinner" />
          <p>Synthesizing multi-hop trajectories across documents…</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card state-box">
          <h3>No multi-document trajectories discovered</h3>
          <p>
            Upload 3 or more documents discussing related entities (e.g. GDP, inflation, revenue)
            to automatically generate chronological multi-hop synthesis chains.
          </p>
        </div>
      ) : (
        <div className="trajectories-list">
          {filtered.map((traj) => (
            <div key={traj.id} className="card card--pad trajectory-card">
              <div className="traj-header">
                <div>
                  <div className="row gap-2 align-center mb-1">
                    <span className="badge badge--info">{traj.metric}</span>
                    <span className="badge badge--ok">
                      {traj.document_count} Source Documents
                    </span>
                    <span className="badge badge--neutral">
                      Conf {Math.round(traj.confidence * 100)}%
                    </span>
                  </div>
                  <h3 className="traj-title">{traj.title}</h3>
                </div>
                <span className="t-xs faint">Model: {traj.reasoner_model}</span>
              </div>

              {/* Synthesis Executive Narrative */}
              <div className="traj-narrative-box">
                <div className="narrative-label">Executive AI Synthesis Narrative</div>
                <p className="narrative-text">{traj.synthesis_narrative}</p>
              </div>

              {/* Key Findings & Discrepancies */}
              <div className="traj-insights-grid">
                {traj.key_findings && traj.key_findings.length > 0 && (
                  <div className="insight-card ok-card">
                    <div className="insight-title">
                      <CheckIcon size={14} className="c-ok" /> Key Corroborations & Findings
                    </div>
                    <ul className="insight-list">
                      {traj.key_findings.map((kf, i) => (
                        <li key={i}>{kf}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {traj.discrepancies && traj.discrepancies.length > 0 && (
                  <div className="insight-card warn-card">
                    <div className="insight-title">
                      <AlertTriangleIcon size={14} className="c-warn" /> Reconciliations & Discrepancies
                    </div>
                    <ul className="insight-list">
                      {traj.discrepancies.map((disc, i) => (
                        <li key={i}>{disc}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Chronological Hop Sequence */}
              <div className="traj-hops-container">
                <div className="hops-title">Chronological Evidence Chain ({traj.hops.length} Hops)</div>
                <div className="hops-timeline">
                  {traj.hops.map((hop, idx) => (
                    <div key={hop.fact_id} className="hop-node-wrapper">
                      <div className="hop-badge-index">{idx + 1}</div>
                      <div className="card hop-card">
                        <div className="hop-card-head">
                          <span className="doc-label">
                            <DocumentIcon size={13} />{" "}
                            <span className="truncate">{hop.document_filename}</span>
                          </span>
                          <span className="chip">Page {hop.pdf_page_index + 1}</span>
                        </div>

                        <div className="hop-body">
                          <div className="hop-claim">
                            <span className="hop-predicate">{hop.predicate}:</span>
                            <span className="hop-value c-accent">
                              {hop.value} {hop.unit || ""}
                            </span>
                          </div>

                          {hop.time_scope && (
                            <div className="hop-meta">
                              <span className="faint">Scope:</span> <b>{hop.time_scope}</b>
                              {hop.qualifier && (
                                <> · <span className="faint">Qualifier:</span> <i>{hop.qualifier}</i></>
                              )}
                            </div>
                          )}

                          <div className="hop-quote" title={hop.evidence_text}>
                            "{hop.evidence_text}"
                          </div>

                          <button
                            className="btn btn--outline btn--sm mt-2"
                            onClick={() =>
                              setPreviewFact({
                                docId: hop.document_id,
                                pageIndex: hop.pdf_page_index,
                                evidenceText: hop.evidence_text,
                                filename: hop.document_filename,
                                title: `${hop.subject} → ${hop.predicate}`,
                              })
                            }
                          >
                            <EyeIcon size={13} /> View Source PDF
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
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
    </div>
  );
}
