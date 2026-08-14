import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { phylotree, computeMidpoint } from "phylotree";
import { sampleTips, stripZc } from "./clade.js";
import { buildLayout, xAxis, tipNamesUnder, searchRows } from "./tree/layout.js";
import {
  fitView, clampView, zoomRows, zoomCols, panBy, revealRows, centreRow,
  LABEL_MIN_ROW_H, REVEAL_ROW_H, MAX_ROW_H,
} from "./tree/view.js";
import { drawTree, drawOverview, labelFontSize, PALETTE, THEME } from "./tree/draw.js";
import { pickAt } from "./tree/hit.js";

const API_BASE = import.meta.env.VITE_API_URL || ".";

const AXIS_H = 26;
const PAD_L = 10;
const OVERVIEW_W = 16;
const ZOOM_STEP = 1.6;

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

/**
 * The tree viewer.
 *
 * Drawn on a canvas rather than as SVG, and zoomed on two axes rather than one.
 * Both of those are answers to the same complaint — that a tree of a few
 * thousand tips could not be read at any zoom level, and that a sample you
 * searched for was highlighted somewhere you could not see. See tree/view.js
 * and tree/draw.js for why each of those follows from the rendering model.
 */
export default function TreeStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const path = params.get("path") || "";

  const [status, setStatus] = useState(project && path ? "Loading…" : "Missing project or path.");
  const [showBootstrap, setShowBootstrap] = useState(false);
  const [cladogram, setCladogram] = useState(false);
  const [stripSuffix, setStripSuffix] = useState(true);
  const [rerootMode, setRerootMode] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [hits, setHits] = useState([]);
  const [hitAt, setHitAt] = useState(0);
  const [tables, setTables] = useState([]);
  const [tableChoice, setTableChoice] = useState("");
  const [selection, setSelection] = useState(null); // {index, tips, range}
  const [opening, setOpening] = useState(false);
  const [layout, setLayout] = useState(null);
  const [readout, setReadout] = useState({ rowH: 0, rows: 0, labels: false });
  const [groups, setGroups] = useState([]); // {colour, range, n}

  const canvasRef = useRef(null);
  const overviewRef = useRef(null);
  const wrapRef = useRef(null);
  const treeRef = useRef(null);
  const originalNewickRef = useRef("");
  const layoutRef = useRef(null);
  const viewRef = useRef(null);
  const geomRef = useRef({ width: 0, height: 0, padL: PAD_L, padT: AXIS_H, plotW: 0, plotH: 0 });
  const selMaskRef = useRef(null);
  const colourMaskRef = useRef(null);
  const hoverRef = useRef(-1);
  const drawPendingRef = useRef(false);
  const dragRef = useRef(null);

  const displayName = useCallback(
    (n) => (stripSuffix ? stripZc(n) : n),
    [stripSuffix]
  );

  const xFor = useCallback(
    (lay) => xAxis(lay || layoutRef.current || { xLen: [], xTopo: [], maxX: 1 }, cladogram),
    [cladogram]
  );

  // ---- drawing ----------------------------------------------------------

  const paint = useCallback(() => {
    drawPendingRef.current = false;
    const lay = layoutRef.current;
    const view = viewRef.current;
    const canvas = canvasRef.current;
    if (!lay || !view || !canvas) return;
    const geom = geomRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { x } = xFor(lay);

    const searchSet = new Set(hits);
    drawTree(ctx, {
      layout: lay, view, geom, x,
      theme: THEME,
      displayName,
      selMask: selMaskRef.current,
      selRange: selection ? selection.range : null,
      searchRows: hits,
      searchRowSet: searchSet,
      currentRow: hits.length ? hits[Math.min(hitAt, hits.length - 1)] : null,
      colourMask: colourMaskRef.current,
      palette: PALETTE,
      hoverIndex: hoverRef.current,
      showInternalLabels: showBootstrap,
      cladogram,
      dense: view.rowH < 1.5,
    });

    const ov = overviewRef.current;
    if (ov) {
      const octx = ov.getContext("2d");
      if (octx) {
        drawOverview(octx, {
          layout: lay, view, geom,
          width: OVERVIEW_W, height: geom.height,
          selRange: selection ? selection.range : null,
          searchRows: hits,
          currentRow: hits.length ? hits[Math.min(hitAt, hits.length - 1)] : null,
          colourRanges: groups.map((g) => [g.range[0], g.range[1], g.colour]),
          theme: THEME,
        });
      }
    }

    const rows = geom.plotH / (view.rowH || 1);
    setReadout((prev) => {
      const next = {
        rowH: view.rowH,
        rows: Math.min(rows, lay.nLeaves),
        labels: view.rowH >= LABEL_MIN_ROW_H,
      };
      return Math.abs(prev.rowH - next.rowH) < 1e-6 && Math.abs(prev.rows - next.rows) < 0.5
        && prev.labels === next.labels ? prev : next;
    });
  }, [hits, hitAt, selection, showBootstrap, cladogram, displayName, groups, xFor]);

  const scheduleDraw = useCallback(() => {
    if (drawPendingRef.current) return;
    drawPendingRef.current = true;
    requestAnimationFrame(paint);
  }, [paint]);

  useEffect(() => { scheduleDraw(); }, [scheduleDraw]);

  const setView = useCallback((next) => {
    const lay = layoutRef.current;
    if (!lay || !next) return;
    viewRef.current = clampView(next, lay, geomRef.current, xFor(lay).max);
    scheduleDraw();
  }, [scheduleDraw, xFor]);

  // ---- sizing -----------------------------------------------------------

  // The label gutter is measured once per tree rather than per frame, and it
  // does NOT change with zoom: a gutter that appeared and vanished as labels
  // came and went would slide the whole tree sideways every time you crossed
  // the threshold.
  const measureGutter = useCallback((lay) => {
    const canvas = canvasRef.current;
    if (!canvas || !lay || !lay.nLeaves) return 80;
    const ctx = canvas.getContext("2d");
    if (!ctx) return 80;
    ctx.font = `${labelFontSize(MAX_ROW_H)}px system-ui, -apple-system, sans-serif`;
    let widest = 0;
    // A sample of tips, not all of them: on 4,611 names the difference in the
    // measurement is a few pixels and the difference in cost is 4,611 text
    // measurements on every reference change.
    const stride = Math.max(1, Math.floor(lay.nLeaves / 300));
    for (let r = 0; r < lay.nLeaves; r += stride) {
      const w = ctx.measureText(displayName(lay.names[lay.leafRows[r]]) || "").width;
      if (w > widest) widest = w;
    }
    return Math.max(60, Math.min(widest + 14, (geomRef.current.width || 800) * 0.42));
  }, [displayName]);

  const resize = useCallback((refit) => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    const lay = layoutRef.current;
    if (!wrap || !canvas) return;
    const width = Math.max(200, wrap.clientWidth - OVERVIEW_W);
    const height = Math.max(200, wrap.clientHeight);
    const dpr = window.devicePixelRatio || 1;
    for (const [c, w, h] of [[canvas, width, height], [overviewRef.current, OVERVIEW_W, height]]) {
      if (!c) continue;
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
      c.style.width = `${w}px`;
      c.style.height = `${h}px`;
      const cx = c.getContext("2d");
      if (cx) { cx.setTransform(1, 0, 0, 1, 0, 0); cx.scale(dpr, dpr); }
    }
    const gutter = lay ? measureGutter(lay) : 80;
    geomRef.current = {
      width, height, padL: PAD_L, padT: AXIS_H,
      plotW: Math.max(50, width - PAD_L - gutter),
      plotH: Math.max(50, height - AXIS_H - 6),
    };
    if (lay) {
      if (refit || !viewRef.current) {
        viewRef.current = fitView(lay, geomRef.current, xFor(lay).max);
      } else {
        viewRef.current = clampView(viewRef.current, lay, geomRef.current, xFor(lay).max);
      }
    }
    scheduleDraw();
  }, [measureGutter, scheduleDraw, xFor]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => resize(false));
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [resize]);

  // Switching between branch lengths and topology changes the x axis under a
  // view expressed in x units, so the old window is meaningless: refit x, keep
  // the vertical position the user had scrolled to.
  useEffect(() => {
    const lay = layoutRef.current;
    if (!lay || !viewRef.current) return;
    const keep = { rowH: viewRef.current.rowH, yTop: viewRef.current.yTop };
    const fitted = fitView(lay, geomRef.current, xFor(lay).max);
    setView({ ...fitted, ...keep });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cladogram]);

  useEffect(() => { resize(false); }, [stripSuffix, resize]);

  // ---- loading ----------------------------------------------------------

  const installTree = useCallback((tree, { refit = true } = {}) => {
    treeRef.current = tree;
    const lay = buildLayout(tree.nodes);
    layoutRef.current = lay;
    if (lay.noBranchLengths) setCladogram(true);
    selMaskRef.current = null;
    colourMaskRef.current = null;
    hoverRef.current = -1;
    setLayout(lay);
    setSelection(null);
    setGroups([]);
    resize(refit);
  }, [resize]);

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
        const tree = new phylotree(newick);
        // phylotree wraps the parsed tree in a synthetic outer node called
        // "root"; left alone it renders as a phantom label beside any genuine
        // `root` outgroup tip.
        if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
          tree.nodes.data.name = "";
        }
        installTree(tree);
        setStatus("");
      } catch (err) {
        setStatus(`Load failed: ${err && err.message ? err.message : err}`);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          // Never fail silently: clade picking is gated on this list, so an
          // endpoint that refuses disables the feature and looks exactly like
          // "clicking the tree does nothing".
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

  useEffect(() => {
    const fname = path ? path.split("/").pop() : "";
    document.title = fname ? `Tree · ${fname}` : "Tree viewer";
  }, [path]);

  // ---- selection --------------------------------------------------------

  const selectNode = useCallback((index) => {
    const lay = layoutRef.current;
    if (!lay || index == null || index < 0) return;
    const mask = new Uint8Array(lay.n);
    const stack = [index];
    while (stack.length) {
      const i = stack.pop();
      mask[i] = 1;
      for (let c = lay.childStart[i]; c < lay.childStart[i + 1]; c++) stack.push(lay.childIdx[c]);
    }
    selMaskRef.current = mask;
    const tips = sampleTips(tipNamesUnder(lay, index));
    if (!tips.length) {
      selMaskRef.current = null;
      setSelection(null);
      setStatus("That is the reference (root) tip, not a sample — "
                + "click a branch or node inside the tree.");
      scheduleDraw();
      return;
    }
    setStatus("");
    setSelection({
      index,
      tips,
      range: [Math.round(lay.rowMin[index]), Math.round(lay.rowMax[index])],
    });
  }, [scheduleDraw]);

  const clearSelection = useCallback(() => {
    selMaskRef.current = null;
    setSelection(null);
  }, []);

  // ---- pointer ----------------------------------------------------------

  const localPoint = (ev) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return [ev.clientX - rect.left, ev.clientY - rect.top];
  };

  const onPointerDown = (ev) => {
    if (ev.button !== 0) return;
    const [px, py] = localPoint(ev);
    dragRef.current = { px, py, startPx: px, startPy: py, moved: false };
    canvasRef.current.setPointerCapture?.(ev.pointerId);
  };

  const onPointerMove = (ev) => {
    const lay = layoutRef.current;
    const view = viewRef.current;
    if (!lay || !view) return;
    const [px, py] = localPoint(ev);
    const drag = dragRef.current;
    if (drag) {
      const dx = px - drag.px;
      const dy = py - drag.py;
      if (Math.abs(px - drag.startPx) > 3 || Math.abs(py - drag.startPy) > 3) drag.moved = true;
      if (drag.moved) {
        drag.px = px;
        drag.py = py;
        setView(panBy(view, geomRef.current, dx, dy));
      }
      return;
    }
    const hit = pickAt(lay, view, geomRef.current, xFor(lay).x, px, py);
    const next = hit ? hit.index : -1;
    if (next !== hoverRef.current) {
      hoverRef.current = next;
      scheduleDraw();
    }
  };

  const onPointerUp = (ev) => {
    const drag = dragRef.current;
    dragRef.current = null;
    canvasRef.current?.releasePointerCapture?.(ev.pointerId);
    if (!drag || drag.moved) return;
    const lay = layoutRef.current;
    const view = viewRef.current;
    if (!lay || !view) return;
    const [px, py] = localPoint(ev);
    const hit = pickAt(lay, view, geomRef.current, xFor(lay).x, px, py);
    if (!hit) return;
    if (rerootMode) {
      try {
        const node = lay.refs[hit.index];
        treeRef.current.reroot(node);
        // Rerooting renumbers every row, so a selection or colouring made
        // against the old ordering would now point at unrelated samples.
        installTree(treeRef.current);
        setStatus("Rerooted. Selection and colours were cleared — the row order changed.");
      } catch (e) {
        setStatus(`Reroot failed: ${e && e.message ? e.message : e}`);
      }
      return;
    }
    selectNode(hit.index);
  };

  // Bound by hand, NOT as an onWheel prop: React registers wheel listeners on
  // the root as passive, so preventDefault() there is ignored with a console
  // warning and the wheel scrolls the page instead of zooming the tree.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    function onWheel(ev) {
      const view = viewRef.current;
      if (!view) return;
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const px = ev.clientX - rect.left;
      const py = ev.clientY - rect.top;
      const factor = Math.pow(1.0016, -ev.deltaY);
      setView(ev.shiftKey
        ? zoomCols(view, geomRef.current, factor, px)
        : zoomRows(view, geomRef.current, factor, py));
    }
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [setView]);

  // ---- zoom controls ----------------------------------------------------

  const zoomV = (factor) => {
    const view = viewRef.current;
    if (view) setView(zoomRows(view, geomRef.current, factor, null));
  };
  const zoomH = (factor) => {
    const view = viewRef.current;
    if (view) setView(zoomCols(view, geomRef.current, factor, null));
  };
  const fit = () => {
    const lay = layoutRef.current;
    if (lay) setView(fitView(lay, geomRef.current, xFor(lay).max));
  };

  useEffect(() => {
    function onKey(ev) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const tag = ev.target && ev.target.tagName ? ev.target.tagName.toLowerCase() : "";
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (ev.key === "+" || ev.key === "=") { ev.preventDefault(); ev.shiftKey ? zoomH(ZOOM_STEP) : zoomV(ZOOM_STEP); }
      else if (ev.key === "-" || ev.key === "_") { ev.preventDefault(); ev.shiftKey ? zoomH(1 / ZOOM_STEP) : zoomV(1 / ZOOM_STEP); }
      else if (ev.key === "0") { ev.preventDefault(); fit(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- overview navigation ---------------------------------------------

  const overviewJump = (ev) => {
    const lay = layoutRef.current;
    const view = viewRef.current;
    if (!lay || !view) return;
    const rect = overviewRef.current.getBoundingClientRect();
    const frac = (ev.clientY - rect.top) / (rect.height || 1);
    setView(centreRow(view, geomRef.current, frac * lay.nLeaves));
  };

  // ---- search -----------------------------------------------------------

  // Searching MOVES THE VIEW. Marking the hit was never the missing part — on a
  // tree fitted to the window the mark is a fraction of a pixel tall, so the
  // only way to answer "where is it" is to go there.
  const goToHit = useCallback((rows, idx) => {
    const lay = layoutRef.current;
    const view = viewRef.current;
    if (!lay || !view || !rows.length) return;
    const row = rows[((idx % rows.length) + rows.length) % rows.length];
    setView(revealRows(view, lay, geomRef.current, [row], { rowH: REVEAL_ROW_H, context: 8 }));
  }, [setView]);

  useEffect(() => {
    const lay = layoutRef.current;
    if (!lay) return;
    const rows = searchRows(lay, searchTerm, displayName);
    setHits(rows);
    setHitAt(0);
    if (rows.length) goToHit(rows, 0);
    else scheduleDraw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTerm, layout, displayName]);

  const stepHit = (delta) => {
    if (!hits.length) return;
    const next = ((hitAt + delta) % hits.length + hits.length) % hits.length;
    setHitAt(next);
    goToHit(hits, next);
  };

  // ---- colour groups ----------------------------------------------------

  const colourSelection = () => {
    const lay = layoutRef.current;
    if (!lay || !selection) return;
    if (!colourMaskRef.current) {
      colourMaskRef.current = new Int8Array(lay.n).fill(-1);
    }
    const idx = groups.length % PALETTE.length;
    const mask = colourMaskRef.current;
    const stack = [selection.index];
    while (stack.length) {
      const i = stack.pop();
      mask[i] = idx;
      for (let c = lay.childStart[i]; c < lay.childStart[i + 1]; c++) stack.push(lay.childIdx[c]);
    }
    setGroups(groups.concat([{
      colour: PALETTE[idx],
      range: selection.range,
      n: selection.tips.length,
    }]));
  };

  const clearColours = () => {
    colourMaskRef.current = null;
    setGroups([]);
  };

  // ---- root controls ----------------------------------------------------

  function midpointRoot() {
    if (!treeRef.current) return;
    try {
      const mid = computeMidpoint(treeRef.current);
      if (mid && mid.location) {
        treeRef.current.reroot(mid.location, mid.breakpoint || 0);
        installTree(treeRef.current);
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
      installTree(tree);
      setStatus("");
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
    if (!selection || !tableChoice || opening) return;
    const win = window.open("", "_blank");
    setOpening(true);
    setStatus("");
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(project)}/snp-selection`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ samples: selection.tips, source: path }),
        }
      );
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body && body.detail) detail = body.detail;
        } catch { /* not JSON — the status is all there is */ }
        throw new Error(detail);
      }
      const data = await res.json();
      const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/preview-xlsx?path=${encodeURIComponent(tableChoice)}&selection=${encodeURIComponent(data.token)}`;
      if (win) win.location = url;
      else {
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

  const filename = path ? path.split("/").pop() : "";
  const nLeaves = layout ? layout.nLeaves : 0;
  const nSamples = useMemo(() => {
    if (!layout) return 0;
    let c = 0;
    for (let r = 0; r < layout.nLeaves; r++) {
      const nm = layout.names[layout.leafRows[r]] || "";
      if (nm && nm.toLowerCase() !== "root") c++;
    }
    return c;
  }, [layout]);

  return (
    <div className="tree-page">
      <div className="tree-bar">
        <strong>Tree viewer</strong>
        <span className="tree-meta">
          {filename}{nLeaves ? ` · ${nLeaves.toLocaleString()} leaves` : ""}{project ? ` · ${project}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <div className="tree-search">
          <input
            placeholder="Find tip…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") stepHit(e.shiftKey ? -1 : 1); }}
            title="Type part of a sample name. The view jumps to the match."
          />
          {searchTerm ? (
            hits.length ? (
              <>
                <span className="tree-hitcount">{hitAt + 1} of {hits.length}</span>
                <button onClick={() => stepHit(-1)} title="Previous match (Shift+Enter)">‹</button>
                <button onClick={() => stepHit(1)} title="Next match (Enter)">›</button>
              </>
            ) : <span className="tree-hitcount tree-nohit">no match</span>
          ) : null}
        </div>
        <label title="Draw branches by topology only, ignoring branch lengths. Useful when every branch is a fraction of a substitution long.">
          <input type="checkbox" checked={cladogram} onChange={(e) => setCladogram(e.target.checked)} /> Topology
        </label>
        <label><input type="checkbox" checked={stripSuffix} onChange={(e) => setStripSuffix(e.target.checked)} /> Strip <code>_zc.vcf</code></label>
        <label><input type="checkbox" checked={showBootstrap} onChange={(e) => setShowBootstrap(e.target.checked)} /> Bootstrap</label>
        <label title={tables.length ? "While this is on, a branch click reroots the tree instead of selecting a clade." : ""}>
          <input type="checkbox" checked={rerootMode} onChange={(e) => setRerootMode(e.target.checked)} /> Reroot mode
        </label>
        <button onClick={midpointRoot}>Midpoint</button>
        <button onClick={resetRoot}>Reset</button>
        <button onClick={downloadTre}>Download .tre</button>
        {status ? <span className="tree-status">{status}</span> : null}
      </div>

      {tables.length > 0 ? (
        <div className="tree-clade-bar">
          {selection ? (
            <>
              <strong>Clade: {selection.tips.length} sample{selection.tips.length === 1 ? "" : "s"}</strong>
              {nSamples && selection.tips.length >= nSamples
                ? <span className="tree-dim">(the whole tree)</span> : null}
              {/* Always offered, never conditional on "is it visible": the
                  answer would be computed from a ref during render and go
                  stale the moment the user pans, leaving a button that lies. */}
              <button className="tree-linky" onClick={() => {
                const lay = layoutRef.current;
                setView(revealRows(viewRef.current, lay, geomRef.current,
                                   selection.range, { rowH: REVEAL_ROW_H, context: 4 }));
              }} title="Scroll and zoom to the selected clade.">Show it</button>
              {rerootMode ? <span className="tree-warn">Reroot mode is on — a branch click reroots.</span> : null}
              <select value={tableChoice} onChange={(e) => setTableChoice(e.target.value)} style={{ maxWidth: 340 }}>
                {tables.map((t) => (
                  <option key={t.path} value={t.path}>
                    {t.name}{t.rows && t.cols ? ` (${t.rows.toLocaleString()} × ${t.cols.toLocaleString()})` : ""}
                  </option>
                ))}
              </select>
              <button onClick={openSnpTable} disabled={opening}
                      title="Open this SNP table showing only the selected clade's samples and their SNP positions.">
                {opening ? "Opening…" : "Open SNP table for clade"}
              </button>
              <button onClick={colourSelection} title="Keep this clade marked in a colour, so it stays visible after you select something else.">Colour it</button>
              <button onClick={clearSelection}>Clear</button>
            </>
          ) : (
            <span className="tree-hint">
              {rerootMode ? (
                <><strong>Reroot mode is on.</strong> A branch click reroots the tree. Untick it to select clades.</>
              ) : (
                <><strong>Select a clade:</strong> click any branch or node. This group's SNP table
                  can then be opened for those samples alone, showing only the positions where they differ.</>
              )}
            </span>
          )}
          {groups.length ? (
            <span className="tree-groups">
              {groups.map((g, i) => (
                <span key={i} className="tree-chip" style={{ borderColor: g.colour, color: g.colour }}>
                  {g.n}
                </span>
              ))}
              <button className="tree-linky" onClick={clearColours}>clear colours</button>
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="tree-body" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          className={rerootMode ? "tree-canvas tree-reroot" : "tree-canvas"}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={() => { if (hoverRef.current !== -1) { hoverRef.current = -1; scheduleDraw(); } }}
        />
        <canvas
          ref={overviewRef}
          className="tree-overview"
          title="The whole tree. The box is what you are looking at; click to jump."
          onPointerDown={overviewJump}
          onPointerMove={(e) => { if (e.buttons === 1) overviewJump(e); }}
        />
        <div className="tree-zoom">
          <span className="tree-zoom-tag" title="Vertical zoom: how far apart the tips are drawn">rows</span>
          <button onClick={() => zoomV(ZOOM_STEP)} title="Spread the tips apart (keyboard: +)">+</button>
          <button onClick={() => zoomV(1 / ZOOM_STEP)} title="Pack the tips closer (keyboard: −)">−</button>
          <span className="tree-zoom-tag" title="Horizontal zoom: the branch-length axis">width</span>
          <button onClick={() => zoomH(ZOOM_STEP)} title="Stretch the branch axis (keyboard: Shift +)">+</button>
          <button onClick={() => zoomH(1 / ZOOM_STEP)} title="Compress the branch axis (keyboard: Shift −)">−</button>
          <button className="tree-zoom-fit" onClick={fit} title="Back to the whole tree (keyboard: 0)">Fit</button>
        </div>
        <div className="tree-readout">
          {nLeaves ? (
            <>
              {Math.round(readout.rows).toLocaleString()} of {nLeaves.toLocaleString()} tips shown
              {readout.labels ? "" : " · zoom in for names"}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
