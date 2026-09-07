import React, { useState, useEffect, useRef, useCallback } from "react";
import { fetchPageImage, fetchEvidenceBBox } from "../api";
import {
  ZoomInIcon,
  ZoomOutIcon,
  ResetIcon,
  CheckIcon,
  AlertTriangleIcon,
  CloseIcon,
} from "./Icons";

export default function PageViewer({ docId, pageIndex, evidenceText, filename }) {
  const [bboxData, setBboxData] = useState(null);
  const [loadingBbox, setLoadingBbox] = useState(false);
  const [viewMode, setViewMode] = useState("fit-page"); // 'fit-page' | 'fit-width' | 'custom'
  const [zoom, setZoom] = useState(1.0);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [fullscreenOpen, setFullscreenOpen] = useState(false);

  const imgRef = useRef(null);
  const viewportRef = useRef(null);
  const [renderedDims, setRenderedDims] = useState({ width: 0, height: 0 });

  // Fetch bounding boxes when evidence text changes
  useEffect(() => {
    if (!docId || pageIndex === undefined || !evidenceText) {
      setBboxData(null);
      return;
    }

    let isMounted = true;
    setLoadingBbox(true);

    fetchEvidenceBBox(docId, pageIndex, evidenceText)
      .then((data) => {
        if (!isMounted) return;
        setBboxData(data);
        setLoadingBbox(false);
      })
      .catch((err) => {
        console.warn("Could not compute evidence bbox:", err);
        if (isMounted) {
          setBboxData(null);
          setLoadingBbox(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [docId, pageIndex, evidenceText]);

  const updateRenderedDims = useCallback(() => {
    if (imgRef.current) {
      const rect = imgRef.current.getBoundingClientRect();
      setRenderedDims({
        width: rect.width || imgRef.current.clientWidth,
        height: rect.height || imgRef.current.clientHeight,
      });
    }
  }, []);

  const handleImageLoad = (e) => {
    setImageLoaded(true);
    setImageError(false);
    updateRenderedDims();
  };

  useEffect(() => {
    window.addEventListener("resize", updateRenderedDims);
    return () => window.removeEventListener("resize", updateRenderedDims);
  }, [updateRenderedDims]);

  useEffect(() => {
    // Recalculate dimensions on viewMode or zoom change
    const timer = setTimeout(updateRenderedDims, 60);
    return () => clearTimeout(timer);
  }, [viewMode, zoom, updateRenderedDims]);

  const imageUrl = fetchPageImage(docId, pageIndex);

  const rectList = bboxData?.rects || bboxData?.bboxes || [];
  const matchMethod = bboxData?.match_method || bboxData?.match_type || "exact";

  const renderBBoxes = () => {
    if (rectList.length === 0 || !imageLoaded) {
      return null;
    }

    const { page_width, page_height } = bboxData;
    if (!page_width || !page_height || !renderedDims.width || !renderedDims.height) return null;

    const scaleX = renderedDims.width / page_width;
    const scaleY = renderedDims.height / page_height;

    return rectList.map((rect, idx) => (
      <div
        key={idx}
        className="evidence-bbox-overlay"
        style={{
          left: `${rect.x0 * scaleX}px`,
          top: `${rect.y0 * scaleY}px`,
          width: `${Math.max(4, (rect.x1 - rect.x0) * scaleX)}px`,
          height: `${Math.max(4, (rect.y1 - rect.y0) * scaleY)}px`,
        }}
        title={`Evidence Grounding (${matchMethod}): "${evidenceText}"`}
      />
    ));
  };

  const handleZoomIn = () => {
    setViewMode("custom");
    setZoom((z) => Math.min(3.0, Number((z + 0.2).toFixed(1))));
  };

  const handleZoomOut = () => {
    setViewMode("custom");
    setZoom((z) => Math.max(0.4, Number((z - 0.2).toFixed(1))));
  };

  const handleSetFitPage = () => {
    setViewMode("fit-page");
    setZoom(1.0);
  };

  const handleSetFitWidth = () => {
    setViewMode("fit-width");
    setZoom(1.0);
  };

  return (
    <div className="page-viewer-container">
      {/* Top Controls Toolbar */}
      <div className="page-viewer-toolbar">
        <div className="toolbar-info">
          <span className="chip">
            Page <b>{pageIndex + 1}</b>
          </span>
          {filename && <span className="t-xs faint truncate" style={{ maxWidth: "160px" }}>{filename}</span>}
          {loadingBbox && (
            <span className="t-xs c-warn">Locating evidence…</span>
          )}
          {rectList.length > 0 && (
            <span className="t-xs c-ok" title={`Matched using ${matchMethod}`}>
              <CheckIcon size={12} /> Grounded ({matchMethod.replace("_", " ")})
            </span>
          )}
          {bboxData && rectList.length === 0 && !loadingBbox && (
            <span className="t-xs faint" title="Evidence text was not found verbatim in page text layer">
              Exact text not in layer
            </span>
          )}
        </div>

        <div className="zoom-controls">
          <div className="seg">
            <button
              className={`seg-btn ${viewMode === "fit-page" ? "active" : ""}`}
              onClick={handleSetFitPage}
              title="Fit whole page on screen"
            >
              Fit Page
            </button>
            <button
              className={`seg-btn ${viewMode === "fit-width" ? "active" : ""}`}
              onClick={handleSetFitWidth}
              title="Fit to full width (scrollable)"
            >
              Fit Width
            </button>
          </div>

          <button
            className="btn btn--ghost btn--icon btn--sm"
            onClick={handleZoomOut}
            title="Zoom out"
          >
            <ZoomOutIcon size={14} />
          </button>
          <span className="zoom-level mono">
            {viewMode === "fit-page" ? "Auto" : `${Math.round(zoom * 100)}%`}
          </span>
          <button
            className="btn btn--ghost btn--icon btn--sm"
            onClick={handleZoomIn}
            title="Zoom in"
          >
            <ZoomInIcon size={14} />
          </button>
          <button
            className="btn btn--ghost btn--icon btn--sm"
            onClick={handleSetFitPage}
            title="Reset to Full Page"
          >
            <ResetIcon size={14} />
          </button>
        </div>
      </div>

      {/* Main Viewport Container */}
      <div
        ref={viewportRef}
        className={`image-viewport ${viewMode === "fit-width" ? "align-top" : ""}`}
      >
        {imageError ? (
          <div className="state-box">
            <AlertTriangleIcon size={24} className="c-warn" />
            <p>Could not load page {pageIndex + 1} image.</p>
          </div>
        ) : (
          <div
            className={`image-wrapper ${viewMode}`}
            style={
              viewMode === "custom"
                ? { width: `${Math.round(zoom * 100)}%` }
                : undefined
            }
          >
            <div className="image-fit-box">
              <img
                ref={imgRef}
                src={imageUrl}
                alt={`Page ${pageIndex + 1}`}
                className="pdf-page-image"
                onLoad={handleImageLoad}
                onError={() => setImageError(true)}
              />
              {renderBBoxes()}
            </div>
          </div>
        )}
      </div>

      {/* Fullscreen Expand Modal */}
      {fullscreenOpen && (
        <div className="modal-overlay" onClick={() => setFullscreenOpen(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title-row">
                <h3>{filename} — Page {pageIndex + 1} Fullscreen Inspection</h3>
                {rectList.length > 0 && (
                  <span className="badge badge--ok">
                    <CheckIcon size={12} /> {rectList.length} Grounding Highlight(s)
                  </span>
                )}
              </div>
              <button className="btn-close" onClick={() => setFullscreenOpen(false)}>
                <CloseIcon size={16} />
              </button>
            </div>
            <div className="modal-body" style={{ alignItems: "center", justifyContent: "center" }}>
              <div className="image-fit-box" style={{ maxWidth: "100%", maxHeight: "100%" }}>
                <img
                  src={imageUrl}
                  alt={`Page ${pageIndex + 1}`}
                  style={{ maxHeight: "75vh", width: "auto", objectFit: "contain", display: "block" }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
