import React, { useEffect, useRef, useState } from "react";
import PhylocanvasGL, { Shapes } from "@phylocanvas/phylocanvas.gl";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

export default function TreePcStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const path = params.get("path") || "";

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  const [showBootstrap, setShowBootstrap] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [rerootMode, setRerootMode] = useState(false);
  const [counts, setCounts] = useState({ leaves: 0 });
  const [shape, setShape] = useState("rectangular");

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  const originalNewickRef = useRef("");

  function buildTree(source) {
    if (treeRef.current) {
      try { treeRef.current.destroy(); } catch (e) { /* ignore */ }
      treeRef.current = null;
    }
    const tree = new PhylocanvasGL(
      containerRef.current,
      {
        source,
        showBranchLengths: true,
        showLabels: true,
        showLeafLabels: true,
        showInternalLabels: showBootstrap,
        showBootstrapValues: showBootstrap,
        type: shape === "circular" ? Shapes.Circular : Shapes.Rectangular,
        size: { width: containerRef.current.clientWidth || 800, height: containerRef.current.clientHeight || 600 },
        styles: searchTerm
          ? Object.fromEntries(
              (function () {
                const tr = new PhylocanvasGL(document.createElement("div"), { source });
                const ids = (tr.getNodes() || []).filter((n) => n.isLeaf && n.label && n.label.toLowerCase().includes(searchTerm.toLowerCase())).map((n) => n.id);
                try { tr.destroy(); } catch (e) {}
                return ids.map((id) => [id, { fillColour: "#d62728", labelColour: "#d62728" }]);
              })()
            )
          : {},
      },
    );
    tree.deck.setProps({
      onClick: (info) => {
        if (!rerootMode || !info || !info.object || !info.object.id) return;
        try {
          tree.setRoot(info.object.id);
        } catch (e) {
          setStatus(`Reroot failed: ${e && e.message ? e.message : e}`);
        }
      },
    });
    treeRef.current = tree;
    const nodes = tree.getNodes ? tree.getNodes() : [];
    const leaves = nodes.filter((n) => n.isLeaf).length;
    setCounts({ leaves });
  }

  useEffect(() => {
    if (!project || !path) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(serveUrl(project, path));
        if (!res.ok) { setStatus(`Fetch failed: HTTP ${res.status}`); return; }
        const newick = (await res.text()).trim();
        if (cancelled) return;
        originalNewickRef.current = newick;
        buildTree(newick);
        setStatus("");
      } catch (err) {
        setStatus(`Load failed: ${err && err.message ? err.message : err}`);
      }
    })();
    return () => {
      cancelled = true;
      if (treeRef.current) { try { treeRef.current.destroy(); } catch (e) {} treeRef.current = null; }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (originalNewickRef.current) buildTree(originalNewickRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showBootstrap, searchTerm, shape]);

  function midpointRoot() {
    if (!treeRef.current) return;
    try {
      treeRef.current.setProps({ rootMode: "midpoint" });
    } catch (err) {
      setStatus(`Midpoint failed: ${err && err.message ? err.message : err}`);
    }
  }

  function resetRoot() {
    if (originalNewickRef.current) buildTree(originalNewickRef.current);
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
    document.title = `Tree (phylocanvas.gl) · ${project || "?"}`;
  }, [project]);

  const filename = path ? path.split("/").pop() : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      <div style={{ padding: "0.5rem 0.8rem", borderBottom: "1px solid #ddd", background: "#f7f7f7", display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
        <strong>phylocanvas.gl spike</strong>
        <span style={{ color: "#666", fontSize: "0.9em" }}>
          {filename}{counts.leaves ? ` · ${counts.leaves} leaves` : ""}{project ? ` · ${project}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <input placeholder="Search tip…" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} style={{ width: 180 }} />
        <select value={shape} onChange={(e) => setShape(e.target.value)}>
          <option value="rectangular">Rectangular</option>
          <option value="circular">Circular</option>
        </select>
        <label><input type="checkbox" checked={showBootstrap} onChange={(e) => setShowBootstrap(e.target.checked)} /> Bootstrap</label>
        <label><input type="checkbox" checked={rerootMode} onChange={(e) => setRerootMode(e.target.checked)} /> Reroot mode (click node)</label>
        <button onClick={midpointRoot}>Midpoint</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "hidden", background: "#fff" }} />
    </div>
  );
}
