import React from "react";

export default function DocumentList({ documents, onSelectDoc, onUploadClick, onCompareClick, loading }) {
  const totalFacts = documents.reduce((acc, d) => acc + (d.fact_count || 0), 0);
  const doneDocs = documents.filter((d) => d.status === "done").length;

  return (
    <div className="document-list-view">
      <div className="view-header">
        <div>
          <h2>Uploaded Documents</h2>
          <p className="subtitle">All ingested documents and extracted atomic fact repositories</p>
        </div>
        <div className="header-actions">
          <button className="btn btn-outline" onClick={onCompareClick}>
            🔍 Run Comparison
          </button>
          <button className="btn btn-primary" onClick={onUploadClick}>
            + Upload New PDF
          </button>
        </div>
      </div>

      <div className="stats-row">
        <div className="stat-card">
          <span className="stat-num">{documents.length}</span>
          <span className="stat-title">Total Documents</span>
        </div>
        <div className="stat-card">
          <span className="stat-num highlight">{totalFacts}</span>
          <span className="stat-title">Total Facts Extracted</span>
        </div>
        <div className="stat-card">
          <span className="stat-num">{doneDocs} / {documents.length}</span>
          <span className="stat-title">Ready for Comparison</span>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading documents...</p>
        </div>
      ) : documents.length === 0 ? (
        <div className="empty-state card">
          <span className="empty-icon">📂</span>
          <h3>No documents ingested yet</h3>
          <p>Upload a PDF to classify, extract facts, and view evidence highlights.</p>
          <button className="btn btn-primary" onClick={onUploadClick}>Upload PDF</button>
        </div>
      ) : (
        <div className="table-card card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Document Filename</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Facts</th>
                <th>Type</th>
                <th>Uploaded</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id} className="clickable-row" onClick={() => onSelectDoc(doc.id)}>
                  <td className="font-semibold">
                    <span className="doc-icon">📄</span> {doc.filename}
                  </td>
                  <td>
                    <span className={`status-badge status-${doc.status}`}>
                      {doc.status}
                    </span>
                  </td>
                  <td>{doc.page_count ?? "—"}</td>
                  <td>
                    <span className="fact-count-pill">{doc.fact_count ?? 0} facts</span>
                  </td>
                  <td><span className="badge badge-muted">{doc.pdf_type || "text_based"}</span></td>
                  <td className="text-muted text-sm">
                    {new Date(doc.upload_time).toLocaleString()}
                  </td>
                  <td>
                    <button
                      className="btn-sm btn-outline"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectDoc(doc.id);
                      }}
                    >
                      Browse Facts →
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
