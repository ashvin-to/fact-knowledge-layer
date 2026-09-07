import React, { useState, useEffect, useCallback } from "react";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import DocumentList from "./components/DocumentList";
import UploadView from "./components/UploadView";
import DocumentDetail from "./components/DocumentDetail";
import CompareView from "./components/CompareView";
import SynthesisView from "./components/SynthesisView";
import GraphView from "./components/GraphView";
import { fetchDocuments, checkApiHealth } from "./api";

export default function App() {
  const [currentTab, setCurrentTab] = useState("documents");
  const [selectedDocId, setSelectedDocId] = useState(null);
  const [selectedPageIndex, setSelectedPageIndex] = useState(null);
  const [selectedFactId, setSelectedFactId] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [apiOnline, setApiOnline] = useState(null);


  const loadDocs = useCallback(() => {
    setLoadingDocs(true);
    fetchDocuments()
      .then((data) => {
        setDocuments(data.documents || []);
        setLoadingDocs(false);
      })
      .catch((err) => {
        console.error("Failed to fetch documents:", err);
        setLoadingDocs(false);
      });
  }, []);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  // API health ping
  useEffect(() => {
    let alive = true;
    const check = () =>
      checkApiHealth().then((ok) => alive && setApiOnline(ok));
    check();
    const timer = setInterval(check, 10000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const clearSelection = () => {
    setSelectedDocId(null);
    setSelectedPageIndex(null);
    setSelectedFactId(null);
  };

  const handleSelectTab = (tab) => {
    if (tab !== "document_detail") clearSelection();
    setCurrentTab(tab);
    if (tab === "documents") loadDocs();
  };

  const handleSelectDoc = (docId, pageIndex = null, factId = null) => {
    setSelectedDocId(docId);
    setSelectedPageIndex(pageIndex);
    setSelectedFactId(factId);
    setCurrentTab("document_detail");
  };

  const handleUploadComplete = (doc) => {
    loadDocs();
    setSelectedDocId(doc.id || doc.document_id);
    setSelectedPageIndex(0);
    setSelectedFactId(null);
    setCurrentTab("document_detail");
  };

  const totalFacts = documents.reduce((acc, d) => acc + (d.fact_count || 0), 0);

  return (
    <div className="app-shell">
      <Sidebar
        currentTab={currentTab}
        onSelectTab={handleSelectTab}
        totalDocs={documents.length}
        totalFacts={totalFacts}
      />

      <div className="app-main">
        <Topbar
          currentTab={currentTab}
          selectedDocId={selectedDocId}
          documents={documents}
          onSelectTab={handleSelectTab}
          apiOnline={apiOnline}
          onBack={() => {
            clearSelection();
            handleSelectTab("documents");
          }}
        />

        <main className="app-content">
          <div className="fill-view view-enter" key={currentTab}>
            {currentTab === "documents" && (
              <DocumentList
                documents={documents}
                onSelectDoc={(id) => handleSelectDoc(id)}
                onUploadClick={() => handleSelectTab("upload")}
                onCompareClick={() => handleSelectTab("compare")}
                onGraphClick={() => handleSelectTab("graph")}
                loading={loadingDocs}
              />
            )}

            {currentTab === "upload" && (
              <UploadView
                onUploadComplete={handleUploadComplete}
                onSelectDoc={(id) => handleSelectDoc(id)}
              />
            )}

            {currentTab === "document_detail" && selectedDocId && (
              <DocumentDetail
                docId={selectedDocId}
                initialPageIndex={selectedPageIndex}
                initialFactId={selectedFactId}
                onBack={() => {
                  clearSelection();
                  handleSelectTab("documents");
                }}
                onCompareClick={() => handleSelectTab("compare")}
              />
            )}

            {currentTab === "compare" && <CompareView />}

            {currentTab === "synthesis" && <SynthesisView />}

            {currentTab === "graph" && (
              <GraphView
                onNavigateToDocPage={(docId, pageIndex, factId) =>
                  handleSelectDoc(docId, pageIndex, factId)
                }
              />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

