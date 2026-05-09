import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import IgvStandalone from "./IgvStandalone.jsx";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const isIgvView = params.get("view") === "igv";

createRoot(document.getElementById("root")).render(isIgvView ? <IgvStandalone /> : <App />);
