import React, { useState, useEffect } from "react";
import { uploadDocument, fetchDocument } from "../api";

export default function UploadView({ onUploadComplete, onSelectDoc }) {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [processingDoc, setProcessingDoc] = useState(null);
  const [error, setError] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // Poll document status if processing
  useEffect(() => {
    if (!processingDoc || processingDoc.status === "done" || processingDoc.status === "failed") {
      return;
    }

    const interval = setInterval(async () => {
      try {
        const doc = await fetchDocument(processingDoc.id || processingDoc.document_id);
        setProcessingDoc(doc);
        if (doc.status === "done" || doc.status === "failed") {
          setUploading(false);
          clearInterval(interval);
          if (onUploadComplete) onUploadComplete(doc);
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [processingDoc, onUploadComplete]);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setError(null);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile.type === "application/pdf" || droppedFile.name.endsWith(".pdf")) {
        setFile(droppedFile);
        setError(null);
      } else {
        setError("Only PDF files are supported.");
      }
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setProcessingDoc(null);

    try {
      const resp = await uploadDocument(file);
      setProcessingDoc(resp);
      if (resp.status === "done") {
        setUploading(false);
        if (onUploadComplete) onUploadComplete(resp);
      }
    } catch (err) {
      setError(err.message || "Upload failed");
      setUploading(false);
    }
  };

  return (
    <div className="upload-view">
      <div className="card upload-card">
        <h2>Upload Document for Fact Extraction</h2>
        <p className="description">
          Upload any business report, prospectus, or financial statement PDF. The pipeline will classify the document, extract atomic facts with page grounding, and make them available for cross-document comparison.
        </p>

        <div
          className={`dropzone ${isDragOver ? "dragover" : ""} ${file ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
        >
          <input
            type="file"
            id="pdf-input"
            accept="application/pdf"
            onChange={handleFileChange}
            disabled={uploading}
          />
          <label htmlFor="pdf-input" className="dropzone-label">
            <span className="drop-icon">📄</span>
            {file ? (
              <span className="file-name">{file.name} ({ (file.size / 1024).toFixed(1) } KB)</span>
            ) : (
              <span>Drag & drop a PDF here, or <strong>browse file</strong></span>
            )}
          </label>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <div className="upload-actions">
          <button
            className="btn btn-primary"
            onClick={handleUpload}
            disabled={!file || uploading}
          >
            {uploading ? "Processing PDF..." : "Extract Facts from PDF"}
          </button>
        </div>

        {uploading && (
          <div className="processing-indicator">
            <div className="spinner"></div>
            <div className="processing-text">
              <h4>Extracting facts page-by-page...</h4>
              <p>LLM is grounding atomic facts to exact verbatim evidence.</p>
            </div>
          </div>
        )}

        {processingDoc && processingDoc.status === "done" && (
          <div className="result-summary card">
            <h3>✓ Ingestion Complete!</h3>
            <div className="stats-grid">
              <div className="stat-box">
                <span className="stat-label">Document</span>
                <span className="stat-val">{processingDoc.filename}</span>
              </div>
              <div className="stat-box">
                <span className="stat-label">Total Facts Extracted</span>
                <span className="stat-val highlight">{processingDoc.fact_count ?? "Ready"}</span>
              </div>
              <div className="stat-box">
                <span className="stat-label">Total Pages</span>
                <span className="stat-val">{processingDoc.page_count ?? "—"}</span>
              </div>
              <div className="stat-box">
                <span className="stat-label">PDF Type</span>
                <span className="stat-val">{processingDoc.pdf_type ?? "text_based"}</span>
              </div>
            </div>

            {processingDoc.skipped_pages?.length > 0 && (
              <p className="note">Skipped OCR/scanned pages: {processingDoc.skipped_pages.join(", ")}</p>
            )}

            <button
              className="btn btn-secondary"
              onClick={() => onSelectDoc(processingDoc.id || processingDoc.document_id)}
            >
              Browse Extracted Facts →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
