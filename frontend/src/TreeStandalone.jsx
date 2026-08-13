import React, { useEffect, useRef, useState } from "react";
import { phylotree, computeMidpoint } from "phylotree";
import "phylotree/dist/phylotree.css";
import { cladeSamples, resolveClickTarget, stripZc, styleable } from "./clade.js";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}


export default function TreeStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const path = params.get("path") || "";

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  const [showBootstrap, setShowBootstrap] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [rerootMode, setRerootMode] = useState(false);
  const [stripSuffix, setStripSuffix] = useState(true);
  const [counts, setCounts] = useState({ leaves: 0 });
  // SNP tables living beside this tree (same Step 2 group directory); empty
  // until the backend answers, in which case the clade UI stays hidden.
  const [tables, setTables] = useState([]);
  const [tableChoice, setTableChoice] = useState("");
  const [selectedTips, setSelectedTips] = useState([]);
  const [opening, setOpening] = useState(false);

  // Clade selection is the DEFAULT way a branch click behaves, not a mode to
  // find and switch on. It was a checkbox first, and the first thing that
  // happened is what always happens with a hidden mode: clicking the tree did
  // nothing and the feature read as broken. Rerooting stays an explicit opt-in
  // and takes the click while it is on — it is the destructive-looking one.
  const cladePick = tables.length > 0 && !rerootMode;

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  const originalNewickRef = useRef("");
  const rerootModeRef = useRef(false);
  const cladePickRef = useRef(false);
  const selectedSetRef = useRef(new Set());

  useEffect(() => { rerootModeRef.current = rerootMode; }, [rerootMode]);
  useEffect(() => { cladePickRef.current = cladePick; }, [cladePick]);
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
        if (!styleable(element)) return;
        const data = (node && node.data) || {};
        // The node itself is a clade-selection target too. A branch is an SVG
        // path with no fill, so it answers pointer events only on its painted
        // stroke — a ~2px line to hit. The node marker at the fork is a far
        // easier target for the same clade, and it is where a reader's eye
        // already is when they mean "this group".
        if (cladePickRef.current) element.style("cursor", "pointer");
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
        if (!styleable(element)) return;
        if (rerootModeRef.current || cladePickRef.current) element.style("cursor", "pointer");
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

  // ONE delegated click listener for the whole tree, bound once to the
  // container — not a handler per branch.
  //
  // phylotree's own branch handler calls modifySelection() + update(), which
  // re-renders and REPLACES every branch path on each click. Per-element
  // listeners therefore live on elements that are about to be discarded, and
  // whether ours runs at all comes down to listener ordering; they also have to
  // be re-attached on every redraw. Delegation sidesteps all of it — the
  // container outlives every render — and it is bound in the CAPTURE phase so
  // the clade is read out of the DOM before phylotree starts rebuilding it.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    function onClick(ev) {
      const hit = resolveClickTarget(ev.target, el);
      if (!hit) return;
      if (!rerootModeRef.current && !cladePickRef.current) return;
      // Having handled it, keep it from reaching phylotree's own click
      // handlers. Theirs call modifySelection() + update() — a full redraw we
      // then redraw over — and its node handler opens a dropdown menu this
      // viewer does not use (selectable/collapsible are off) whose code throws
      // "Invalid selector" here anyway. Zooming is wheel/drag, so it is
      // unaffected.
      ev.stopPropagation();
      if (rerootModeRef.current) {
        if (hit.kind !== "edge" || !treeRef.current) return;
        try {
          treeRef.current.reroot(hit.node);
          render(treeRef.current);
        } catch (e) {
          setStatus(`Reroot failed: ${e && e.message ? e.message : e}`);
        }
        return;
      }
      if (!cladePickRef.current) return;
      const s = cladeSamples(hit.node);
      if (s.length) setSelectedTips(s);
    }
    el.addEventListener("click", onClick, true);
    return () => el.removeEventListener("click", onClick, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  }, [showBootstrap, searchTerm, rerootMode, cladePick, stripSuffix, selectedTips]);

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
        <label title={tables.length ? "While this is on, a branch click reroots the tree instead of selecting a clade." : ""}>
          <input type="checkbox" checked={rerootMode} onChange={(e) => setRerootMode(e.target.checked)} /> Reroot mode (click branch)
        </label>
        <button onClick={midpointRoot}>Midpoint</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      {tables.length > 0 ? (
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
            // Stated before the first click, because the alternative is a user
            // clicking a tree that appears to do nothing. Kept to one line: what
            // to click, and what it gets you.
            <span style={{ color: "#2f4a2f" }}>
              {rerootMode ? (
                <>
                  <strong>Reroot mode is on.</strong> A branch click reroots the tree.
                  Untick it to select clades.
                </>
              ) : (
                <>
                  <strong>Select a clade:</strong> click any branch or node. This group's
                  SNP table can then be opened for those samples alone, showing only the
                  positions where they differ.
                </>
              )}
            </span>
          )}
        </div>
      ) : null}
      <div
        ref={containerRef}
        className={cladePick ? "tree-canvas tree-pick" : "tree-canvas"}
        style={{ flex: 1, overflow: "auto", background: "#fff" }}
      />
    </div>
  );
}
