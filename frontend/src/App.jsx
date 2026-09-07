import React, { useState, useEffect } from "react";
import Navbar from "./components/Navbar";
import DocumentList from "./components/DocumentList";
import UploadView from "./components/UploadView";
import DocumentDetail from "./components/DocumentDetail";
import CompareView from "./components/CompareView";
import { fetchDocuments } from "./api";
import "./App.css";

export default function App() {
  const [currentTab, setCurrentTab] = useState("documents");
  const [selectedDocId, setSelectedDocId] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [loadingDocs, setLoadingDocs] = useState(false);

  const loadDocs = () => {
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
  };

  useEffect(() => {
    loadDocs();
  }, []);

  const handleSelectDoc = (docId) => {
    setSelectedDocId(docId);
    setCurrentTab("document_detail");
  };

  const handleUploadComplete = (doc) => {
    loadDocs();
    setSelectedDocId(doc.id || doc.document_id);
    setCurrentTab("document_detail");
  };

  return (
    <div className="app-container">
      <Navbar
        currentTab={currentTab}
        onSelectTab={(tab) => {
          if (tab !== "document_detail") setSelectedDocId(null);
          setCurrentTab(tab);
          if (tab === "documents") loadDocs();
        }}
        totalDocs={documents.length}
      />

      <main className="main-content">
        {currentTab === "documents" && (
          <DocumentList
            documents={documents}
            onSelectDoc={handleSelectDoc}
            onUploadClick={() => setCurrentTab("upload")}
            onCompareClick={() => setCurrentTab("compare")}
            loading={loadingDocs}
          />
        )}

        {currentTab === "upload" && (
          <UploadView
            onUploadComplete={handleUploadComplete}
            onSelectDoc={handleSelectDoc}
          />
        )}

        {currentTab === "document_detail" && selectedDocId && (
          <DocumentDetail
            docId={selectedDocId}
            onBack={() => {
              setSelectedDocId(null);
              setCurrentTab("documents");
              loadDocs();
            }}
            onCompareClick={() => setCurrentTab("compare")}
          />
        )}

        {currentTab === "compare" && (
          <CompareView />
        )}
      </main>
    </div>
  );
}
