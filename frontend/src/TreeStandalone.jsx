import React, { useEffect, useRef, useState } from "react";
import { phylotree, computeMidpoint } from "phylotree";
import "phylotree/dist/phylotree.css";
import { cladeSamples, resolveClickTarget, stripZc, styleable } from "./clade.js";
import {
  ZOOM_STEP, ZOOM_MIN, ZOOM_MAX,
  applyZoomStyles, attachZoom, restoreZoom, zoomBy as zoomDisplayBy, zoomReset as zoomDisplayReset,
} from "./zoom.js";

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
  const [zoomK, setZoomK] = useState(1);

  // Clade selection is the DEFAULT way a branch click behaves, not a mode to
  // find and switch on. It was a checkbox first, and the first thing that
  // happened is what always happens with a hidden mode: clicking the tree did
  // nothing and the feature read as broken. Rerooting stays an explicit opt-in
  // and takes the click while it is on — it is the destructive-looking one.
  const cladePick = tables.length > 0 && !rerootMode;

  const treeRef = useRef(null);
  const containerRef = useRef(null);
  // The TreeRender phylotree builds per render(): it owns the d3 zoom behaviour
  // and the SVG selection the +/− buttons have to drive.
  const displayRef = useRef(null);
  // The live zoom transform, kept outside React state so a rebuild can restore
  // it (a new render() means a new TreeRender, with the zoom back at identity).
  const zoomRef = useRef(null);
  const originalNewickRef = useRef("");
  const rerootModeRef = useRef(false);
  const cladePickRef = useRef(false);
  const selectedSetRef = useRef(new Set());
  const searchTermRef = useRef("");
  // The delegated click listener is bound ONCE, so anything it calls must be
  // reached through a ref. Calling `render` directly from it captured the
  // mount-time closure: a reroot then re-rendered with the ORIGINAL Bootstrap,
  // Search and Strip-suffix values, silently undoing all three while their
  // checkboxes still showed the user's choices.
  const renderRef = useRef(() => {});

  useEffect(() => { rerootModeRef.current = rerootMode; }, [rerootMode]);
  useEffect(() => { cladePickRef.current = cladePick; }, [cladePick]);

  // Repaint tip labels for the current selection and search, IN PLACE.
  //
  // Selecting a clade used to be in the render effect's dependencies, so every
  // click rebuilt the whole SVG — which throws away the user's zoom and pan,
  // because phylotree keeps the zoom transform on the TreeRender object and
  // `tree.render()` constructs a new one. On the 1,044-tip tree zooming in is
  // the only way to read tip labels, so clicking the clade you had just found
  // snapped the tree back to fit-to-size. Colour is all that changes here, and
  // colour can be set on the nodes that are already on screen.
  function paintTips() {
    const el = containerRef.current;
    if (!el) return;
    const selected = selectedSetRef.current;
    const term = (searchTermRef.current || "").toLowerCase();
    for (const g of el.querySelectorAll("g.node")) {
      const d = g.__data__;
      const name = (d && d.data && d.data.name) || "";
      const text = g.querySelector("text");
      if (!text) continue;
      const isSelected = selected.has(stripZc(name));
      const isHit = term && name.toLowerCase().includes(term);
      // Search wins, so a hit inside the selected clade stays findable.
      text.style.fill = isHit ? "#d62728" : (isSelected ? "#1d5fbf" : "");
      text.style.fontWeight = isHit || isSelected ? "bold" : "";
    }
  }

  useEffect(() => {
    selectedSetRef.current = new Set(selectedTips.map((t) => stripZc(t)));
    paintTips();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTips]);

  useEffect(() => {
    searchTermRef.current = searchTerm;
    paintTips();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTerm]);

  // The zoom mechanics live in zoom.js; these are the two buttons' worth of
  // wiring. Everything goes through displayRef because each render() replaces
  // the TreeRender that owns the zoom behaviour.
  function zoomIn(factor) {
    const el = containerRef.current;
    if (!el) return;
    // About the middle of what the user can SEE. d3 measures the zoom point in
    // the SVG's user units; with no viewBox and fit-to-size spacing those are
    // CSS pixels from the container's top-left corner, so the centre of the
    // view is just half the container.
    const p = [(el.clientWidth || 0) / 2, (el.clientHeight || 0) / 2];
    try {
      zoomDisplayBy(displayRef.current, factor, p);
    } catch (e) {
      setStatus(`Zoom failed: ${e && e.message ? e.message : e}`);
    }
  }

  function zoomFit() {
    try {
      zoomDisplayReset(displayRef.current);
    } catch (e) {
      setStatus(`Zoom reset failed: ${e && e.message ? e.message : e}`);
    }
  }

  // Keyboard, for the same reason the buttons exist: a trackpad pinch is not a
  // reliable way to zoom. Modified keys are left alone so browser zoom and
  // shortcuts still work, and typing in the tip search never zooms the tree.
  useEffect(() => {
    function onKey(ev) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const tag = ev.target && ev.target.tagName ? ev.target.tagName.toLowerCase() : "";
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (ev.key === "+" || ev.key === "=") { ev.preventDefault(); zoomIn(ZOOM_STEP); }
      else if (ev.key === "-" || ev.key === "_") { ev.preventDefault(); zoomIn(1 / ZOOM_STEP); }
      else if (ev.key === "0") { ev.preventDefault(); zoomFit(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function render(tree, keepZoom = false) {
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
      },
      "edge-styler": (element) => {
        if (!styleable(element)) return;
        if (rerootModeRef.current || cladePickRef.current) element.style("cursor", "pointer");
      },
    });
    displayRef.current = display;
    const svgNode = display.show ? display.show() : null;
    if (svgNode) {
      svgNode.style.width = "100%";
      svgNode.style.height = "100%";
      containerRef.current.appendChild(svgNode);
      // Selection and search highlighting is applied after the SVG is in the
      // DOM, by paintTips, so that changing either does not require a rebuild.
      paintTips();
    } else {
      setStatus("phylotree returned no SVG node");
    }

    // phylotree creates the zoom behaviour inside render(), so it exists by now.
    attachZoom(display, (t) => {
      zoomRef.current = { k: t.k, x: t.x, y: t.y };
      applyZoomStyles(containerRef.current, t.k);
      setZoomK(t.k);
    });
    let restored = false;
    if (keepZoom && zoomRef.current) {
      try {
        restored = restoreZoom(display, zoomRef.current);
      } catch { /* fall through to the fit-to-size view */ }
    }
    if (!restored) {
      zoomRef.current = null;
      applyZoomStyles(containerRef.current, 1);
      setZoomK(1);
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
          // Through the ref: a direct call would use the closure this listener
          // was bound with, which is the mount-time one.
          renderRef.current(treeRef.current);
        } catch (e) {
          setStatus(`Reroot failed: ${e && e.message ? e.message : e}`);
        }
        return;
      }
      if (!cladePickRef.current) return;
      const s = cladeSamples(hit.node);
      if (s.length) {
        setStatus("");
        setSelectedTips(s);
      } else {
        // The reference tip is the one thing on the tree that is not a sample.
        // Silence here is indistinguishable from a missed click, which is the
        // symptom this whole feature was reported for.
        setStatus("That is the reference (root) tip, not a sample — "
                  + "click a branch or node within the tree.");
      }
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
        // Two counts, because they differ by the reference tip: `leaves` is what
        // the header reports about the tree, `samples` is how many of them a
        // clade selection can ever contain. Comparing a selection against
        // `leaves` was always off by one on a rooted tree, so the "(the whole
        // tree)" note never appeared on a real vSNP3 tree.
        const sampleTips = tips.filter((t) => {
          const n = (t && t.data && t.data.name) || "";
          return n && n.toLowerCase() !== "root";
        });
        setCounts({ leaves: tips.length || 0, samples: sampleTips.length || 0 });
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
        if (cancelled) return;
        if (!res.ok) {
          // Do NOT fail silently. Clade picking is gated on this list, so an
          // unreachable or refusing endpoint disables the feature entirely —
          // and a swallowed error looked exactly like "this group has no SNP
          // tables": no bar, no pointer, clicking the tree does nothing, no
          // explanation. That is the original bug report, reconstituted.
          setStatus(`Could not list this tree's SNP tables (HTTP ${res.status}) — `
                    + "clade selection is unavailable.");
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        const list = (data && data.tables) || [];
        setTables(list);
        if (list.length) setTableChoice(list[0].path);
      } catch (err) {
        if (!cancelled) {
          setStatus("Could not list this tree's SNP tables "
                    + `(${err && err.message ? err.message : err}) — `
                    + "clade selection is unavailable.");
        }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the ref pointing at the CURRENT render closure, for the once-bound
  // click listener.
  useEffect(() => { renderRef.current = render; });

  // Only settings that change what is DRAWN force a rebuild. Selection and
  // search changed colour only and used to be in here, which is what made every
  // clade click discard the user's zoom and pan; they are painted in place now.
  useEffect(() => {
    // keepZoom: none of these change the tree's topology, so throwing away the
    // user's zoom and pan to redraw the same tree with bootstrap labels on is
    // pure loss.
    if (treeRef.current) render(treeRef.current, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showBootstrap, rerootMode, cladePick, stripSuffix]);

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
      if (!res.ok) {
        // The backend's own words, not just a status code: "Selection is too
        // large" and "No sample names given" both arrive this way and both say
        // what to do about it.
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body && body.detail) detail = body.detail;
        } catch { /* not JSON — the status is all there is */ }
        throw new Error(detail);
      }
      const data = await res.json();
      const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/preview-xlsx?path=${encodeURIComponent(tableChoice)}&selection=${encodeURIComponent(data.token)}`;
      if (win) {
        win.location = url;
      } else {
        // The tab was blocked. A second window.open here is outside the click
        // gesture and gets blocked too, so say so instead of doing nothing —
        // silence after pressing a button is the failure users report as "it
        // doesn't work".
        setStatus("Your browser blocked the new tab. Allow pop-ups for this "
                  + "site, then press the button again.");
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
              {/* Against the SAMPLE count, not the leaf count: every vSNP3 tree
                  carries a `root` reference tip that a clade never includes, so
                  comparing with `leaves` was off by one and this never showed. */}
              {counts.samples && selectedTips.length >= counts.samples
                ? <span style={{ color: "#666" }}>(the whole tree)</span> : null}
              {/* The mode cue has to be visible here too. It used to live only
                  in the no-selection branch, so ticking Reroot mode with a clade
                  selected changed nothing on screen and the next branch click
                  quietly rerooted instead of selecting. */}
              {rerootMode
                ? <span style={{ color: "#8a5a10" }}>Reroot mode is on — a branch click reroots.</span>
                : null}
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
      {/* The zoom buttons live OUTSIDE the tree container on purpose: render()
          clears that element's innerHTML on every redraw, and a control inside
          it would be wiped out with the tree. */}
      <div style={{ position: "relative", flex: 1, minHeight: 0, display: "flex" }}>
        <div
          ref={containerRef}
          className={cladePick ? "tree-canvas tree-pick" : "tree-canvas"}
          style={{ flex: 1, minWidth: 0, overflow: "auto", background: "#fff" }}
        />
        <div className="tree-zoom">
          <button onClick={() => zoomIn(ZOOM_STEP)} disabled={zoomK >= ZOOM_MAX * 0.999}
                  title="Zoom in (keyboard: +)" aria-label="Zoom in">+</button>
          <span className="tree-zoom-level" title="Current zoom">
            {zoomK >= 10 ? zoomK.toFixed(0) : zoomK.toFixed(1)}×
          </span>
          <button onClick={() => zoomIn(1 / ZOOM_STEP)} disabled={zoomK <= ZOOM_MIN * 1.001}
                  title="Zoom out (keyboard: −)" aria-label="Zoom out">−</button>
          <button className="tree-zoom-fit" onClick={zoomFit}
                  title="Back to the whole tree (keyboard: 0)">Fit</button>
        </div>
      </div>
    </div>
  );
}
