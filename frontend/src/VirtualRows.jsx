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
  ...rest             // role, aria-label, … for the scrolling box
}) {
  const scrollRef = useRef(null);
  const windowRef = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(0);
  // key -> {h, open}: every row drawn so far, at the current width.
  const measured = useRef(new Map());
  // The first closed row's height at this width: the guess for closed rows.
  const closedH = useRef(0);
  const width = useRef(0);
  const [version, setVersion] = useState(0);

  // The height the list is given: 320 px on the page, most of the window
  // when it is expanded, 0 while a closed <details> hides it. A new width can
  // wrap rows differently, so what was measured at the old one is dropped.
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
      setViewH(el.clientHeight);
    };
    update();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    setScrollTop(0);
  }, [resetKey]);

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
  const view = viewH || 320;
  const top = Math.max(0, Math.min(scrollTop, total - view));
  // The first row whose bottom is below `top`.
  let lo = 0;
  let hi = Math.max(0, n - 1);
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offs[mid + 1] <= top) lo = mid + 1; else hi = mid;
  }
  const start = Math.max(0, lo - overscan);
  let end = lo;
  while (end < n && offs[end] < top + view) end++;
  end = Math.min(n, end + overscan);
  const offset = n ? offs[start] : 0;

  // Measure what was drawn. A height is kept once known, so this settles as
  // soon as the rows drawn are measured; nothing here re-guesses the rest.
  useLayoutEffect(() => {
    const win = windowRef.current;
    const el = scrollRef.current;
    if (!win || !el) return;
    const at = el.scrollTop;
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
    if (shift && at > 0) el.scrollTop = at + shift;
    if (changed) setVersion((v) => v + 1);
  });

  return (
    <div
      {...rest}
      ref={scrollRef}
      className={className ? `s2-vrows ${className}` : "s2-vrows"}
      style={style}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
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
