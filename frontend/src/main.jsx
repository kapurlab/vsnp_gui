import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import IgvStandalone from "./IgvStandalone.jsx";
import TreePtStandalone from "./TreePtStandalone.jsx";
import TreePcStandalone from "./TreePcStandalone.jsx";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const view = params.get("view");

const root = createRoot(document.getElementById("root"));
if (view === "igv") {
  root.render(<IgvStandalone />);
} else if (view === "tree-pt") {
  root.render(<TreePtStandalone />);
} else if (view === "tree-pc") {
  root.render(<TreePcStandalone />);
} else {
  root.render(<App />);
}
