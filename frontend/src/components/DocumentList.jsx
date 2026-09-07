import React from "react";
import {
  DocumentIcon,
  CompareIcon,
  GraphIcon,
  ArrowRightIcon,
  PlusIcon,
  LayersIcon,
  CheckIcon,
  SparklesIcon,
} from "./Icons";

function StatCard({ icon, value, label, tone }) {
  return (
    <div className="card card--pad stat-card">
      <span className={`stat-icon ${tone ? `stat-icon--${tone}` : ""}`}>
        {icon}
      </span>
      <div className="stat-meta">
        <span className="stat-num">{value}</span>
        <span className="stat-title">{label}</span>
      </div>
    </div>
  );
}

export default function DocumentList({
  documents,
  onSelectDoc,
  onUploadClick,
  onCompareClick,
  onGraphClick,
  loading,
}) {
  const totalFacts = documents.reduce((acc, d) => acc + (d.fact_count || 0), 0);
  const doneDocs = documents.filter((d) => d.status === "done").length;

  return (
    <div className="document-list-view">
      <div className="view-head">
        <div>
          <div className="view-title">
            <DocumentIcon size={20} /> Documents
          </div>
          <p className="view-desc">
            All ingested documents, classified layouts, and extracted atomic
            facts with page grounding.
          </p>
        </div>
        <div className="view-actions">
          {onGraphClick && (
            <button className="btn btn--outline" onClick={onGraphClick}>
              <GraphIcon size={16} /> Knowledge Graph
            </button>
          )}
          <button className="btn btn--outline" onClick={onCompareClick}>
            <CompareIcon size={16} /> Run Comparison
          </button>
          <button className="btn btn--primary" onClick={onUploadClick}>
            <PlusIcon size={16} /> Upload PDF
          </button>
        </div>
      </div>

      <div className="stats-row">
        <StatCard
          icon={<DocumentIcon size={18} />}
          value={documents.length}
          label="Total Documents"
        />
        <StatCard
          icon={<SparklesIcon size={18} />}
          value={totalFacts.toLocaleString()}
          label="Facts Extracted"
          tone="accent"
        />
        <StatCard
          icon={<CheckIcon size={18} />}
          value={`${doneDocs} / ${documents.length}`}
          label="Ready for Comparison"
          tone="ok"
        />
      </div>

      {loading ? (
        <div className="state-box">
          <div className="spinner" />
          <p>Loading documents repository…</p>
        </div>
      ) : documents.length === 0 ? (
        <div className="card state-box">
          <div className="state-icon brand">
            <DocumentIcon size={26} />
          </div>
          <h3>No documents ingested yet</h3>
          <p>
            Upload a PDF to classify its layout, extract atomic facts, and view
            evidence highlights on the source pages.
          </p>
          <button className="btn btn--primary mt-3" onClick={onUploadClick}>
            <PlusIcon size={16} /> Upload your first PDF
          </button>
        </div>
      ) : (
        <div className="card table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Facts</th>
                <th>Classification</th>
                <th>Uploaded</th>
                <th aria-label="actions" />
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr
                  key={doc.id}
                  className="clickable-row"
                  onClick={() => onSelectDoc(doc.id)}
                >
                  <td>
                    <div className="doc-name-cell">
                      <span className="doc-thumb">
                        <DocumentIcon size={15} />
                      </span>
                      <span className="truncate doc-filename">{doc.filename}</span>
                    </div>
                  </td>
                  <td>
                    <span className={`badge badge--status-${doc.status}`}>
                      {doc.status}
                    </span>
                  </td>
                  <td className="mono">{doc.page_count ?? "—"}</td>
                  <td>
                    <span className="chip">
                      <b>{doc.fact_count ?? 0}</b> facts
                    </span>
                  </td>
                  <td>
                    <span className="badge badge--neutral">
                      {doc.pdf_type || "text_based"}
                    </span>
                  </td>
                  <td className="t-sm lo">
                    {new Date(doc.upload_time).toLocaleString()}
                  </td>
                  <td>
                    <button
                      className="btn btn--outline btn--sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectDoc(doc.id);
                      }}
                    >
                      Browse <ArrowRightIcon size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
