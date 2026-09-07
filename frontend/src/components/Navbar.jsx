import React from "react";

export default function Navbar({ currentTab, onSelectTab, totalDocs, totalFacts }) {
  return (
    <header className="navbar">
      <div className="nav-brand">
        <span className="brand-logo">📑</span>
        <div className="brand-text">
          <h1>Fact Extraction & Comparison</h1>
          <span className="brand-subtitle">Cross-Document Evidence Grounding & Reconciliation</span>
        </div>
      </div>

      <nav className="nav-links">
        <button
          className={`nav-btn ${currentTab === "documents" ? "active" : ""}`}
          onClick={() => onSelectTab("documents")}
        >
          📂 Documents {totalDocs > 0 && <span className="pill">{totalDocs}</span>}
        </button>

        <button
          className={`nav-btn ${currentTab === "upload" ? "active" : ""}`}
          onClick={() => onSelectTab("upload")}
        >
          📤 Upload PDF
        </button>

        <button
          className={`nav-btn ${currentTab === "compare" ? "active" : ""}`}
          onClick={() => onSelectTab("compare")}
        >
          🔍 Cross-Doc Comparison
        </button>
      </nav>
    </header>
  );
}
