import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const view = params.get("view");

// The viewers load on demand. igv.js alone is 1.4 MB of the bundle, and the
// main GUI needs it only when its own IGV panel opens (App.jsx imports it then);
// the tree viewer never does. Keeping both out of the main chunk makes every
// page open — the GUI, a tree, a fresh OnDemand session — about a quarter of
// the download it was.
const TreeStandalone = lazy(() => import("./TreeStandalone.jsx"));
const IgvStandalone = lazy(() => import("./IgvStandalone.jsx"));

const Fallback = () => (
  <div style={{ padding: "1rem", fontFamily: "system-ui" }}>Loading viewer…</div>
);

const ErrorView = ({ what, err }) => (
  <div style={{ padding: "1rem", fontFamily: "system-ui", color: "#b34" }}>
    <strong>{what} failed to load</strong>
    <pre style={{ whiteSpace: "pre-wrap" }}>{String(err && err.message ? err.message : err)}</pre>
  </div>
);

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) { console.error("Standalone viewer error:", err, info); }
  render() {
    if (this.state.err) return <ErrorView what={this.props.label} err={this.state.err} />;
    return this.props.children;
  }
}

const root = createRoot(document.getElementById("root"));
if (view === "igv") {
  root.render(
    <ErrorBoundary label="IGV viewer">
      <Suspense fallback={<Fallback />}>
        <IgvStandalone />
      </Suspense>
    </ErrorBoundary>
  );
} else if (view === "tree") {
  root.render(
    <ErrorBoundary label="Tree viewer">
      <Suspense fallback={<Fallback />}>
        <TreeStandalone />
      </Suspense>
    </ErrorBoundary>
  );
} else {
  root.render(<App />);
}
