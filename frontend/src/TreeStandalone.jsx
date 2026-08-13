import React, { useEffect, useRef, useState } from "react";
import { phylotree, computeMidpoint } from "phylotree";
import "phylotree/dist/phylotree.css";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

// Tree tips may carry the vSNP3 `_zc.vcf` suffix; reduce to the sample name
// the way the display option does. The backend strips again on its side, so
// this only needs to be good, not perfect.
function stripZc(name) {
  const n = String(name || "");
  const low = n.toLowerCase();
  if (low.endsWith("_zc.vcf.gz")) return n.slice(0, -"_zc.vcf.gz".length);
  if (low.endsWith("_zc.vcf")) return n.slice(0, -"_zc.vcf".length);
  return n;
}

// All tip names under a node (the clade a clicked branch leads to).
function tipsUnder(node) {
  const out = [];
  if (!node) return out;
  if (typeof node.leaves === "function") {
    for (const leaf of node.leaves()) {
      const name = leaf && leaf.data && leaf.data.name;
      if (name) out.push(name);
    }
    return out;
  }
  (function walk(n) {
    if (!n) return;
    if (!n.children || !n.children.length) {
      const name = n.data && n.data.name;
      if (name) out.push(name);
      return;
    }
    n.children.forEach(walk);
  })(node);
  return out;
}

export default function TreeStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const path = params.get("path") || "";

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  const [showBootstrap, setShowBootstrap] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [rerootMode, setRerootMode] = useState(false);
  const [cladeMode, setCladeMode] = useState(false);
  const [stripSuffix, setStripSuffix] = useState(true);
  const [counts, setCounts] = useState({ leaves: 0 });
  // SNP tables living beside this tree (same Step 2 group directory); empty
  // until the backend answers, in which case the clade UI stays hidden.
  const [tables, setTables] = useState([]);
  const [tableChoice, setTableChoice] = useState("");
  const [selectedTips, setSelectedTips] = useState([]);
  const [opening, setOpening] = useState(false);

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  const originalNewickRef = useRef("");
  const rerootModeRef = useRef(false);
  const cladeModeRef = useRef(false);
  const selectedSetRef = useRef(new Set());

  useEffect(() => { rerootModeRef.current = rerootMode; }, [rerootMode]);
  useEffect(() => { cladeModeRef.current = cladeMode; }, [cladeMode]);
  useEffect(() => {
    selectedSetRef.current = new Set(selectedTips.map((t) => stripZc(t)));
  }, [selectedTips]);

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
        const data = (node && node.data) || {};
        if (node && node.children) {
          // Internal node: phylotree renders data.name when internal-names is on.
          // Suppress any literal "root" label (synthetic wrapper or otherwise);
          // genuine bootstrap values are stored as numeric strings.
          if (data.name === "root") {
            element.select("text").text("");
          }
          return;
        }
        const name = data.name || "";
        if (stripSuffix && name.toLowerCase().endsWith("_zc.vcf")) {
          element.select("text").text(name.slice(0, -("_zc.vcf".length)));
        } else if (stripSuffix && name.toLowerCase().endsWith("_zc.vcf.gz")) {
          element.select("text").text(name.slice(0, -("_zc.vcf.gz".length)));
        }
        // Clade selection paints its tips; an active search still wins so a
        // hit inside the selected clade stays findable.
        if (selectedSetRef.current.has(stripZc(name))) {
          element.select("text").style("fill", "#1d5fbf").style("font-weight", "bold");
        }
        if (searchTerm && name.toLowerCase().includes(searchTerm.toLowerCase())) {
          element.select("text").style("fill", "#d62728").style("font-weight", "bold");
        }
      },
      "edge-styler": (element, edge) => {
        if (rerootModeRef.current || cladeModeRef.current) element.style("cursor", "pointer");
        element.on("click.tree-branch", () => {
          if (cladeModeRef.current) {
            const tips = tipsUnder(edge.target)
              .filter((t) => String(t).toLowerCase() !== "root");
            if (tips.length) setSelectedTips(tips);
            return;
          }
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
        // Phylotree's parser wraps the parsed tree in a synthetic outer node
        // named "root"; with internal-names on, that label is rendered as a
        // phantom "root" alongside any legit "root" outgroup leaf. Blank it.
        if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
          tree.nodes.data.name = "";
        }
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

  // The SNP tables that pair with this tree (xlsx siblings of the .tre). No
  // tables → the clade-to-table UI never appears; the viewer stays as it was.
  useEffect(() => {
    if (!project || !path) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/projects/${encodeURIComponent(project)}/tree-tables?path=${encodeURIComponent(path)}`
        );
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        const list = (data && data.tables) || [];
        setTables(list);
        if (list.length) setTableChoice(list[0].path);
      } catch {
        /* endpoint missing or unreachable: leave the clade UI hidden */
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (treeRef.current) render(treeRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showBootstrap, searchTerm, rerootMode, cladeMode, stripSuffix, selectedTips]);

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
      if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
        tree.nodes.data.name = "";
      }
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

  async function openSnpTable() {
    if (!selectedTips.length || !tableChoice || opening) return;
    // Open the tab synchronously (inside the click) so popup blockers stay
    // quiet, then point it at the filtered preview once the token exists.
    const win = window.open("", "_blank");
    setOpening(true);
    setStatus("");
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(project)}/snp-selection`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            samples: selectedTips.map((t) => stripZc(t)),
            source: path,
          }),
        }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/preview-xlsx?path=${encodeURIComponent(tableChoice)}&selection=${encodeURIComponent(data.token)}`;
      if (win) {
        win.location = url;
      } else {
        window.open(url, "_blank");
      }
    } catch (err) {
      if (win) win.close();
      setStatus(`Could not open the filtered table: ${err && err.message ? err.message : err}`);
    } finally {
      setOpening(false);
    }
  }

  useEffect(() => {
    const fname = path ? path.split("/").pop() : "";
    document.title = fname ? `Tree · ${fname}` : "Tree viewer";
  }, [path]);

  const filename = path ? path.split("/").pop() : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      <div style={{ padding: "0.5rem 0.8rem", borderBottom: "1px solid #ddd", background: "#f7f7f7", display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
        <strong>Tree viewer</strong>
        <span style={{ color: "#666", fontSize: "0.9em" }}>
          {filename}{counts.leaves ? ` · ${counts.leaves} leaves` : ""}{project ? ` · ${project}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <input placeholder="Search tip…" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} style={{ width: 180 }} />
        <label><input type="checkbox" checked={stripSuffix} onChange={(e) => setStripSuffix(e.target.checked)} /> Strip <code>_zc.vcf</code></label>
        <label><input type="checkbox" checked={showBootstrap} onChange={(e) => setShowBootstrap(e.target.checked)} /> Bootstrap</label>
        <label><input type="checkbox" checked={rerootMode} onChange={(e) => { setRerootMode(e.target.checked); if (e.target.checked) setCladeMode(false); }} /> Reroot mode (click branch)</label>
        {tables.length ? (
          <label title="Click a branch to select its clade, then open the group's SNP table filtered to those samples.">
            <input type="checkbox" checked={cladeMode} onChange={(e) => { setCladeMode(e.target.checked); if (e.target.checked) setRerootMode(false); }} /> Clade → SNP table (click branch)
          </label>
        ) : null}
        <button onClick={midpointRoot}>Midpoint</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      {tables.length && (cladeMode || selectedTips.length) ? (
        <div style={{ padding: "0.4rem 0.8rem", borderBottom: "1px solid #cfe0cf", background: "#eef6ee", display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap", fontSize: "0.92em" }}>
          {selectedTips.length ? (
            <>
              <strong>Clade: {selectedTips.length} sample{selectedTips.length === 1 ? "" : "s"}</strong>
              {selectedTips.length === counts.leaves ? <span style={{ color: "#666" }}>(the whole tree)</span> : null}
              <select value={tableChoice} onChange={(e) => setTableChoice(e.target.value)} style={{ maxWidth: 340 }}>
                {tables.map((t) => (
                  <option key={t.path} value={t.path}>
                    {t.name}{t.rows && t.cols ? ` (${t.rows.toLocaleString()} × ${t.cols.toLocaleString()})` : ""}
                  </option>
                ))}
              </select>
              <button onClick={openSnpTable} disabled={opening} title="Open this SNP table showing only the selected clade's samples and their SNP positions.">
                {opening ? "Opening…" : "Open SNP table for clade"}
              </button>
              <button onClick={() => setSelectedTips([])}>Clear</button>
            </>
          ) : (
            <span style={{ color: "#3a5a3a" }}>Click a branch to select its clade.</span>
          )}
        </div>
      ) : null}
      <div ref={containerRef} style={{ flex: 1, overflow: "auto", background: "#fff" }} />
    </div>
  );
}
