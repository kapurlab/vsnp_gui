import React, { useEffect, useRef, useState } from "react";
import { phylotree, computeMidpoint } from "phylotree";
import "phylotree/dist/phylotree.css";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

export default function TreePtStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const path = params.get("path") || "";

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  const [showBootstrap, setShowBootstrap] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [rerootMode, setRerootMode] = useState(false);
  const [counts, setCounts] = useState({ leaves: 0 });

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  const originalNewickRef = useRef("");
  const rerootModeRef = useRef(false);

  useEffect(() => { rerootModeRef.current = rerootMode; }, [rerootMode]);

  function render(tree) {
    if (!containerRef.current) return;
    containerRef.current.innerHTML = "";
    const width = Math.max(400, containerRef.current.clientWidth || 800);
    const height = Math.max(400, containerRef.current.clientHeight || 600);
    const display = tree.render({
      container: containerRef.current,
      "left-right-spacing": "fit-to-size",
      "top-bottom-spacing": "fit-to-size",
      width,
      height,
      "show-scale": "top",
      "draw-size-bubbles": false,
      "internal-names": showBootstrap,
      selectable: false,
      collapsible: false,
      brush: false,
      zoom: true,
      "node-styler": (element, node) => {
        if (!node.children) {
          const name = (node.data && node.data.name) || "";
          if (searchTerm && name.toLowerCase().includes(searchTerm.toLowerCase())) {
            element.select("text").style("fill", "#d62728").style("font-weight", "bold");
          }
        }
      },
      "edge-styler": (element, edge) => {
        if (rerootModeRef.current) element.style("cursor", "pointer");
        element.on("click.spike-reroot", () => {
          if (!rerootModeRef.current) return;
          try {
            tree.reroot(edge.target);
            render(tree);
          } catch (e) {
            setStatus(`Reroot failed: ${e && e.message ? e.message : e}`);
          }
        });
      },
    });
    const svgNode = display.show ? display.show() : null;
    if (svgNode) {
      svgNode.style.width = "100%";
      svgNode.style.height = "100%";
      containerRef.current.appendChild(svgNode);
    } else {
      setStatus("phylotree returned no SVG node");
    }
  }

  useEffect(() => {
    if (!project || !path) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(serveUrl(project, path));
        if (!res.ok) {
          setStatus(`Fetch failed: HTTP ${res.status}`);
          return;
        }
        const newick = (await res.text()).trim();
        if (cancelled) return;
        originalNewickRef.current = newick;
        const tree = new phylotree(newick);
        treeRef.current = tree;
        const tips = tree.getTips ? tree.getTips() : [];
        setCounts({ leaves: tips.length || 0 });
        render(tree);
        setStatus("");
      } catch (err) {
        setStatus(`Load failed: ${err && err.message ? err.message : err}`);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (treeRef.current) render(treeRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showBootstrap, searchTerm, rerootMode]);

  function midpointRoot() {
    if (!treeRef.current) return;
    try {
      const mid = computeMidpoint(treeRef.current);
      if (mid && mid.location) {
        treeRef.current.reroot(mid.location, mid.breakpoint || 0);
        render(treeRef.current);
      } else {
        setStatus("Midpoint not computable on this tree.");
      }
    } catch (err) {
      setStatus(`Midpoint failed: ${err && err.message ? err.message : err}`);
    }
  }

  function resetRoot() {
    if (!originalNewickRef.current) return;
    try {
      const tree = new phylotree(originalNewickRef.current);
      treeRef.current = tree;
      render(tree);
    } catch (err) {
      setStatus(`Reset failed: ${err && err.message ? err.message : err}`);
    }
  }

  function downloadTre() {
    if (!project || !path) return;
    const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/download-file?path=${encodeURIComponent(path)}`;
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  useEffect(() => {
    document.title = `Tree (phylotree.js) · ${project || "?"}`;
  }, [project]);

  const filename = path ? path.split("/").pop() : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      <div style={{ padding: "0.5rem 0.8rem", borderBottom: "1px solid #ddd", background: "#f7f7f7", display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
        <strong>phylotree.js spike</strong>
        <span style={{ color: "#666", fontSize: "0.9em" }}>
          {filename}{counts.leaves ? ` · ${counts.leaves} leaves` : ""}{project ? ` · ${project}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <input placeholder="Search tip…" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} style={{ width: 180 }} />
        <label><input type="checkbox" checked={showBootstrap} onChange={(e) => setShowBootstrap(e.target.checked)} /> Bootstrap</label>
        <label><input type="checkbox" checked={rerootMode} onChange={(e) => setRerootMode(e.target.checked)} /> Reroot mode (click branch)</label>
        <button onClick={midpointRoot}>Midpoint</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "auto", background: "#fff" }} />
    </div>
  );
}
