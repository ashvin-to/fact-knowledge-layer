import React, { useState, useEffect, useRef } from "react";
import { fetchEvidenceBBox, fetchPageImage } from "../api";

export default function PageViewer({ docId, pageIndex, evidenceText, filename }) {
  const [bboxData, setBboxData] = useState(null);
  const [loadingBbox, setLoadingBbox] = useState(false);
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(1);
  const imgRef = useRef(null);

  const imageUrl = fetchPageImage(docId, pageIndex);

  useEffect(() => {
    if (!docId || pageIndex === undefined || pageIndex === null || !evidenceText) {
      setBboxData(null);
      return;
    }

    let isMounted = true;
    setLoadingBbox(true);

    fetchEvidenceBBox(docId, pageIndex, evidenceText)
      .then((data) => {
        if (isMounted) {
          setBboxData(data);
          setLoadingBbox(false);
        }
      })
      .catch((err) => {
        console.warn("Could not fetch bbox:", err);
        if (isMounted) {
          setBboxData(null);
          setLoadingBbox(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [docId, pageIndex, evidenceText]);

  const handleImageLoad = () => {
    if (imgRef.current) {
      setImageSize({
        width: imgRef.current.clientWidth,
        height: imgRef.current.clientHeight,
      });
    }
  };

  useEffect(() => {
    const handleResize = () => {
      if (imgRef.current) {
        setImageSize({
          width: imgRef.current.clientWidth,
          height: imgRef.current.clientHeight,
        });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const scaleX = bboxData && bboxData.page_width ? imageSize.width / bboxData.page_width : 1;
  const scaleY = bboxData && bboxData.page_height ? imageSize.height / bboxData.page_height : 1;

  return (
    <div className="page-viewer-container">
      <div className="page-viewer-header">
        <div className="page-meta">
          <span className="page-tag">Page {pageIndex + 1}</span>
          {filename && <span className="doc-name">{filename}</span>}
        </div>
        <div className="viewer-controls">
          {bboxData && bboxData.match_type === "exact" && (
            <span className="badge badge-success">✓ Exact Evidence Match</span>
          )}
          {bboxData && bboxData.match_type === "fallback_prefix" && (
            <span className="badge badge-warning">⚡ Prefix Match</span>
          )}
          {bboxData && bboxData.match_type === "none" && (
            <span className="badge badge-muted">Page View (No Text Overlay)</span>
          )}
          <div className="zoom-buttons">
            <button className="btn-sm" onClick={() => setZoom((z) => Math.max(0.6, z - 0.2))}>-</button>
            <span className="zoom-level">{Math.round(zoom * 100)}%</span>
            <button className="btn-sm" onClick={() => setZoom((z) => Math.min(2.0, z + 0.2))}>+</button>
          </div>
        </div>
      </div>

      <div className="page-image-scroll-wrapper">
        <div
          className="page-image-relative-container"
          style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
        >
          <img
            ref={imgRef}
            src={imageUrl}
            alt={`Page ${pageIndex + 1}`}
            className="page-rendered-image"
            onLoad={handleImageLoad}
          />

          {/* Bounding box highlights */}
          {imageSize.width > 0 &&
            bboxData?.bboxes?.map((box, idx) => {
              const left = box.x0 * scaleX;
              const top = box.y0 * scaleY;
              const width = Math.max((box.x1 - box.x0) * scaleX, 10);
              const height = Math.max((box.y1 - box.y0) * scaleY, 12);

              return (
                <div
                  key={idx}
                  className="evidence-highlight-box"
                  style={{
                    left: `${left}px`,
                    top: `${top}px`,
                    width: `${width}px`,
                    height: `${height}px`,
                  }}
                  title={evidenceText}
                />
              );
            })}
        </div>
      </div>
    </div>
  );
}
