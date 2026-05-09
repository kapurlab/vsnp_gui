import React, { useEffect, useRef, useState } from "react";
import igv from "igv";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

export default function IgvStandalone() {
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project") || "";
  const samples = (params.get("samples") || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const [status, setStatus] = useState(samples.length ? "Loading…" : "No samples specified.");
  const [meta, setMeta] = useState({ reference: "", trackCount: 0 });
  const browserRef = useRef(null);
  const containerRef = useRef(null);
  const projectRef = useRef(project);
  const refNameRef = useRef("");
  const loadedRef = useRef(new Set());

  async function addSample(reqProject, sample) {
    if (!browserRef.current) {
      setStatus("IGV not ready yet — wait for initial load.");
      return;
    }
    if (reqProject !== projectRef.current) {
      setStatus(`Cannot mix projects (popout is showing ${projectRef.current}).`);
      return;
    }
    if (loadedRef.current.has(sample)) {
      setStatus(`${sample} already loaded.`);
      return;
    }
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(reqProject)}/step1/files?sample=${encodeURIComponent(sample)}`
      );
      if (!res.ok) { setStatus(`${sample}: HTTP ${res.status}`); return; }
      const data = await res.json();
      if (!data.bam || !data.reference_fasta) { setStatus(`${sample}: missing BAM/FASTA`); return; }
      const candidate = data.reference_fasta.split("/").pop();
      if (candidate !== refNameRef.current) {
        setStatus(`${sample}: reference ${candidate} ≠ ${refNameRef.current}`);
        return;
      }
      await browserRef.current.loadTrack({
        type: "alignment",
        format: "bam",
        name: sample,
        url: serveUrl(reqProject, data.bam),
        indexURL: serveUrl(reqProject, `${data.bam}.bai`),
      });
      loadedRef.current.add(sample);
      setMeta((prev) => ({ ...prev, trackCount: loadedRef.current.size }));
      setStatus("");
    } catch (err) {
      setStatus(`${sample}: ${err && err.message ? err.message : err}`);
    }
  }

  useEffect(() => {
    function onMessage(ev) {
      if (ev.origin !== window.location.origin) return;
      const data = ev.data;
      if (data && data.type === "vsnpAddSample" && data.project && data.sample) {
        addSample(data.project, data.sample);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!project || samples.length === 0) return;
    let cancelled = false;
    (async () => {
      const tracks = [];
      let referenceFastaPath = "";
      let referenceFaiPath = "";
      let refName = "";
      const skipped = [];
      for (const sample of samples) {
        try {
          const res = await fetch(
            `${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/files?sample=${encodeURIComponent(sample)}`
          );
          if (!res.ok) {
            skipped.push(`${sample} (HTTP ${res.status})`);
            continue;
          }
          const data = await res.json();
          if (!data.bam || !data.reference_fasta) {
            skipped.push(`${sample} (missing BAM/FASTA)`);
            continue;
          }
          if (!referenceFastaPath) {
            referenceFastaPath = data.reference_fasta;
            referenceFaiPath = `${data.reference_fasta}.fai`;
            refName = data.reference_fasta.split("/").pop();
          } else {
            const candidate = data.reference_fasta.split("/").pop();
            if (candidate !== refName) {
              skipped.push(`${sample} (reference ${candidate} ≠ ${refName})`);
              continue;
            }
          }
          tracks.push({ sample, bamPath: data.bam, baiPath: `${data.bam}.bai` });
        } catch (e) {
          skipped.push(`${sample} (${e && e.message ? e.message : e})`);
        }
      }
      if (cancelled) return;
      if (!referenceFastaPath || tracks.length === 0) {
        setStatus("Could not resolve any sample files.");
        return;
      }
      setMeta({ reference: refName, trackCount: tracks.length });
      const config = {
        reference: {
          id: refName.replace(/\.(fa|fasta)$/i, "") || "ref",
          fastaURL: serveUrl(project, referenceFastaPath),
          indexURL: serveUrl(project, referenceFaiPath),
        },
        tracks: tracks.map((t) => ({
          type: "alignment",
          format: "bam",
          name: t.sample,
          url: serveUrl(project, t.bamPath),
          indexURL: serveUrl(project, t.baiPath),
        })),
      };
      try {
        const browser = await igv.createBrowser(containerRef.current, config);
        if (cancelled) {
          try { igv.removeBrowser(browser); } catch (e) { /* ignore */ }
          return;
        }
        browserRef.current = browser;
        refNameRef.current = refName;
        for (const t of tracks) loadedRef.current.add(t.sample);
        setStatus(skipped.length ? `Loaded ${tracks.length}; skipped: ${skipped.join("; ")}` : "");
      } catch (err) {
        setStatus(`IGV failed to load: ${err && err.message ? err.message : err}`);
      }
    })();
    return () => {
      cancelled = true;
      if (browserRef.current) {
        try { igv.removeBrowser(browserRef.current); } catch (e) { /* ignore */ }
        browserRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const n = meta.trackCount;
    document.title = n
      ? `IGV · ${project || "?"} · ${n} sample${n === 1 ? "" : "s"}`
      : `IGV · ${project || "?"}`;
  }, [project, meta.trackCount]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      <div
        style={{
          padding: "0.5rem 0.8rem",
          borderBottom: "1px solid #ddd",
          background: "#f7f7f7",
          display: "flex",
          alignItems: "center",
          gap: "0.8rem",
        }}
      >
        <strong>IGV</strong>
        <span style={{ color: "#666", fontSize: "0.9em" }}>
          {meta.reference}
          {meta.trackCount ? ` · ${meta.trackCount} track${meta.trackCount === 1 ? "" : "s"}` : ""}
          {project ? ` · ${project}` : ""}
        </span>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "auto" }} />
    </div>
  );
}
