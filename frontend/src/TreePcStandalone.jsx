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
  const [counts, setCounts] = useState({ leaves: 0 });
  const [shape, setShape] = useState("rectangular");

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  const originalNewickRef = useRef("");

  function computeStyles(tree, term) {
    if (!tree || !term) return {};
    const lc = term.toLowerCase();
    const out = {};
    const nodes = (tree.getNodes && tree.getNodes()) || [];
    for (const n of nodes) {
      if (n.isLeaf && n.label && n.label.toLowerCase().includes(lc)) {
        out[n.id] = { fillColour: "#d62728", labelColour: "#d62728" };
      }
    }
    return out;
  }

  function buildTree(source) {
    if (!containerRef.current) return;
    if (treeRef.current) {
      try { treeRef.current.destroy(); } catch (e) { /* ignore */ }
      treeRef.current = null;
      containerRef.current.innerHTML = "";
    }
    const tree = new PhylocanvasGL(containerRef.current, {
      source,
      type: shape === "circular" ? Shapes.Circular : Shapes.Rectangular,
      showLabels: true,
      showLeafLabels: true,
      showInternalLabels: showBootstrap,
      showBranchLengths: true,
    });
    treeRef.current = tree;
    const nodes = (tree.getNodes && tree.getNodes()) || [];
    const leaves = nodes.filter((n) => n.isLeaf).length;
    setCounts({ leaves });
    if (searchTerm) {
      try { tree.setProps({ styles: computeStyles(tree, searchTerm) }); } catch (e) { /* ignore */ }
    }
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
  }, [showBootstrap, shape]);

  useEffect(() => {
    if (treeRef.current) {
      try { treeRef.current.setProps({ styles: computeStyles(treeRef.current, searchTerm) }); } catch (e) { /* ignore */ }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTerm]);

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
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "hidden", background: "#fff" }} />
    </div>
  );
}
