import React, { useState, useEffect } from "react";
import {
  DocumentIcon,
  UploadIcon,
  CompareIcon,
  GraphIcon,
  CommandIcon,
  ActivityIcon,
} from "./Icons";

export default function Navbar({
  currentTab,
  onSelectTab,
  totalDocs,
  onOpenCommandPalette,
}) {
  const [apiOnline, setApiOnline] = useState(true);

  useEffect(() => {
    // Ping /health
    const checkHealth = async () => {
      try {
        const res = await fetch("http://localhost:8000/health");
        setApiOnline(res.ok);
      } catch {
        setApiOnline(false);
      }
    };
    checkHealth();
    const timer = setInterval(checkHealth, 10000);
    return () => clearInterval(timer);
  }, []);

  return (
    <header className="navbar">
      <div
        className="nav-brand"
        onClick={() => onSelectTab("documents")}
        style={{ cursor: "pointer" }}
      >
        <div className="brand-logo-badge">
          <DocumentIcon size={20} className="brand-icon" />
        </div>
        <div className="brand-text">
          <h1>Fact Extraction & Comparison</h1>
          <span className="brand-subtitle">
            Cross-Document Grounding & Knowledge Graph
          </span>
        </div>
      </div>

      <nav className="nav-links">
        <button
          className={`nav-btn ${currentTab === "documents" ? "active" : ""}`}
          onClick={() => onSelectTab("documents")}
        >
          <DocumentIcon size={16} />
          <span>Documents</span>
          {totalDocs > 0 && <span className="pill">{totalDocs}</span>}
        </button>

        <button
          className={`nav-btn ${currentTab === "upload" ? "active" : ""}`}
          onClick={() => onSelectTab("upload")}
        >
          <UploadIcon size={16} />
          <span>Upload PDF</span>
        </button>

        <button
          className={`nav-btn ${currentTab === "compare" ? "active" : ""}`}
          onClick={() => onSelectTab("compare")}
        >
          <CompareIcon size={16} />
          <span>Cross-Doc Comparison</span>
        </button>

        <button
          className={`nav-btn ${currentTab === "graph" ? "active" : ""}`}
          onClick={() => onSelectTab("graph")}
        >
          <GraphIcon size={16} />
          <span>Knowledge Graph</span>
        </button>
      </nav>

      <div className="navbar-right-actions">
        <button
          className="cmd-palette-trigger-btn"
          onClick={onOpenCommandPalette}
          title="Open Command Palette (Ctrl+K or Cmd+K)"
        >
          <CommandIcon size={14} />
          <span className="text-xs font-semibold">Quick Find</span>
          <kbd className="kbd-shortcut">⌘K</kbd>
        </button>

        <div
          className={`api-status-chip ${apiOnline ? "online" : "offline"}`}
          title={apiOnline ? "FastAPI Server connected" : "Cannot reach backend on :8000"}
        >
          <span className="api-pulse-dot"></span>
          <span className="api-status-label">{apiOnline ? "API Online" : "Connecting..."}</span>
        </div>
      </div>
    </header>
  );
}
