import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import * as d3 from "d3";
import { fetchGraph, fetchDocuments } from "../api";
import {
  SearchIcon,
  ZoomInIcon,
  ZoomOutIcon,
  ResetIcon,
  PlayIcon,
  PauseIcon,
  EyeIcon,
  CloseIcon,
  LayersIcon,
  GraphIcon,
  SparklesIcon,
  CopyIcon,
  CheckIcon,
  DocumentIcon,
  AlertTriangleIcon,
} from "./Icons";

const DOC_COLORS = [
  { primary: "#8b5cf6", light: "#c4b5fd", bg: "rgba(139, 92, 246, 0.2)", glow: "rgba(139, 92, 246, 0.5)", border: "#a78bfa" },
  { primary: "#06b6d4", light: "#67e8f9", bg: "rgba(6, 182, 212, 0.2)", glow: "rgba(6, 182, 212, 0.5)", border: "#22d3ee" },
  { primary: "#10b981", light: "#6ee7b7", bg: "rgba(16, 185, 129, 0.2)", glow: "rgba(16, 185, 129, 0.5)", border: "#34d399" },
  { primary: "#f59e0b", light: "#fcd34d", bg: "rgba(245, 158, 11, 0.2)", glow: "rgba(245, 158, 11, 0.5)", border: "#fbbf24" },
  { primary: "#ec4899", light: "#f472b6", bg: "rgba(236, 72, 153, 0.2)", glow: "rgba(236, 72, 153, 0.5)", border: "#f472b6" },
];

const LINK_COLORS = {
  corroborate: "#10b981",
  contradict: "#ef4444",
  context_reconciled: "#38bdf8",
  unrelated: "#475569",
};

export default function GraphView({ onNavigateToDocPage }) {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [documents, setDocuments] = useState([]);
  const [activeDocId, setActiveDocId] = useState("all");
  const [relFilter, setRelFilter] = useState("all");
  const [graphMode, setGraphMode] = useState("orbit"); // 'orbit' | 'entity' | 'bridges'
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState(null);
  const [simulationRunning, setSimulationRunning] = useState(true);
  const [showClusterHulls, setShowClusterHulls] = useState(true);
  const [copiedFactId, setCopiedFactId] = useState(null);

  const svgRef = useRef(null);
  const tooltipRef = useRef(null);
  const simulationRef = useRef(null);
  const zoomBehaviorRef = useRef(null);
  const d3ElementsRef = useRef(null); // stores references to { nodeSelection, linkSelection }

  // Load documents
  useEffect(() => {
    fetchDocuments()
      .then((res) => setDocuments(res.documents || []))
      .catch((err) => console.error("Error loading documents for graph:", err));
  }, []);

  // Fetch graph data
  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    const docParam = activeDocId === "all" ? null : activeDocId;
    const relParam = relFilter === "all" ? null : relFilter;

    fetchGraph(docParam, relParam)
      .then((data) => {
        if (!isMounted) return;
        setGraphData(data);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Error fetching graph data:", err);
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [activeDocId, relFilter]);

  const docColorMap = useMemo(() => {
    const map = new Map();
    documents.forEach((d, i) => {
      map.set(d.id, DOC_COLORS[i % DOC_COLORS.length]);
    });
    return map;
  }, [documents]);

  const docClustersSummary = useMemo(() => {
    const map = new Map();
    documents.forEach((d, i) => {
      map.set(d.id, {
        document: d,
        color: DOC_COLORS[i % DOC_COLORS.length],
        factCount: 0,
        contradictCount: 0,
      });
    });

    graphData.nodes.forEach((n) => {
      if (n.node_type === "fact" && map.has(n.document_id)) {
        map.get(n.document_id).factCount++;
      }
    });

    graphData.links.forEach((l) => {
      if (l.link_type === "contradict") {
        const sId = typeof l.source === "object" ? l.source.id : l.source;
        const s = graphData.nodes.find((n) => n.id === sId);
        if (s && map.has(s.document_id)) map.get(s.document_id).contradictCount++;
      }
    });

    return Array.from(map.values());
  }, [documents, graphData]);

  // Transform graph data based on graphMode
  const processedGraph = useMemo(() => {
    if (graphMode === "bridges") {
      const relNodeIds = new Set();
      graphData.links.forEach((l) => {
        if (l.link_type !== "contains") {
          const sId = typeof l.source === "object" ? l.source.id : l.source;
          const tId = typeof l.target === "object" ? l.target.id : l.target;
          relNodeIds.add(sId);
          relNodeIds.add(tId);
        }
      });
      const docIdsWithBridges = new Set();
      graphData.nodes.forEach((n) => {
        if (relNodeIds.has(n.id)) docIdsWithBridges.add(n.document_id);
      });
      docIdsWithBridges.forEach((dId) => relNodeIds.add(`doc-${dId}`));

      const nodes = graphData.nodes.filter((n) => relNodeIds.has(n.id));
      const links = graphData.links.filter((l) => {
        const sId = typeof l.source === "object" ? l.source.id : l.source;
        const tId = typeof l.target === "object" ? l.target.id : l.target;
        return relNodeIds.has(sId) && relNodeIds.has(tId);
      });
      return { nodes, links };
    }

    if (graphMode === "entity") {
      const entityMap = new Map();
      const docSet = new Set();

      graphData.nodes.forEach((n) => {
        if (n.node_type === "document") {
          docSet.add(n);
        } else if (n.node_type === "fact") {
          const key = (n.subject || "General").trim();
          if (!entityMap.has(key)) {
            entityMap.set(key, {
              id: `entity-${key}`,
              node_type: "entity",
              label: key,
              subject: key,
              facts: [],
              document_ids: new Set(),
            });
          }
          const ent = entityMap.get(key);
          ent.facts.push(n);
          ent.document_ids.add(n.document_id);
        }
      });

      const nodes = [...Array.from(docSet)];
      const links = [];

      entityMap.forEach((ent) => {
        nodes.push({
          ...ent,
          document_count: ent.document_ids.size,
          fact_count: ent.facts.length,
        });

        ent.document_ids.forEach((dId) => {
          links.push({
            source: `doc-${dId}`,
            target: ent.id,
            link_type: "contains",
            label: "mentions",
          });
        });
      });

      graphData.links.forEach((l) => {
        if (l.link_type !== "contains") {
          const s = graphData.nodes.find((n) => n.id === (typeof l.source === "object" ? l.source.id : l.source));
          const t = graphData.nodes.find((n) => n.id === (typeof l.target === "object" ? l.target.id : l.target));
          if (s && t && s.subject && t.subject) {
            links.push({
              source: `entity-${s.subject.trim()}`,
              target: `entity-${t.subject.trim()}`,
              link_type: l.link_type,
              label: l.label,
              explanation: l.explanation,
              confidence: l.confidence,
              similarity_score: l.similarity_score,
            });
          }
        }
      });

      return { nodes, links };
    }

    return graphData;
  }, [graphData, graphMode]);

  // Helper to highlight neighbors smoothly in D3 without restarting simulation
  const highlightNeighborsInD3 = useCallback((targetNodeId) => {
    if (!d3ElementsRef.current) return;
    const { nodeSelection, linkSelection, links } = d3ElementsRef.current;

    if (!targetNodeId) {
      // Reset all opacities
      nodeSelection.attr("opacity", 1.0);
      linkSelection.attr("opacity", (d) => (d.link_type === "contains" ? 0.35 : 0.85));
      return;
    }

    const neighborIds = new Set([targetNodeId]);
    const linkIndices = new Set();

    links.forEach((l, idx) => {
      const sId = typeof l.source === "object" ? l.source.id : l.source;
      const tId = typeof l.target === "object" ? l.target.id : l.target;
      if (sId === targetNodeId || tId === targetNodeId) {
        neighborIds.add(sId);
        neighborIds.add(tId);
        linkIndices.add(idx);
      }
    });

    nodeSelection.attr("opacity", (d) => (neighborIds.has(d.id) ? 1.0 : 0.12));
    linkSelection.attr("opacity", (d, idx) => (linkIndices.has(idx) ? 1.0 : 0.05));
  }, []);

  // Initialize and run D3 Force Simulation (ONLY when data/mode changes, NOT on hover)
  useEffect(() => {
    if (!svgRef.current || loading || processedGraph.nodes.length === 0) return;

    const width = svgRef.current.clientWidth || 900;
    const height = svgRef.current.clientHeight || 650;

    const q = searchQuery.toLowerCase().trim();
    const matchingNodeIds = new Set(
      processedGraph.nodes
        .filter((n) => {
          if (!q) return true;
          return (
            (n.label && n.label.toLowerCase().includes(q)) ||
            (n.subject && n.subject.toLowerCase().includes(q)) ||
            (n.value && n.value.toLowerCase().includes(q)) ||
            (n.filename && n.filename.toLowerCase().includes(q))
          );
        })
        .map((n) => n.id)
    );

    const nodes = processedGraph.nodes
      .filter((n) => matchingNodeIds.has(n.id))
      .map((d) => ({ ...d }));

    const validNodeIdSet = new Set(nodes.map((n) => n.id));
    const links = processedGraph.links
      .filter((l) => {
        const sourceId = typeof l.source === "object" ? l.source.id : l.source;
        const targetId = typeof l.target === "object" ? l.target.id : l.target;
        return validNodeIdSet.has(sourceId) && validNodeIdSet.has(targetId);
      })
      .map((d, i) => ({ ...d, originalIndex: i }));

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const g = svg.append("g").attr("class", "graph-root");

    const zoom = d3
      .zoom()
      .scaleExtent([0.1, 4])
      .on("zoom", (event) => {
        g.attr("transform", event.transform);
      });

    svg.call(zoom);
    zoomBehaviorRef.current = zoom;

    // Arrow markers
    const defs = svg.append("defs");
    Object.entries({
      "marker-corroborate": LINK_COLORS.corroborate,
      "marker-contradict": LINK_COLORS.contradict,
      "marker-reconciled": LINK_COLORS.context_reconciled,
    }).forEach(([id, fill]) => {
      defs
        .append("marker")
        .attr("id", id)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 24)
        .attr("refY", 0)
        .attr("markerWidth", 6)
        .attr("markerHeight", 6)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", fill);
    });

    // Orbital Cluster Centers per document
    const docNodes = nodes.filter((n) => n.node_type === "document");
    const clusterCenters = new Map();
    const docCount = Math.max(1, docNodes.length);
    const radius = Math.min(width, height) * 0.32;

    docNodes.forEach((docNode, idx) => {
      const angle = (idx / docCount) * 2 * Math.PI - Math.PI / 2;
      clusterCenters.set(docNode.document_id, {
        x: width / 2 + (docCount === 1 ? 0 : Math.cos(angle) * radius),
        y: height / 2 + (docCount === 1 ? 0 : Math.sin(angle) * radius),
      });
    });

    function forceCluster(alpha) {
      for (const d of nodes) {
        if (clusterCenters.has(d.document_id)) {
          const center = clusterCenters.get(d.document_id);
          const pull = d.node_type === "document" ? 0.18 : 0.05;
          d.vx += (center.x - d.x) * pull * alpha;
          d.vy += (center.y - d.y) * pull * alpha;
        }
      }
    }

    const simulation = d3
      .forceSimulation(nodes)
      .velocityDecay(0.4)
      .force(
        "link",
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance((d) => (d.link_type === "contains" ? 60 : 160))
          .strength((d) => (d.link_type === "contains" ? 0.6 : 0.2))
      )
      .force(
        "charge",
        d3.forceManyBody().strength((d) =>
          d.node_type === "document" ? -600 : d.node_type === "entity" ? -350 : -160
        )
      )
      .force("center", d3.forceCenter(width / 2, height / 2).strength(0.04))
      .force(
        "collide",
        d3.forceCollide().radius((d) =>
          d.node_type === "document" ? 42 : d.node_type === "entity" ? 28 : 18
        ).iterations(2)
      )
      .force("cluster", forceCluster);

    simulationRef.current = simulation;
    setSimulationRunning(true);

    // Convex Hull layer
    const hullGroup = g.append("g").attr("class", "cluster-hulls");

    // Render Links
    const linkGroup = g.append("g").attr("class", "links");
    const link = linkGroup
      .selectAll("path")
      .data(links)
      .enter()
      .append("path")
      .attr("fill", "none")
      .attr("stroke", (d) => {
        switch (d.link_type) {
          case "corroborate": return LINK_COLORS.corroborate;
          case "contradict": return LINK_COLORS.contradict;
          case "context_reconciled": return LINK_COLORS.context_reconciled;
          case "unrelated": return LINK_COLORS.unrelated;
          default: {
            const docTheme = docColorMap.get(d.document_id);
            return docTheme ? docTheme.glow : "rgba(148, 163, 184, 0.25)";
          }
        }
      })
      .attr("stroke-width", (d) => (d.link_type === "contains" ? 1.2 : 2.5))
      .attr("stroke-dasharray", (d) => {
        if (d.link_type === "contains") return "3,3";
        if (d.link_type === "contradict") return "6,3";
        return null;
      })
      .attr("opacity", (d) => (d.link_type === "contains" ? 0.35 : 0.85))
      .attr("marker-end", (d) => {
        if (d.link_type === "corroborate") return "url(#marker-corroborate)";
        if (d.link_type === "contradict") return "url(#marker-contradict)";
        if (d.link_type === "context_reconciled") return "url(#marker-reconciled)";
        return null;
      });

    // Render Nodes Group
    const nodeGroup = g.append("g").attr("class", "nodes");
    const node = nodeGroup
      .selectAll("g")
      .data(nodes)
      .enter()
      .append("g")
      .attr("class", (d) => `node-group node-${d.node_type}`)
      .style("cursor", "pointer")
      .call(
        d3
          .drag()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      )
      .on("click", (event, d) => {
        event.stopPropagation();
        setSelectedNode(d);
        highlightNeighborsInD3(d.id);
      })
      .on("mouseenter", (event, d) => {
        highlightNeighborsInD3(d.id);
        if (tooltipRef.current) {
          const typeLabel = d.node_type.toUpperCase();
          const title = d.subject || d.label;
          const sub = d.predicate ? `${d.predicate}: <strong>${d.value || ""} ${d.unit || ""}</strong>` : "";
          const meta = d.filename ? `${d.filename} (Page ${(d.pdf_page_index ?? 0) + 1})` : "";

          tooltipRef.current.innerHTML = `
            <div class="tooltip-type">${typeLabel}</div>
            <div class="tooltip-title">${title}</div>
            ${sub ? `<div class="tooltip-sub">${sub}</div>` : ""}
            ${meta ? `<div class="tooltip-meta">${meta}</div>` : ""}
          `;
          tooltipRef.current.style.display = "block";
          tooltipRef.current.style.left = `${event.clientX + 14}px`;
          tooltipRef.current.style.top = `${event.clientY + 14}px`;
        }
      })
      .on("mousemove", (event) => {
        if (tooltipRef.current) {
          tooltipRef.current.style.left = `${event.clientX + 14}px`;
          tooltipRef.current.style.top = `${event.clientY + 14}px`;
        }
      })
      .on("mouseleave", () => {
        if (tooltipRef.current) {
          tooltipRef.current.style.display = "none";
        }
        highlightNeighborsInD3(selectedNode?.id || null);
      })
      .on("dblclick", (event, d) => {
        event.stopPropagation();
        if (svgRef.current && zoomBehaviorRef.current) {
          d3.select(svgRef.current)
            .transition()
            .duration(500)
            .call(
              zoomBehaviorRef.current.transform,
              d3.zoomIdentity.translate(width / 2 - d.x * 1.8, height / 2 - d.y * 1.8).scale(1.8)
            );
        }
      });

    // Node circles with document theme
    node
      .append("circle")
      .attr("r", (d) => {
        if (d.node_type === "document") return 22;
        if (d.node_type === "entity") return Math.min(24, 12 + (d.fact_count || 1) * 2);
        return 10;
      })
      .attr("fill", (d) => {
        if (d.node_type === "entity") return "#1e1b4b";
        const theme = docColorMap.get(d.document_id) || DOC_COLORS[0];
        if (d.node_type === "document") return theme.primary;
        return theme.bg;
      })
      .attr("stroke", (d) => {
        if (d.node_type === "entity") return "#818cf8";
        const theme = docColorMap.get(d.document_id) || DOC_COLORS[0];
        return theme.light;
      })
      .attr("stroke-width", (d) => (d.node_type === "document" ? 2.5 : 1.5))
      .style("filter", (d) => {
        if (d.node_type === "entity") return "drop-shadow(0 0 8px rgba(129, 140, 248, 0.4))";
        const theme = docColorMap.get(d.document_id) || DOC_COLORS[0];
        return d.node_type === "document" ? `drop-shadow(0 0 12px ${theme.glow})` : "none";
      });

    // Node labels
    node
      .append("text")
      .text((d) => {
        if (d.node_type === "document") {
          return d.filename.length > 22 ? d.filename.slice(0, 20) + "..." : d.filename;
        }
        if (d.node_type === "entity") {
          return `${d.subject} (${d.fact_count || 1})`;
        }
        const label = `${d.subject || ""}: ${d.value || ""}`;
        return label.length > 22 ? label.slice(0, 20) + "..." : label;
      })
      .attr("x", (d) => (d.node_type === "document" ? 28 : d.node_type === "entity" ? 22 : 14))
      .attr("y", 4)
      .attr("fill", "#f8fafc")
      .attr("font-size", (d) => (d.node_type === "document" ? "12px" : d.node_type === "entity" ? "11px" : "10px"))
      .attr("font-weight", (d) => (d.node_type === "document" ? "700" : d.node_type === "entity" ? "600" : "500"))
      .style("pointer-events", "none")
      .style("text-shadow", "0 1px 4px rgba(0,0,0,0.8)");

    // Convex Hull calculator for Document Clusters
    const updateHulls = () => {
      if (!showClusterHulls || graphMode === "entity") {
        hullGroup.selectAll("path").remove();
        return;
      }

      const clusterMap = new Map();
      nodes.forEach((n) => {
        if (n.document_id) {
          if (!clusterMap.has(n.document_id)) clusterMap.set(n.document_id, []);
          if (n.x !== undefined && n.y !== undefined) {
            clusterMap.get(n.document_id).push([n.x, n.y]);
          }
        }
      });

      const hullData = [];
      clusterMap.forEach((points, docId) => {
        if (points.length >= 3) {
          const hull = d3.polygonHull(points);
          if (hull) {
            const theme = docColorMap.get(docId) || DOC_COLORS[0];
            hullData.push({ docId, hull, theme });
          }
        }
      });

      const hulls = hullGroup.selectAll("path").data(hullData, (d) => d.docId);

      hulls
        .enter()
        .append("path")
        .merge(hulls)
        .attr("d", (d) => `M${d.hull.join("L")}Z`)
        .attr("fill", (d) => d.theme.bg)
        .attr("stroke", (d) => d.theme.primary)
        .attr("stroke-width", 1.5)
        .attr("stroke-dasharray", "4,4")
        .attr("opacity", 0.35)
        .style("pointer-events", "none");

      hulls.exit().remove();
    };

    function linkPath(d) {
      if (d.link_type === "contains") {
        return `M${d.source.x},${d.source.y} L${d.target.x},${d.target.y}`;
      }
      const dx = d.target.x - d.source.x;
      const dy = d.target.y - d.source.y;
      const dr = Math.sqrt(dx * dx + dy * dy) * 1.2;
      return `M${d.source.x},${d.source.y}A${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
    }

    simulation.on("tick", () => {
      link.attr("d", linkPath);
      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
      updateHulls();
    });

    svg.on("click", () => {
      setSelectedNode(null);
      highlightNeighborsInD3(null);
    });

    // Save references for fast highlight manipulation without re-running simulation
    d3ElementsRef.current = {
      nodeSelection: node,
      linkSelection: link,
      links,
    };

    return () => {
      simulation.stop();
    };
  }, [
    processedGraph,
    searchQuery,
    docColorMap,
    showClusterHulls,
    graphMode,
    highlightNeighborsInD3,
  ]);

  // Update selected node border glow without restarting simulation
  useEffect(() => {
    if (!d3ElementsRef.current) return;
    const { nodeSelection } = d3ElementsRef.current;
    nodeSelection.select("circle")
      .attr("stroke", (d) => {
        if (selectedNode?.id === d.id) return "#fbbf24";
        if (d.node_type === "entity") return "#818cf8";
        const theme = docColorMap.get(d.document_id) || DOC_COLORS[0];
        return theme.light;
      })
      .attr("stroke-width", (d) => (selectedNode?.id === d.id ? 3.5 : d.node_type === "document" ? 2.5 : 1.5));
  }, [selectedNode, docColorMap]);

  // Graph controls
  const handleZoomIn = () => {
    if (svgRef.current && zoomBehaviorRef.current) {
      d3.select(svgRef.current).transition().duration(250).call(zoomBehaviorRef.current.scaleBy, 1.3);
    }
  };

  const handleZoomOut = () => {
    if (svgRef.current && zoomBehaviorRef.current) {
      d3.select(svgRef.current).transition().duration(250).call(zoomBehaviorRef.current.scaleBy, 0.75);
    }
  };

  const handleResetZoom = () => {
    if (svgRef.current && zoomBehaviorRef.current) {
      d3.select(svgRef.current).transition().duration(350).call(zoomBehaviorRef.current.transform, d3.zoomIdentity);
    }
  };

  const handleToggleSimulation = () => {
    if (!simulationRef.current) return;
    if (simulationRunning) {
      simulationRef.current.stop();
      setSimulationRunning(false);
    } else {
      simulationRef.current.alpha(0.3).restart();
      setSimulationRunning(true);
    }
  };

  const handleCopyFactJSON = (node) => {
    const payload = JSON.stringify(node, null, 2);
    navigator.clipboard.writeText(payload);
    setCopiedFactId(node.id);
    setTimeout(() => setCopiedFactId(null), 2000);
  };

  const connectedLinks = selectedNode
    ? processedGraph.links.filter((l) => {
        const sId = typeof l.source === "object" ? l.source.id : l.source;
        const tId = typeof l.target === "object" ? l.target.id : l.target;
        return sId === selectedNode.id || tId === selectedNode.id;
      })
    : [];

  return (
    <div className="graph-view">
      {/* View Header */}
      <div className="view-head">
        <div>
          <div className="view-title">
            <GraphIcon size={20} /> Document Knowledge Graph
          </div>
          <p className="view-desc">
            Grounding discovery: atomic facts, orbital document clusters, and cross-document reasoning bridges.
          </p>
        </div>

        {/* View Mode Switcher */}
        <div className="seg">
          <button
            className={`seg-btn ${graphMode === "orbit" ? "active" : ""}`}
            onClick={() => setGraphMode("orbit")}
            title="Full orbital clusters per document"
          >
            <LayersIcon size={14} /> Orbit Clusters
          </button>
          <button
            className={`seg-btn ${graphMode === "entity" ? "active" : ""}`}
            onClick={() => setGraphMode("entity")}
            title="Group facts into higher-level Entity & Metric Hubs"
          >
            <SparklesIcon size={14} /> Entity Hubs
          </button>
          <button
            className={`seg-btn ${graphMode === "bridges" ? "active" : ""}`}
            onClick={() => setGraphMode("bridges")}
            title="Focus strictly on cross-document relationship links"
          >
            <GraphIcon size={14} /> Reasoning Bridges
          </button>
        </div>
      </div>

      {/* Document Cluster Switcher Ribbon */}
      <div className="doc-cluster-ribbon card">
        <button
          className={`cluster-pill-btn ${activeDocId === "all" ? "active" : ""}`}
          onClick={() => setActiveDocId("all")}
        >
          <LayersIcon size={14} />
          <span>All Document Clusters</span>
          <span className="pill-count">{documents.length}</span>
        </button>

        <div className="cluster-pills-list">
          {docClustersSummary.map(({ document: d, color, factCount, contradictCount }) => {
            const isActive = activeDocId === d.id;
            return (
              <button
                key={d.id}
                className={`cluster-pill-btn ${isActive ? "active" : ""}`}
                style={{
                  borderColor: isActive ? color.primary : undefined,
                  boxShadow: isActive ? `0 0 12px ${color.glow}` : undefined,
                }}
                onClick={() => setActiveDocId(d.id)}
              >
                <span className="doc-color-indicator" style={{ background: color.primary }} />
                <span className="pill-doc-name">{d.filename}</span>
                <span className="pill-count">{factCount} facts</span>
                {contradictCount > 0 && (
                  <span className="pill-alert-dot" title={`${contradictCount} contradiction(s)`}>
                    {contradictCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Control bar */}
      <div className="graph-control-bar card">
        <div className="filter-group">
          <div className="field-wrap" style={{ minWidth: "220px" }}>
            <SearchIcon size={14} />
            <input
              type="text"
              placeholder="Search facts, subjects, metrics..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="field"
            />
          </div>

          <div className="select-wrapper">
            <select
              value={relFilter}
              onChange={(e) => setRelFilter(e.target.value)}
              className="field"
            >
              <option value="all">All Relationships</option>
              <option value="corroborate">Corroborate Only</option>
              <option value="contradict">Contradict Only</option>
              <option value="context_reconciled">Context Reconciled Only</option>
            </select>
          </div>

          <label className="check text-xs">
            <input
              type="checkbox"
              checked={showClusterHulls}
              onChange={(e) => setShowClusterHulls(e.target.checked)}
            />
            Document Orbital Hulls
          </label>
        </div>

        <div className="graph-legend">
          <span className="legend-item"><span className="legend-dot doc-dot"></span> Doc Hub</span>
          <span className="legend-item"><span className="legend-dot fact-dot"></span> Fact</span>
          <span className="legend-item"><span className="legend-line corrob-line"></span> Corroborate</span>
          <span className="legend-item"><span className="legend-line contrad-line"></span> Contradict</span>
          <span className="legend-item"><span className="legend-line reconc-line"></span> Reconciled</span>
        </div>

        <div className="toolbar-actions">
          <button className="btn btn--icon btn--outline btn--sm" onClick={handleZoomIn} title="Zoom In">
            <ZoomInIcon size={14} />
          </button>
          <button className="btn btn--icon btn--outline btn--sm" onClick={handleZoomOut} title="Zoom Out">
            <ZoomOutIcon size={14} />
          </button>
          <button className="btn btn--icon btn--outline btn--sm" onClick={handleResetZoom} title="Reset View">
            <ResetIcon size={14} />
          </button>
          <button
            className={`btn btn--icon btn--outline btn--sm ${simulationRunning ? "active" : ""}`}
            onClick={handleToggleSimulation}
            title={simulationRunning ? "Pause Physics Simulation" : "Resume Physics Simulation"}
          >
            {simulationRunning ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
          </button>
        </div>
      </div>

      {/* Main Canvas & Details Split */}
      <div className="graph-workspace">
        <div className="graph-canvas-container card">
          {loading && (
            <div className="loading-state-overlay">
              <div className="spinner"></div>
              <p>Constructing document-wise knowledge graph...</p>
            </div>
          )}
          <svg ref={svgRef} className="graph-svg" />

          {/* Floating Hover Tooltip (pure DOM, zero React re-render) */}
          <div ref={tooltipRef} className="graph-hover-tooltip" style={{ display: "none" }} />
        </div>

        {/* Selected Node Details Drawer */}
        {selectedNode && (
          <div className="node-inspector-drawer card">
            <div className="drawer-header">
              <div className="drawer-title-row">
                <span className={`badge ${selectedNode.node_type === "document" ? "badge-doc" : selectedNode.node_type === "entity" ? "badge-entity" : "badge-fact"}`}>
                  {selectedNode.node_type.toUpperCase()}
                </span>
                <div className="drawer-actions-right">
                  {selectedNode.node_type === "fact" && (
                    <button
                      className="btn-icon-xs"
                      title="Copy fact JSON"
                      onClick={() => handleCopyFactJSON(selectedNode)}
                    >
                      {copiedFactId === selectedNode.id ? <CheckIcon size={13} className="text-green" /> : <CopyIcon size={13} />}
                    </button>
                  )}
                  <button className="btn-close" onClick={() => {
                    setSelectedNode(null);
                    highlightNeighborsInD3(null);
                  }}>
                    <CloseIcon size={14} />
                  </button>
                </div>
              </div>
              <h3>{selectedNode.label}</h3>
            </div>

            <div className="drawer-body">
              {selectedNode.node_type === "document" ? (
                <div className="doc-inspector">
                  <div className="inspector-field">
                    <span className="field-label">Document Filename</span>
                    <span className="field-value font-semibold">{selectedNode.filename}</span>
                  </div>
                  <div className="inspector-field">
                    <span className="field-label">Total Pages</span>
                    <span className="field-value">{selectedNode.page_count ?? "—"}</span>
                  </div>
                  <div className="inspector-field">
                    <span className="field-label">Status</span>
                    <span className={`status-badge status-${selectedNode.status}`}>{selectedNode.status}</span>
                  </div>
                </div>
              ) : selectedNode.node_type === "entity" ? (
                <div className="entity-inspector">
                  <div className="inspector-field">
                    <span className="field-label">Entity / Subject</span>
                    <span className="field-value font-semibold">{selectedNode.subject}</span>
                  </div>
                  <div className="inspector-field">
                    <span className="field-label">Total Facts Extracted</span>
                    <span className="field-value highlight font-bold">{selectedNode.fact_count} facts</span>
                  </div>
                  <div className="inspector-field">
                    <span className="field-label">Documents Mentioning</span>
                    <span className="field-value">{selectedNode.document_count} documents</span>
                  </div>
                  <div className="connected-relationships-section">
                    <h4>Associated Facts & Evidence</h4>
                    <div className="facts-mini-list">
                      {selectedNode.facts?.slice(0, 10).map((f) => (
                        <div
                          key={f.id}
                          className="fact-mini-item"
                          onClick={() => {
                            setSelectedNode(f);
                            highlightNeighborsInD3(f.id);
                          }}
                        >
                          <span className="font-semibold text-xs">{f.predicate}:</span>{" "}
                          <span className="highlight text-xs font-bold">{f.value} {f.unit || ""}</span>
                          <span className="text-muted text-xs block">{f.filename} (Page {(f.pdf_page_index ?? 0) + 1})</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="fact-inspector">
                  <div className="inspector-field">
                    <span className="field-label">Subject</span>
                    <span className="field-value font-semibold">{selectedNode.subject}</span>
                  </div>
                  <div className="inspector-field">
                    <span className="field-label">Predicate / Metric</span>
                    <span className="field-value">{selectedNode.predicate}</span>
                  </div>
                  <div className="inspector-field">
                    <span className="field-label">Extracted Value</span>
                    <span className="field-value highlight font-bold">
                      {selectedNode.value} {selectedNode.unit || ""}
                    </span>
                  </div>
                  {selectedNode.time_scope && (
                    <div className="inspector-field">
                      <span className="field-label">Time Scope</span>
                      <span className="field-value">{selectedNode.time_scope}</span>
                    </div>
                  )}
                  <div className="inspector-field">
                    <span className="field-label">Grounding Location</span>
                    <span className="field-value">
                      {selectedNode.filename} — <strong>Page {(selectedNode.pdf_page_index ?? 0) + 1}</strong>
                    </span>
                  </div>

                  <div className="evidence-quote-box">
                    <span className="quote-label">Exact Verbatim Grounding:</span>
                    <p className="quote-text">"{selectedNode.evidence_text}"</p>
                  </div>

                  {onNavigateToDocPage && (
                    <button
                      className="btn btn--primary btn--sm w-full mt-3"
                      onClick={() =>
                        onNavigateToDocPage(
                          selectedNode.document_id,
                          selectedNode.pdf_page_index,
                          selectedNode.fact_id || selectedNode.id.replace("fact-", "")
                        )
                      }
                    >
                      <EyeIcon size={14} /> View Evidence on Source PDF Page
                    </button>
                  )}

                  {/* Connected Relationships with Reasoner Explanation */}
                  <div className="connected-relationships-section">
                    <h4>Cross-Document Reasoning ({connectedLinks.filter((l) => l.link_type !== "contains").length})</h4>
                    <div className="rel-list">
                      {connectedLinks
                        .filter((l) => l.link_type !== "contains")
                        .map((link, idx) => (
                          <div key={idx} className={`rel-mini-card rel-border-${link.link_type}`}>
                            <div className="rel-mini-header">
                              <span className={`badge badge-${link.link_type}`}>
                                {link.link_type.replace("_", " ")}
                              </span>
                              {link.similarity_score && (
                                <span className="text-xs text-muted">
                                  {Math.round(link.similarity_score * 100)}% sim
                                </span>
                              )}
                            </div>
                            <p className="rel-mini-explanation">{link.explanation}</p>
                            {link.reconciliation_factor && (
                              <p className="rel-mini-reconciliation text-xs text-blue">
                                <strong>Factor:</strong> {link.reconciliation_factor}
                              </p>
                            )}
                          </div>
                        ))}
                      {connectedLinks.filter((l) => l.link_type !== "contains").length === 0 && (
                        <p className="text-xs text-muted">No cross-document comparison links for this fact.</p>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
