// Painting the tree onto a canvas.
//
// The rule this file exists to enforce: LINE WIDTHS, MARKER RADII AND FONT
// SIZES ARE SCREEN PIXELS, ALWAYS. They are never multiplied by a zoom factor,
// because they are never part of the geometry. Zoom changes where things are,
// not how thick they are.
//
// The old viewer could not hold that line. phylotree zooms an SVG group with
// `scale(k)`, which scales strokes and radii with the coordinates, so the
// previous fix had to divide every width by k through CSS custom properties to
// stand still — and anything it missed (the node markers outside pick mode) grew
// to fill the screen. Here there is nothing to counteract: `lineWidth = 1` is
// one pixel at every zoom level, and stays one pixel, and cannot be made to do
// anything else.
//
// Font size is the same argument seen from the other end. phylotree sizes tip
// labels to the row spacing at render time, which on a 4,611-tip tree fitted to
// 600px is 0.13px; magnifying that 60× still gives 7.8px, so "zoom in to read
// the names" never actually arrived. Here the font is a function of the row
// height IN SCREEN PIXELS at the moment of drawing, so a readable row always
// carries a readable label.

import { rowToPy, xToPx, visibleRows, LABEL_MIN_ROW_H } from "./view.js";

/** Categorical colours for user-assigned groups. Distinguishable on white. */
export const PALETTE = [
  "#1f77b4", "#d95f02", "#2ca02c", "#9467bd",
  "#8c564b", "#e7298a", "#17becf", "#bcbd22",
];

export const THEME = {
  bg: "#ffffff",
  branch: "#5a6472",
  branchFaint: "#98a2b3",
  selected: "#1d5fbf",
  selectedBand: "rgba(29,95,191,0.13)",
  hit: "#d62728",
  hitBand: "rgba(214,39,40,0.20)",
  hitCurrentBand: "rgba(214,39,40,0.38)",
  text: "#1a2029",
  axis: "#98a2b3",
  axisText: "#6b7480",
  hover: "#0b7285",
};

/** Label font size for a given row height — readable, never microscopic. */
export function labelFontSize(rowH) {
  return Math.max(7, Math.min(14, rowH * 0.82));
}

/** Node markers only once rows are far enough apart to tell them apart. */
export function nodeRadius(rowH) {
  if (rowH < 7) return 0;
  return Math.min(3.5, 1.6 + rowH * 0.06);
}

/**
 * A "nice" axis step near `target`, so tick labels read 0.002 / 0.005 / 0.01
 * rather than eight ticks all rounding to "0.00" — which is what the old
 * fixed-2-decimal axis showed on trees whose whole width is 0.02.
 */
// Row height below which an internal-node label has nowhere to go. Exported so
// the viewer can say "zoom in" rather than leave the Bootstrap tick looking
// broken at a zoom level where no label could have fitted.
export const INTERNAL_LABEL_MIN_ROW_H = 9;

export function niceStep(target) {
  if (!(target > 0)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(target)));
  const norm = target / mag;
  const step = norm >= 5 ? 5 : norm >= 2 ? 2 : 1;
  return step * mag;
}

/** Format a tick to the precision its own step actually resolves. */
export function formatTick(value, step) {
  if (value === 0) return "0";
  // A step of 1 or more resolves nothing after the point. This matters for the
  // SNP axis, where the unit is a whole count and "5.0 SNPs" is noise; on the
  // substitutions/site axis the step is always far below 1, so nothing changes.
  if (step >= 1) return String(Math.round(value));
  const decimals = Math.max(0, Math.ceil(-Math.log10(step)) + 1);
  if (step < 1e-4) return value.toExponential(1);
  return value.toFixed(Math.min(decimals, 8));
}

/**
 * Draw the tree.
 *
 * Branches go down in batched paths — one path per colour, stroked once —
 * because a 4,611-tip tree is ~9,200 branches and a stroke() per branch is what
 * turns a pan into a slideshow.
 */
export function drawTree(ctx, o) {
  const { layout, view, geom, x } = o;
  const th = o.theme || THEME;
  const W = geom.width;
  const H = geom.height;

  ctx.save();
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = th.bg;
  ctx.fillRect(0, 0, W, H);

  if (!layout.n) {
    ctx.restore();
    return;
  }

  const [visLo, visHi] = visibleRows(view, geom, layout);
  const rowH = view.rowH;

  // ---- bands ------------------------------------------------------------
  // A band is how a selection stays visible when it is 30 rows out of 4,611.
  // Those 30 rows are 3.9 screen pixels at fit-to-size: far too little to show
  // as coloured text, plenty to show as a filled stripe across the plot. This
  // is the direct answer to "the names go blue but you cannot see them go
  // blue" — at fit you are not meant to read the names, only to see WHERE.
  if (o.selRange) {
    const [lo, hi] = o.selRange;
    const y0 = rowToPy(view, geom, lo) - rowH / 2;
    const y1 = rowToPy(view, geom, hi) + rowH / 2;
    ctx.fillStyle = th.selectedBand;
    ctx.fillRect(0, y0, W, Math.max(2.5, y1 - y0));
  }
  if (o.searchRows && o.searchRows.length) {
    for (const r of o.searchRows) {
      if (r < visLo - 2 || r > visHi + 2) continue;
      const y = rowToPy(view, geom, r);
      ctx.fillStyle = r === o.currentRow ? th.hitCurrentBand : th.hitBand;
      ctx.fillRect(0, y - Math.max(1.25, rowH / 2), W, Math.max(2.5, rowH));
    }
  }

  // ---- branches ---------------------------------------------------------
  // Crisp hairlines: a 1px stroke centred on an integer coordinate straddles
  // two pixel columns and comes out grey and fuzzy. The half-pixel offset puts
  // it inside one.
  ctx.translate(0.5, 0.5);

  const groups = new Map();
  const push = (key, colour, width) => {
    let g = groups.get(key);
    if (!g) {
      g = { colour, width, path: new Path2D() };
      groups.set(key, g);
    }
    return g.path;
  };
  const baseWidth = rowH >= 10 ? 1.4 : 1;
  const plain = push("plain", o.dense ? th.branchFaint : th.branch, baseWidth);

  for (let i = 0; i < layout.n; i++) {
    // Cull whole subtrees by their row span: at high zoom this is what keeps
    // the frame time flat instead of proportional to the size of the tree.
    if (layout.rowMax[i] < visLo || layout.rowMin[i] > visHi) continue;

    const nx = xToPx(view, geom, x[i]);
    const ny = rowToPy(view, geom, layout.row[i]);

    let path = plain;
    let sel = o.selMask && o.selMask[i];
    let colourIdx = o.colourMask ? o.colourMask[i] : -1;
    if (sel) {
      path = push("sel", th.selected, 2);
    } else if (colourIdx >= 0) {
      const c = o.palette[colourIdx % o.palette.length];
      path = push(`c${colourIdx}`, c, 1.8);
    }

    const p = layout.parent[i];
    if (p >= 0) {
      const ppx = xToPx(view, geom, x[p]);
      path.moveTo(ppx, ny);
      path.lineTo(nx, ny);
    }
    const cs = layout.childStart[i];
    const ce = layout.childStart[i + 1];
    if (ce > cs) {
      const topY = rowToPy(view, geom, layout.row[layout.childIdx[cs]]);
      const botY = rowToPy(view, geom, layout.row[layout.childIdx[ce - 1]]);
      path.moveTo(nx, topY);
      path.lineTo(nx, botY);
    }
  }

  ctx.lineCap = "butt";
  ctx.lineJoin = "miter";
  for (const g of groups.values()) {
    ctx.strokeStyle = g.colour;
    ctx.lineWidth = g.width;
    ctx.stroke(g.path);
  }

  // ---- node markers -----------------------------------------------------
  const nodeR = nodeRadius(rowH);
  if (nodeR > 0 && o.showNodes !== false) {
    ctx.fillStyle = th.branch;
    ctx.beginPath();
    for (let i = 0; i < layout.n; i++) {
      if (layout.isLeaf[i]) continue;
      if (layout.rowMax[i] < visLo || layout.rowMin[i] > visHi) continue;
      const nx = xToPx(view, geom, x[i]);
      const ny = rowToPy(view, geom, layout.row[i]);
      if (nx < -4 || nx > W + 4) continue;
      ctx.moveTo(nx + nodeR, ny);
      ctx.arc(nx, ny, nodeR, 0, Math.PI * 2);
    }
    ctx.fill();
  }

  if (o.hoverIndex != null && o.hoverIndex >= 0) {
    const i = o.hoverIndex;
    ctx.strokeStyle = th.hover;
    ctx.fillStyle = "rgba(11,114,133,0.25)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(
      xToPx(view, geom, x[i]),
      rowToPy(view, geom, layout.row[i]),
      Math.max(5, nodeR + 3), 0, Math.PI * 2
    );
    ctx.fill();
    ctx.stroke();
  }

  // ---- labels -----------------------------------------------------------
  if (rowH >= LABEL_MIN_ROW_H) {
    const fs = labelFontSize(rowH);
    ctx.font = `${fs}px system-ui, -apple-system, "Segoe UI", sans-serif`;
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    const boldFont = `bold ${fs}px system-ui, -apple-system, "Segoe UI", sans-serif`;
    const plainFont = ctx.font;
    for (let r = Math.max(0, visLo); r <= visHi; r++) {
      const i = layout.leafRows[r];
      const ny = rowToPy(view, geom, r);
      if (ny < -fs || ny > H + fs) continue;
      const nx = xToPx(view, geom, x[i]);
      if (nx > W) continue;
      const name = o.displayName ? o.displayName(layout.names[i]) : layout.names[i];
      if (!name) continue;
      const isHit = o.searchRowSet && o.searchRowSet.has(r);
      const isSel = o.selMask && o.selMask[i];
      const cIdx = o.colourMask ? o.colourMask[i] : -1;
      // Search wins over selection: a hit inside the selected clade must stay
      // findable, which is the case where both are true at once.
      if (isHit) {
        ctx.fillStyle = th.hit;
        ctx.font = boldFont;
      } else if (isSel) {
        ctx.fillStyle = th.selected;
        ctx.font = boldFont;
      } else if (cIdx >= 0) {
        ctx.fillStyle = o.palette[cIdx % o.palette.length];
        ctx.font = plainFont;
      } else {
        ctx.fillStyle = th.text;
        ctx.font = plainFont;
      }
      ctx.fillText(name, nx + nodeR + 3, ny);
    }
    ctx.font = plainFont;
  }

  // Internal labels (bootstrap) only when there is room for them.
  if (o.showInternalLabels && rowH >= INTERNAL_LABEL_MIN_ROW_H) {
    const fs = Math.max(7, Math.min(11, rowH * 0.6));
    ctx.font = `${fs}px system-ui, sans-serif`;
    ctx.fillStyle = th.axisText;
    ctx.textAlign = "right";
    ctx.textBaseline = "bottom";
    for (let i = 0; i < layout.n; i++) {
      if (layout.isLeaf[i]) continue;
      if (layout.rowMax[i] < visLo || layout.rowMin[i] > visHi) continue;
      const name = layout.names[i];
      if (!name || name === "root") continue;
      ctx.fillText(name, xToPx(view, geom, x[i]) - 3, rowToPy(view, geom, layout.row[i]) - 1);
    }
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
  }

  ctx.restore();
  drawAxis(ctx, o);
}

/** The distance ruler across the top. */
export function drawAxis(ctx, o) {
  const { view, geom } = o;
  const th = o.theme || THEME;
  const W = geom.width;
  ctx.save();
  ctx.fillStyle = th.bg;
  ctx.fillRect(0, 0, W, geom.padT);
  ctx.strokeStyle = th.axis;
  ctx.fillStyle = th.axisText;
  ctx.lineWidth = 1;
  ctx.font = "10px system-ui, sans-serif";
  ctx.textBaseline = "top";
  ctx.textAlign = "center";

  const y = geom.padT - 10.5;
  ctx.beginPath();
  ctx.moveTo(geom.padL, y);
  ctx.lineTo(geom.padL + geom.plotW, y);
  ctx.stroke();

  if (o.cladogram) {
    ctx.textAlign = "left";
    ctx.fillText("topology only — branch lengths not to scale", geom.padL, 2);
    ctx.restore();
    return;
  }

  // The ruler can be labelled in a unit other than the tree's own. Branch
  // lengths come off RAxML as substitutions per site of the SNP alignment, which
  // answers "how far" but not the question actually being asked of these trees —
  // how many SNPs is this branch? Multiplying by the alignment's length converts
  // one to the other, and it is purely a LABELLING change: the geometry, the
  // view and every hit test stay in tree units, so nothing downstream has to
  // know which unit is on show. Tick placement is chosen in the DISPLAY unit,
  // though — picking a nice step in substitutions and converting it would put
  // the marks at 7.3 and 14.6 SNPs.
  const scale = o.unitScale > 0 ? o.unitScale : 1;
  const stepD = niceStep(((view.x1 - view.x0) * scale) / 6);
  const firstD = Math.ceil((view.x0 * scale) / stepD) * stepD;
  const lastD = view.x1 * scale + stepD * 0.001;
  // Reserved for the unit caption at the right-hand end, so the last tick label
  // does not print on top of it.
  const unitW = o.unitLabel ? ctx.measureText(o.unitLabel).width + 8 : 0;
  const labelLimit = geom.padL + geom.plotW - unitW;
  ctx.beginPath();
  // Counted rather than accumulated: `v += step` on a step of ~1e-9 against an
  // x0 of ~0.7 drifts visibly across a screen's worth of ticks.
  for (let k = 0; ; k++) {
    const vD = firstD + k * stepD;
    if (vD > lastD) break;
    const px = xToPx(view, geom, vD / scale);
    if (px < geom.padL - 1 || px > geom.padL + geom.plotW + 1) continue;
    ctx.moveTo(px, y);
    ctx.lineTo(px, y + 4);
    // The tick mark is always drawn; only its label gives way to the caption.
    const text = formatTick(vD, stepD);
    if (px + ctx.measureText(text).width / 2 <= labelLimit) ctx.fillText(text, px, 2);
  }
  ctx.stroke();
  if (o.unitLabel) {
    // Right-aligned, so it cannot collide with the "0" tick at the axis origin.
    ctx.textAlign = "right";
    ctx.fillText(o.unitLabel, geom.padL + geom.plotW, 2);
  }
  ctx.restore();
}

/**
 * The overview strip: the whole tree squeezed into one column of pixels.
 *
 * Its entire job is to answer "where am I, and where is the thing I am looking
 * for" on a tree far too tall to show at a readable scale. The viewport is a
 * bracket, the selection is a bar, and every search hit is a tick — so a match
 * 3,000 rows below the fold is visible as a mark you can click, rather than
 * something you have to believe is there.
 */
export function drawOverview(ctx, o) {
  const { layout, view, geom, width, height } = o;
  const th = o.theme || THEME;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f2f4f7";
  ctx.fillRect(0, 0, width, height);
  if (!layout.nLeaves) return;

  const rowToY = (r) => (r / layout.nLeaves) * height;

  if (o.colourRanges) {
    for (const [lo, hi, colour] of o.colourRanges) {
      ctx.fillStyle = colour;
      ctx.fillRect(0, rowToY(lo), width, Math.max(1.5, rowToY(hi + 1) - rowToY(lo)));
    }
  }
  if (o.selRange) {
    const [lo, hi] = o.selRange;
    ctx.fillStyle = th.selected;
    ctx.fillRect(0, rowToY(lo), width, Math.max(2, rowToY(hi + 1) - rowToY(lo)));
  }
  if (o.searchRows) {
    for (const r of o.searchRows) {
      ctx.fillStyle = r === o.currentRow ? th.hit : "rgba(214,39,40,0.75)";
      const h = r === o.currentRow ? 3 : 2;
      ctx.fillRect(0, Math.max(0, rowToY(r) - h / 2), width, h);
    }
  }

  // The viewport bracket. Given a minimum height so that at 48px rows — where
  // it covers 12 of 4,611 rows, i.e. 0.26% of the strip — it is still findable.
  const topRow = view.yTop;
  const rowsShown = geom.plotH / (view.rowH || 1);
  const y0 = rowToY(topRow);
  const h = Math.max(4, rowToY(topRow + rowsShown) - y0);
  ctx.strokeStyle = "#1a2029";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(0.75, Math.max(0.75, y0), width - 1.5, Math.min(h, height - 1.5));
  ctx.fillStyle = "rgba(26,32,41,0.08)";
  ctx.fillRect(0.75, Math.max(0.75, y0), width - 1.5, Math.min(h, height - 1.5));
}
