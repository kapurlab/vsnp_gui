import React, { useLayoutEffect, useMemo, useRef, useState } from "react";

// A long list that puts only the rows in view on the page.
//
// The Step 2 sample list drew every VCF as a row: 23,671 of them on the Ames
// modified_NL set, 165,706 elements. Opening it took seconds, scrolling it
// dragged, and every change anywhere in the app redrew all of it while it was
// open. Here the list keeps its full height, so the scroll bar and the wheel
// behave as before, but only the rows under the viewport (and a few either
// side) exist; scrolling swaps them.
//
// Two ways to scroll. By default the list is its own scrolling box. With
// `scrollParent` it is a block inside a box that scrolls other things too —
// the Projects panel, whose one box holds every project row and each
// expanded project's samples — and it windows itself against that box, so the
// panel looks and scrolls exactly as it did.
//
// Every row drawn is measured and its height kept by key (and by whether it
// was open), so rows need not be one height: a Step 1 name that wraps, or a
// row opened to show everything, simply takes its space. A row not yet drawn
// is guessed at the first closed row's height, or, when it is open, at the
// average open row. A guess is never re-derived from whichever row happens to
// be drawn first: an earlier version did, flipped between two heights, and
// React gave up ("Maximum update depth exceeded"). When a row above the view
// turns out taller or shorter than guessed, the scroll position moves by the
// difference, so what is under the eye stays put.
//
// What it cannot do: the browser's own Find (Cmd/Ctrl-F) sees only the rows
// on the page. The filter box above the list searches all of them.

// The nearest ancestor that scrolls, or the page itself.
function scrollerOf(el) {
  for (let p = el ? el.parentElement : null; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if (oy === "auto" || oy === "scroll" || oy === "overlay") return p;
  }
  return document.scrollingElement || document.documentElement;
}

const isPage = (sc) => sc === document.scrollingElement || sc === document.documentElement;

export default function VirtualRows({
  items,              // the rows, in order
  itemKey,            // item -> a stable string key
  isOpen,             // item -> true when it is drawn opened (taller)
  renderItem,         // (item, index) -> the row's elements
  resetKey,           // when it changes (a new filter), go back to the top
  className,
  style,
  empty = null,       // shown when there are no items
  overscan = 10,      // rows drawn beyond each edge of the view
  estimate = 22,      // a closed row's height until one is measured
  tallRows = 8,       // an open row's height, in closed rows, until one is measured
  scrollParent = false, // window against the ancestor that scrolls, not a box of its own
  ...rest             // role, aria-label, … for the list's own element
}) {
  const scrollRef = useRef(null);     // the list's own element (its scrolling box, by default)
  const scrollerRef = useRef(null);   // with scrollParent: the ancestor that scrolls
  const windowRef = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(0);
  // With scrollParent: how far the list's top is above the top of the
  // scroller's view (negative while the list starts lower down), and the
  // height of that view.
  const [band, setBand] = useState({ top: 0, h: 0 });
  // The band last handed to setBand. A read is compared with this, never with
  // the state an updater is given: while an earlier update still waits (a
  // resize arrives through the ResizeObserver at a lower priority than the
  // read after every render), React applies each new updater to the state
  // from before that update. Such an updater saw the old band every time,
  // reported a change every time, and the read after the next render asked
  // again, with no end: resizing the window blanked the whole page with
  // "Maximum update depth exceeded" (backend/perf/resize_probe.mjs).
  const bandAsked = useRef({ top: 0, h: 0 });
  // key -> {h, open}: every row drawn so far, at the current width.
  const measured = useRef(new Map());
  // The first closed row's height at this width: the guess for closed rows.
  const closedH = useRef(0);
  const width = useRef(0);
  const [version, setVersion] = useState(0);

  // Where the scroller's view falls on the list, read from the page.
  const readBand = () => {
    const el = scrollRef.current;
    const sc = scrollerRef.current;
    if (!el || !sc) return;
    const r = el.getBoundingClientRect();
    const page = isPage(sc);
    const viewTop = page ? 0 : sc.getBoundingClientRect().top + sc.clientTop;
    const h = page ? window.innerHeight : sc.clientHeight;
    const top = viewTop - r.top;
    const was = bandAsked.current;
    if (Math.abs(was.top - top) < 0.5 && Math.abs(was.h - h) < 0.5) return;
    bandAsked.current = { top, h };
    setBand(bandAsked.current);
  };

  // The height the list is given: 320 px on the page, most of the window
  // when it is expanded, 0 while a closed <details> hides it. A new width can
  // wrap rows differently, so what was measured at the old one is dropped.
  // With scrollParent, the scroller's own scrolling and resizing move the
  // view instead.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const update = () => {
      const w = el.clientWidth;
      if (w && width.current && Math.abs(w - width.current) > 0.5) {
        measured.current.clear();
        closedH.current = 0;
        setVersion((v) => v + 1);
      }
      if (w) width.current = w;
      if (scrollParent) readBand(); else setViewH(el.clientHeight);
    };
    let sc = null;
    if (scrollParent) {
      sc = scrollerOf(el);
      scrollerRef.current = sc;
    }
    const target = sc && isPage(sc) ? window : sc;
    if (target) target.addEventListener("scroll", readBand, { passive: true });
    window.addEventListener("resize", update);
    update();
    let ro = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(update);
      ro.observe(el);
      if (sc && !isPage(sc)) ro.observe(sc);
    }
    return () => {
      if (target) target.removeEventListener("scroll", readBand);
      window.removeEventListener("resize", update);
      if (ro) ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollParent]);

  // A new filter starts at the top, in a box of its own. A shared scroller is
  // left where it is: it holds other things the user may be looking at.
  useLayoutEffect(() => {
    if (scrollParent) return;
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    setScrollTop(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  // Content above the list, in the same scroller, can grow or shrink with no
  // scroll event (another project opening above this one), so the view is
  // read again after every render. It sets state only when it moved.
  useLayoutEffect(() => { if (scrollParent) readBand(); });

  const rowH = closedH.current || estimate;
  // Where each row starts: offs[i] is the top of row i, offs[n] the total.
  // Redone when the rows, their open state or a measurement changes, not on
  // a scroll, which is the render that has to be quick.
  const offs = useMemo(() => {
    let sum = 0;
    let count = 0;
    for (const m of measured.current.values()) {
      if (m.open) { sum += m.h; count += 1; }
    }
    const openGuess = count ? sum / count : rowH * tallRows;
    const out = new Float64Array(items.length + 1);
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      const open = Boolean(isOpen(it));
      const m = measured.current.get(itemKey(it));
      out[i + 1] = out[i] + (m && m.open === open ? m.h : (open ? openGuess : rowH));
    }
    return out;
    // `version` stands for the measurements, which live in a ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, isOpen, itemKey, version, rowH, tallRows]);

  const n = items.length;
  const total = offs[n];
  // The band of the list in view, in the list's own coordinates.
  const bandTop = scrollParent ? band.top : scrollTop;
  const bandH = scrollParent ? (band.h || 600) : (viewH || 320);
  const top = Math.max(0, Math.min(bandTop, total - Math.min(bandH, total)));
  const bottom = Math.max(0, Math.min(bandTop + bandH, total));
  // The first row whose bottom is below `top`.
  let lo = 0;
  let hi = Math.max(0, n - 1);
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offs[mid + 1] <= top) lo = mid + 1; else hi = mid;
  }
  const start = Math.max(0, lo - overscan);
  let end = lo;
  while (end < n && offs[end] < bottom) end++;
  end = Math.min(n, Math.max(end, lo + 1) + overscan);
  const offset = n ? offs[start] : 0;

  // Measure what was drawn. A height is kept once known, so this settles as
  // soon as the rows drawn are measured; nothing here re-guesses the rest.
  useLayoutEffect(() => {
    const win = windowRef.current;
    const el = scrollRef.current;
    if (!win || !el) return;
    const sc = scrollParent ? scrollerRef.current : el;
    if (!sc) return;
    // How far down the list the view starts, now.
    const at = scrollParent
      ? (isPage(sc) ? 0 : sc.getBoundingClientRect().top + sc.clientTop) - el.getBoundingClientRect().top
      : el.scrollTop;
    let changed = false;
    let shift = 0;
    for (const child of win.children) {
      const i = Number(child.dataset.vi);
      const it = items[i];
      if (!it) continue;
      const h = child.getBoundingClientRect().height;
      if (!h) continue;                        // hidden (a closed <details>)
      const open = Boolean(isOpen(it));
      const k = itemKey(it);
      const prev = measured.current.get(k);
      if (prev && prev.open === open && Math.abs(prev.h - h) <= 0.5) continue;
      measured.current.set(k, { h, open });
      if (!open && !closedH.current) closedH.current = h;
      const was = offs[i + 1] - offs[i];
      if (Math.abs(was - h) > 0.5) {
        changed = true;
        // Wholly above the view: keep the rows in view where they are.
        if (offs[i + 1] <= at + 0.5) shift += h - was;
      }
    }
    if (shift && at > 0) {
      if (scrollParent && isPage(sc)) window.scrollBy(0, shift);
      else sc.scrollTop += shift;
    }
    if (changed) setVersion((v) => v + 1);
  });

  return (
    <div
      {...rest}
      ref={scrollRef}
      className={className ? `s2-vrows ${className}` : "s2-vrows"}
      style={style}
      onScroll={scrollParent ? undefined : (e) => setScrollTop(e.currentTarget.scrollTop)}
    >
      {n === 0 ? empty : (
        <div style={{ height: total, position: "relative" }}>
          <div ref={windowRef} style={{ position: "absolute", top: offset, left: 0, right: 0 }}>
            {items.slice(start, end).map((it, k) => (
              <div key={itemKey(it)} data-vi={start + k}>{renderItem(it, start + k)}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
