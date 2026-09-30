// A dark appearance for igv.js, which ships light-only.
//
// igv.js renders inside a shadow root it attaches to the element it is given,
// with its own stylesheet adopted there, so nothing in the page's stylesheet
// reaches its navbar, track labels, menus or dialogs. This module adds a second
// stylesheet to that shadow root and mirrors the page's theme attribute onto
// the host element, so the rules below switch with the appearance control in
// the bar above the viewer. The page's colour variables inherit through the
// shadow boundary, so the viewer's chrome takes the same colours as the app.
//
// What igv.js paints on canvas keeps its own colours where they already read
// on a dark page — reads, coverage, mismatches, the sequence letters. Canvases
// it paints white (the ruler, the ideogram, the calls and annotation tracks,
// the coverage axis) are inverted with the hue rotated back, so black-on-white
// becomes white-on-black while a red locus box stays red and a blue feature
// stays blue.

const IGV_DARK_CSS = `
:host([data-theme="dark"]) .igv-navbar { background-color: var(--panel-2); border-color: var(--border); color: var(--text); }
:host([data-theme="dark"]) .igv-navbar .igv-zoom-widget,
:host([data-theme="dark"]) .igv-navbar .igv-zoom-widget-900 { color: var(--muted); }
:host([data-theme="dark"]) .igv-navbar-button { background-color: var(--panel); color: var(--muted); border-color: var(--border); }
:host([data-theme="dark"]) .igv-navbar-button-clicked { background-color: var(--accent); color: var(--accent-text); border-color: var(--accent); }
:host([data-theme="dark"]) .igv-navbar-text-button-svg-inactive rect { fill: var(--panel); stroke: var(--border); }
:host([data-theme="dark"]) .igv-navbar-text-button-svg-inactive text { fill: var(--muted); }
:host([data-theme="dark"]) input,
:host([data-theme="dark"]) select,
:host([data-theme="dark"]) .igv-navbar .igv-navbar-left-container .igv-navbar-genomic-location .igv-locus-size-group .igv-search-container input.igv-search-input,
:host([data-theme="dark"]) .igv-navbar .igv-navbar-left-container .igv-navbar-genomic-location .igv-chromosome-select-widget-container select { color: var(--text); background-color: var(--surface, var(--panel)); border-color: var(--border); }
:host([data-theme="dark"]) .igv-track-label { background-color: var(--panel); color: var(--text); border-color: var(--border); }
:host([data-theme="dark"]) .igv-track-label:hover,
:host([data-theme="dark"]) .igv-track-label:focus,
:host([data-theme="dark"]) .igv-track-label:active { background-color: var(--panel-2); }
:host([data-theme="dark"]) .igv-gear-menu-column > div { background: transparent; }
:host([data-theme="dark"]) .igv-gear-menu-column > div > div { color: var(--muted); }
:host([data-theme="dark"]) .igv-gear-menu-column > div > div:hover { color: var(--text); }
:host([data-theme="dark"]) .igv-zoom-in-notice-container { background-color: var(--panel); }
:host([data-theme="dark"]) .igv-zoom-in-notice-container > div,
:host([data-theme="dark"]) .igv-zoom-in-notice div { color: var(--text); background-color: transparent; }
:host([data-theme="dark"]) .igv-loading-spinner-container > div { border-color: rgba(160, 170, 178, .35); border-top-color: var(--text); }
:host([data-theme="dark"]) .igv-ideogram-canvas,
:host([data-theme="dark"]) .igv-viewport[data-track-type="ruler"] canvas,
:host([data-theme="dark"]) .igv-viewport[data-track-type="ideogram"] canvas,
:host([data-theme="dark"]) .igv-viewport[data-track-type="variant"] canvas,
:host([data-theme="dark"]) .igv-viewport[data-track-type="annotation"] canvas,
:host([data-theme="dark"]) .igv-viewport[data-track-type="feature"] canvas,
:host([data-theme="dark"]) .igv-axis-column canvas { filter: invert(1) hue-rotate(180deg); }
:host([data-theme="dark"]) .igv-menu-popup,
:host([data-theme="dark"]) .igv-menu-popup > div:not(:first-child) > div,
:host([data-theme="dark"]) .igv-ui-popover,
:host([data-theme="dark"]) .igv-ui-popover > div:last-child,
:host([data-theme="dark"]) .igv-ui-dropdown,
:host([data-theme="dark"]) .igv-ui-dropdown > div,
:host([data-theme="dark"]) .igv-ui-dropdown > div > div,
:host([data-theme="dark"]) .igv-track-label-popover,
:host([data-theme="dark"]) .igv-track-label-popover__body,
:host([data-theme="dark"]) .igv-ui-alert-dialog-container,
:host([data-theme="dark"]) .igv-ui-alert-dialog-container > div:last-child,
:host([data-theme="dark"]) .igv-ui-alert-dialog-container .igv-ui-alert-dialog-body .igv-ui-alert-dialog-body-copy,
:host([data-theme="dark"]) .igv-ui-dialog,
:host([data-theme="dark"]) .igv-ui-dialog .igv-ui-dialog-one-liner,
:host([data-theme="dark"]) .igv-ui-generic-dialog-container,
:host([data-theme="dark"]) .igv-ui-generic-dialog-container .igv-ui-generic-dialog-one-liner,
:host([data-theme="dark"]) .igv-ui-generic-dialog-container .igv-ui-generic-dialog-label-input,
:host([data-theme="dark"]) .igv-ui-generic-dialog-container .igv-ui-generic-dialog-label-input > div,
:host([data-theme="dark"]) .igv-ui-table { background: var(--panel); background-color: var(--panel); color: var(--text); border-color: var(--border); }
:host([data-theme="dark"]) .igv-menu-popup-header,
:host([data-theme="dark"]) .igv-ui-popover > div:first-child,
:host([data-theme="dark"]) .igv-track-label-popover__header,
:host([data-theme="dark"]) .igv-ui-alert-dialog-container > div:first-child,
:host([data-theme="dark"]) .igv-ui-dialog .igv-ui-dialog-header,
:host([data-theme="dark"]) .igv-ui-generic-dialog-container .igv-ui-generic-dialog-header { background-color: var(--panel-2); border-color: var(--border); color: var(--text); }
:host([data-theme="dark"]) .igv-menu-popup > div:not(:first-child) > div:hover,
:host([data-theme="dark"]) .igv-ui-dropdown > div > div:hover,
:host([data-theme="dark"]) .igv-ui-table tr:hover { background: var(--panel-2); background-color: var(--panel-2); }
:host([data-theme="dark"]) .igv-ui-alert-dialog-container .igv-ui-alert-dialog-body { color: var(--text); }
:host([data-theme="dark"]) .igv-ruler-tooltip > div { background-color: var(--panel); color: var(--text); border-color: var(--border); }
`;

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/**
 * Give the viewer inside `host` the page's appearance, now and as it changes.
 *
 * Call once the shadow root exists (igv.js attaches it when the browser is
 * constructed). Safe to call again: the stylesheet is added once per host.
 * Returns a function that stops mirroring the theme.
 */
export function installIgvTheme(host) {
  const root = host && host.shadowRoot;
  if (!root) return () => {};
  if (!host.__vsnpIgvThemed) {
    host.__vsnpIgvThemed = true;
    let adopted = false;
    try {
      if (typeof CSSStyleSheet !== "undefined" && "replaceSync" in CSSStyleSheet.prototype) {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(IGV_DARK_CSS);
        root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
        adopted = true;
      }
    } catch (_) { /* fall through to a <style> element */ }
    if (!adopted) {
      const style = document.createElement("style");
      style.textContent = IGV_DARK_CSS;
      root.appendChild(style);
    }
  }
  const mirror = () => { host.dataset.theme = currentTheme(); };
  mirror();
  const observer = new MutationObserver(mirror);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}

/**
 * Run `fn` as soon as `host` has a shadow root — igv.js creates it a moment
 * after createBrowser is called — so the viewer's chrome is never drawn light
 * first and restyled after. Gives up quietly after a few seconds.
 */
export function whenShadowRoot(host, fn) {
  let tries = 0;
  const poll = () => {
    if (!host) return;
    if (host.shadowRoot) { fn(host); return; }
    if (++tries < 600) requestAnimationFrame(poll);
  };
  poll();
}
