import React, { useEffect, useRef, useState } from "react";
import igv from "igv";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

// Translate the backend's structured 404 details for /step1/files into a
// human-readable label. Matches the contract in main.py:step1_files.
async function describeStep1FilesError(res) {
  if (res.status !== 404) return `HTTP ${res.status}`;
  try {
    const body = await res.json();
    if (body && body.detail === "imported_vcf") return "imported VCF — no BAM to load";
    if (body && body.detail === "no_step1") return "no Step 1 outputs";
  } catch (_) { /* fall through */ }
  return `HTTP ${res.status}`;
}

export default function IgvStandalone() {
  const params = new URLSearchParams(window.location.search);
  // Two URL formats supported:
  //   ?view=igv&tracks=proj:sample,proj:sample,...  (preferred, multi-project)
  //   ?view=igv&project=X&samples=A,B,C             (back-compat, single project)
  const initialTracks = (() => {
    const tracksParam = params.get("tracks");
    if (tracksParam) {
      return tracksParam
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean)
        .map((t) => {
          const i = t.indexOf(":");
          return i > 0
            ? { project: t.slice(0, i), sample: t.slice(i + 1) }
            : null;
        })
        .filter(Boolean);
    }
    const project = params.get("project") || "";
    const samples = (params.get("samples") || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return samples.map((sample) => ({ project, sample }));
  })();

  const distinctProjects = Array.from(new Set(initialTracks.map((t) => t.project).filter(Boolean)));
  const initialLocus = (params.get("locus") || "").trim();

  const [status, setStatus] = useState(initialTracks.length ? "Loading…" : "No samples specified.");
  const [meta, setMeta] = useState({ reference: "", trackCount: 0 });
  const browserRef = useRef(null);
  const containerRef = useRef(null);
  const projectRef = useRef(initialTracks[0] ? initialTracks[0].project : "");
  const refNameRef = useRef("");
  const loadedRef = useRef(new Set());

  async function addSample(reqProject, sample) {
    if (!browserRef.current) {
      setStatus("IGV not ready yet — wait for initial load.");
      return;
    }
    const trackKey = `${reqProject}:${sample}`;
    if (loadedRef.current.has(trackKey)) {
      setStatus(`${sample} already loaded.`);
      return;
    }
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(reqProject)}/step1/files?sample=${encodeURIComponent(sample)}`
      );
      if (!res.ok) {
        setStatus(`${sample}: ${await describeStep1FilesError(res)}`);
        return;
      }
      const data = await res.json();
      if (!data.bam || !data.reference_fasta) { setStatus(`${sample}: missing BAM/FASTA`); return; }
      const candidate = data.reference_fasta.split("/").pop();
      if (candidate !== refNameRef.current) {
        setStatus(`${sample}: reference ${candidate} ≠ ${refNameRef.current}`);
        return;
      }
      const displayName = reqProject !== projectRef.current ? `${reqProject}/${sample}` : sample;
      await browserRef.current.loadTrack({
        type: "alignment",
        format: "bam",
        name: displayName,
        url: serveUrl(reqProject, data.bam),
        indexURL: serveUrl(reqProject, `${data.bam}.bai`),
      });
      loadedRef.current.add(trackKey);
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
    if (initialTracks.length === 0) return;
    let cancelled = false;
    (async () => {
      const tracks = [];
      let referenceFastaPath = "";
      let referenceFaiPath = "";
      let referenceGffPath = "";
      let refProject = "";
      let refName = "";
      const skipped = [];
      for (const t of initialTracks) {
        const { project: tProject, sample } = t;
        try {
          const res = await fetch(
            `${API_BASE}/api/projects/${encodeURIComponent(tProject)}/step1/files?sample=${encodeURIComponent(sample)}`
          );
          if (!res.ok) {
            skipped.push(`${tProject}/${sample} (${await describeStep1FilesError(res)})`);
            continue;
          }
          const data = await res.json();
          if (!data.bam || !data.reference_fasta) {
            skipped.push(`${tProject}/${sample} (missing BAM/FASTA)`);
            continue;
          }
          if (!referenceFastaPath) {
            referenceFastaPath = data.reference_fasta;
            referenceFaiPath = `${data.reference_fasta}.fai`;
            referenceGffPath = data.reference_gff || "";
            refName = data.reference_fasta.split("/").pop();
            refProject = tProject;
          } else {
            const candidate = data.reference_fasta.split("/").pop();
            if (candidate !== refName) {
              skipped.push(`${tProject}/${sample} (reference ${candidate} ≠ ${refName})`);
              continue;
            }
          }
          tracks.push({ project: tProject, sample, bamPath: data.bam, baiPath: `${data.bam}.bai` });
        } catch (e) {
          skipped.push(`${tProject}/${sample} (${e && e.message ? e.message : e})`);
        }
      }
      if (cancelled) return;
      if (!referenceFastaPath || tracks.length === 0) {
        setStatus("Could not resolve any sample files.");
        return;
      }
      setMeta({ reference: refName, trackCount: tracks.length });
      // Reference GFF annotation track (genes/CDS/ORFs). Lives in the
      // reference options dir; the serve endpoint allows reference paths.
      // Placed first so it renders at the top, above the BAM tracks.
      const annotationTrack = referenceGffPath
        ? [{
            type: "annotation",
            format: referenceGffPath.toLowerCase().endsWith(".gff3") ? "gff3" : "gff",
            name: "Reference annotation",
            url: serveUrl(refProject, referenceGffPath),
            displayMode: "EXPANDED",
            visibilityWindow: -1,
          }]
        : [];
      const bamTracks = tracks.map((t) => {
        const displayName = t.project !== refProject ? `${t.project}/${t.sample}` : t.sample;
        return {
          type: "alignment",
          format: "bam",
          name: displayName,
          url: serveUrl(t.project, t.bamPath),
          indexURL: serveUrl(t.project, t.baiPath),
        };
      });
      const config = {
        reference: {
          id: refName.replace(/\.(fa|fasta)$/i, "") || "ref",
          fastaURL: serveUrl(refProject, referenceFastaPath),
          indexURL: serveUrl(refProject, referenceFaiPath),
        },
        ...(initialLocus ? { locus: initialLocus } : {}),
        tracks: [...annotationTrack, ...bamTracks],
      };
      try {
        const browser = await igv.createBrowser(containerRef.current, config);
        if (cancelled) {
          try { igv.removeBrowser(browser); } catch (e) { /* ignore */ }
          return;
        }
        browserRef.current = browser;
        refNameRef.current = refName;
        projectRef.current = refProject;
        for (const t of tracks) loadedRef.current.add(`${t.project}:${t.sample}`);
        setMeta({ reference: refName, trackCount: tracks.length });
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
      ? `IGV · ${n} sample${n === 1 ? "" : "s"}`
      : "IGV";
  }, [meta.trackCount]);

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
          {distinctProjects.length ? ` · ${distinctProjects.join(", ")}` : ""}
        </span>
        {status ? <span style={{ color: "#b34", fontSize: "0.9em" }}>{status}</span> : null}
      </div>
      <div ref={containerRef} style={{ flex: 1, overflow: "auto" }} />
    </div>
  );
}
