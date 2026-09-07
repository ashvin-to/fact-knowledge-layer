import React, { useState, useEffect } from "react";
import { uploadDocument, fetchDocument } from "../api";
import {
  UploadIcon,
  CheckIcon,
  AlertTriangleIcon,
  ArrowRightIcon,
  FileTextIcon,
} from "./Icons";

export default function UploadView({ onUploadComplete, onSelectDoc }) {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [processingDoc, setProcessingDoc] = useState(null);
  const [error, setError] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // Poll document status while processing
  useEffect(() => {
    if (
      !processingDoc ||
      processingDoc.status === "done" ||
      processingDoc.status === "failed"
    ) {
      return;
    }

    const interval = setInterval(async () => {
      try {
        const doc = await fetchDocument(
          processingDoc.id || processingDoc.document_id
        );
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
      if (
        droppedFile.type === "application/pdf" ||
        droppedFile.name.endsWith(".pdf")
      ) {
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
      <div className="view-head">
        <div>
          <div className="view-title">
            <UploadIcon size={20} /> Upload Document
          </div>
          <p className="view-desc">
            Ingest a business report, prospectus, or financial statement. The
            pipeline classifies the layout, extracts atomic facts with page
            grounding, and prepares them for cross-document comparison.
          </p>
        </div>
      </div>

      <div className="card card--pad upload-card">
        <div
          className={`dropzone ${isDragOver ? "dragover" : ""} ${
            file ? "has-file" : ""
          }`}
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
            <div className="drop-icon-box">
              {file ? <FileTextIcon size={30} /> : <UploadIcon size={30} />}
            </div>
            {file ? (
              <div className="file-info">
                <span className="file-name">{file.name}</span>
                <span className="file-size">
                  {(file.size / 1024 / 1024).toFixed(2)} MB — click to replace
                </span>
              </div>
            ) : (
              <div className="drop-prompt">
                <span className="drop-title">
                  Click to select or drag &amp; drop a PDF
                </span>
                <span className="drop-hint">PDF up to 100 MB</span>
              </div>
            )}
          </label>
        </div>

        {error && (
          <div className="alert alert--bad mt-4">
            <AlertTriangleIcon size={16} />
            <span>{error}</span>
          </div>
        )}

        <div className="upload-actions">
          <button
            className="btn btn--primary btn--lg"
            onClick={handleUpload}
            disabled={!file || uploading}
          >
            {uploading ? (
              <>
                <span className="spinner spinner--sm" /> Ingesting &amp;
                extracting facts…
              </>
            ) : (
              <>
                <UploadIcon size={18} /> Process Document
              </>
            )}
          </button>
        </div>

        {processingDoc && (
          <div className="processing-status card--pad card mt-4">
            <div className="status-header">
              <h4>Processing Status</h4>
              <span className={`badge badge--status-${processingDoc.status}`}>
                {processingDoc.status}
              </span>
            </div>

            {processingDoc.status === "processing" && (
              <div className="progress-info">
                <div className="spinner" />
                <p>
                  Classifying layout, rendering pages, and extracting atomic
                  facts…
                </p>
              </div>
            )}

            {processingDoc.status === "done" && (
              <div className="done-summary">
                <div className="alert alert--ok">
                  <CheckIcon size={18} />
                  <span>
                    Extraction complete —{" "}
                    <strong>{processingDoc.fact_count}</strong> facts across{" "}
                    <strong>{processingDoc.page_count}</strong> pages.
                  </span>
                </div>
                <div className="action-row mt-3">
                  <button
                    className="btn btn--primary"
                    onClick={() =>
                      onSelectDoc &&
                      onSelectDoc(processingDoc.id || processingDoc.document_id)
                    }
                  >
                    Browse Extracted Facts <ArrowRightIcon size={14} />
                  </button>
                </div>
              </div>
            )}

            {processingDoc.status === "failed" && (
              <div className="alert alert--bad">
                <AlertTriangleIcon size={18} />
                <span>
                  Processing failed:{" "}
                  {processingDoc.error_message || "Unknown error"}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
