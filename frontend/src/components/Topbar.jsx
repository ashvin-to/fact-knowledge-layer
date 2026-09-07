import React, { useState, useEffect, useRef } from "react";
import { SearchIcon, MoonIcon, SunIcon, ArrowLeftIcon } from "./Icons";

const TITLES = {
  documents: ["Workspace", "Documents"],
  upload: ["Workspace", "Upload PDF"],
  compare: ["Analysis", "Cross-Document Comparison"],
  synthesis: ["Analysis", "Multi-Hop Synthesis"],
  graph: ["Analysis", "Knowledge Graph"],
  document_detail: ["Workspace", "Document Detail"],
};

export default function Topbar({
  currentTab,
  selectedDocId,
  documents,
  onSelectTab,
  apiOnline,
  onBack,
}) {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme || "dark"
  );

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("fex-theme", next);
    } catch {
      /* ignore */
    }
  };

  const [section, page] = TITLES[currentTab] || ["Workspace", "—"];
  const selectedDoc =
    currentTab === "document_detail" && selectedDocId
      ? documents.find((d) => d.id === selectedDocId)
      : null;

  return (
    <header className="topbar">
      <div className="topbar-context">
        {currentTab === "document_detail" && onBack && (
          <button
            className="btn btn--ghost btn--icon btn--sm"
            onClick={onBack}
            title="Back to Documents"
          >
            <ArrowLeftIcon size={16} />
          </button>
        )}
        <span className="crumbs">
          {section} <span className="crumb-sep">/</span>{" "}
          <b>{selectedDoc ? selectedDoc.filename : page}</b>
        </span>
      </div>

      <div className="topbar-actions">


        <div
          className={`api-chip ${apiOnline === false ? "offline" : "online"}`}
          title={
            apiOnline === false
              ? "Cannot reach backend on :8000"
              : "FastAPI server connected"
          }
        >
          <span className="pulse" />
          <span>{apiOnline === false ? "API Offline" : "API Online"}</span>
        </div>

        <button
          className="btn btn--ghost btn--icon btn--sm theme-toggle"
          onClick={toggleTheme}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        >
          {theme === "dark" ? <SunIcon size={16} /> : <MoonIcon size={16} />}
        </button>
      </div>
    </header>
  );
}
