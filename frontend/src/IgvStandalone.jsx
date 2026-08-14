import React, { useEffect, useRef, useState } from "react";
import igv from "igv";
import { normalizeLocus, goToLocus } from "./igvLocus.js";

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
  const initialLocus = normalizeLocus(params.get("locus") || "");

  const [status, setStatus] = useState(initialTracks.length ? "Loading…" : "No samples specified.");
  const [meta, setMeta] = useState({ reference: "", trackCount: 0 });
  const browserRef = useRef(null);
  const containerRef = useRef(null);
  const projectRef = useRef(initialTracks[0] ? initialTracks[0].project : "");
  const refNameRef = useRef("");
  const loadedRef = useRef(new Set());
  // Launches that arrived before the browser existed, replayed once it does.
  const pendingRef = useRef([]);
  const pendingLocusRef = useRef("");

  async function addSample(reqProject, sample) {
    if (!browserRef.current) {
      // Queue it instead of dropping it. The initial load takes seconds (index
      // fetches over the serve endpoint), and a click on the cascade table during
      // that window used to be discarded with a message that then stayed on screen
      // after the load finished — so the user saw "IGV not ready yet" over a
      // perfectly loaded viewer, at the wrong locus, with their variant missing,
      // and no indication that clicking again was what was needed.
      pendingRef.current.push([reqProject, sample]);
      setStatus("Loading IGV — your sample will be added when it finishes…");
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
      if (!data.reference_fasta) { setStatus(`${sample}: no reference`); return; }
      if (!data.bam && !data.source_vcf) { setStatus(`${sample}: no BAM or VCF`); return; }
      const candidate = data.reference_fasta.split("/").pop();
      if (candidate !== refNameRef.current) {
        setStatus(`${sample}: reference ${candidate} ≠ ${refNameRef.current}`);
        return;
      }
      const displayName = reqProject !== projectRef.current ? `${reqProject}/${sample}` : sample;
      const callsVcf = data.annotated_vcf || data.source_vcf || "";
      if (callsVcf) {
        try {
          await browserRef.current.loadTrack({
            type: "variant",
            format: "vcf",
            name: `${displayName} · calls`,
            url: serveUrl(reqProject, callsVcf),
            indexed: false,
            displayMode: "EXPANDED",
            height: 30,
          });
        } catch (_) { /* non-fatal: drop the calls track, keep the BAM */ }
      }
      if (data.bam) await browserRef.current.loadTrack({
        type: "alignment",
        format: "bam",
        name: `${displayName} · reads`,
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

  // Cascade-table additive launches arrive here as { type: "vsnpIgvLaunch",
  // url: "<full launcher URL>" }. We parse the same URL params the initial
  // load uses (tracks, locus), then add any samples not already loaded and
  // navigate to the locus — so clicking variant after variant in the
  // cascade table builds up the cohort in this single IGV view.
  async function handleIgvLaunch(url) {
    let tracksParam = "";
    let locusParam = "";
    try {
      const u = new URL(url, window.location.origin);
      tracksParam = u.searchParams.get("tracks") || "";
      locusParam = (u.searchParams.get("locus") || "").trim();
    } catch (e) {
      setStatus(`Bad launch URL: ${e && e.message ? e.message : e}`);
      return;
    }
    const requested = tracksParam
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((t) => {
        const i = t.indexOf(":");
        return i > 0 ? { project: t.slice(0, i), sample: t.slice(i + 1) } : null;
      })
      .filter(Boolean);
    for (const t of requested) {
      const key = `${t.project}:${t.sample}`;
      if (!loadedRef.current.has(key)) {
        await addSample(t.project, t.sample);
      }
    }
    if (locusParam) {
      if (browserRef.current) {
        const navErr = await goToLocus(browserRef.current, locusParam);
        if (navErr) setStatus(navErr);
      } else {
        // Still loading: remember where they wanted to go. Dropping it left the
        // user at the initial whole-genome view, where an alignment track shows
        // "Zoom in to see features" and looks broken.
        pendingLocusRef.current = locusParam;
      }
    }
    try { window.focus(); } catch (e) { /* ignore */ }
  }

  useEffect(() => {
    function onMessage(ev) {
      if (ev.origin !== window.location.origin) return;
      const data = ev.data;
      if (!data) return;
      if (data.type === "vsnpAddSample" && data.project && data.sample) {
        addSample(data.project, data.sample);
        return;
      }
      if (data.type === "vsnpIgvLaunch" && data.url) {
        handleIgvLaunch(data.url);
        return;
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
      // Resolve every sample's step1 files in parallel — the per-call cost
      // is ~200ms RTT and a sequential for/await chain serializes that into
      // N×RTT (~3s wall time for 14 samples). Promise.all collapses it to
      // ~1×RTT. Reference-consistency check runs after all responses are
      // in, so it stays single-pass and deterministic.
      const resolutions = await Promise.all(
        initialTracks.map(async (t) => {
          const { project: tProject, sample } = t;
          try {
            const res = await fetch(
              `${API_BASE}/api/projects/${encodeURIComponent(tProject)}/step1/files?sample=${encodeURIComponent(sample)}`
            );
            if (!res.ok) {
              return { t, error: await describeStep1FilesError(res) };
            }
            const data = await res.json();
            return { t, data };
          } catch (e) {
            return { t, error: e && e.message ? e.message : String(e) };
          }
        })
      );

      for (const r of resolutions) {
        const { project: tProject, sample } = r.t;
        if (r.error) {
          skipped.push(`${tProject}/${sample} (${r.error})`);
          continue;
        }
        const data = r.data;
        // Imported-VCF samples (kind === "imported_vcf") arrive with empty
        // `bam` but a populated `source_vcf` — they're still loadable as a
        // calls-only track. Require either bam+ref OR source_vcf+ref.
        if (!data.reference_fasta) {
          skipped.push(`${tProject}/${sample} (no reference)`);
          continue;
        }
        if (!data.bam && !data.source_vcf) {
          skipped.push(`${tProject}/${sample} (no BAM or VCF)`);
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
        tracks.push({
          project: tProject,
          sample,
          bamPath: data.bam || "",
          baiPath: data.bam ? `${data.bam}.bai` : "",
          // Prefer the rich annotated VCF when it exists (step1-derived,
          // has gene/product/AA in the ID column). Fall back to the bare
          // source_vcf for imported samples — fewer fields on hover, but
          // variant positions and basic INFO still visible.
          annotatedVcfPath: data.annotated_vcf || "",
          sourceVcfPath: data.source_vcf || "",
        });
      }
      if (cancelled) return;
      if (!referenceFastaPath || tracks.length === 0) {
        setStatus("Could not resolve any sample files.");
        return;
      }
      setMeta({ reference: refName, trackCount: tracks.length });
      // Reference GFF annotation track (genes/CDS/ORFs), loaded AFTER the browser
      // exists — see below. It is not part of the createBrowser config on purpose:
      // igv.js rejects the whole call if any track's resource fails, so one
      // unreachable GFF used to leave the user with no viewer at all, just
      // "IGV failed to load: Error accessing resource … status: 400". The reads
      // and the calls are what they came for; the annotation is an extra.
      const annotationConfig = referenceGffPath
        ? {
            type: "annotation",
            format: referenceGffPath.toLowerCase().endsWith(".gff3") ? "gff3" : "gff",
            name: "Reference annotation",
            url: serveUrl(refProject, referenceGffPath),
            displayMode: "EXPANDED",
            visibilityWindow: -1,
          }
        : null;
      // For each sample, interleave a "calls" variant track immediately
      // above its BAM track. Prefer the annotated VCF (step1-derived, has
      // gene/product/AA in the ID column → rich on-hover info) over the
      // bare source_vcf (imports — variant positions still visible, fewer
      // fields on hover). The BAM (reads) is skipped for imported-VCF
      // samples that don't have one.
      const sampleTracks = tracks.flatMap((t) => {
        const displayName = t.project !== refProject ? `${t.project}/${t.sample}` : t.sample;
        const out = [];
        const callsVcf = t.annotatedVcfPath || t.sourceVcfPath;
        if (callsVcf) {
          out.push({
            type: "variant",
            format: "vcf",
            name: `${displayName} · calls`,
            url: serveUrl(t.project, callsVcf),
            indexed: false,
            displayMode: "EXPANDED",
            height: 30,
          });
        }
        if (t.bamPath) {
          out.push({
            type: "alignment",
            format: "bam",
            name: `${displayName} · reads`,
            url: serveUrl(t.project, t.bamPath),
            indexURL: serveUrl(t.project, t.baiPath),
          });
        }
        return out;
      });
      const config = {
        reference: {
          id: refName.replace(/\.(fa|fasta)$/i, "") || "ref",
          fastaURL: serveUrl(refProject, referenceFastaPath),
          indexURL: serveUrl(refProject, referenceFaiPath),
          // NO whole-genome view. igv.js builds a pseudo-chromosome called
          // "all" whenever a reference has more than one contig, and lands
          // there by default — but its alignment readers return no features for
          // chr "all", so the reads track draws EMPTY. On an 8-segment
          // influenza reference that is what "I click a SNP and nothing
          // happens" actually was: the viewer opened on a view that cannot
          // show reads. It is also why opening a virus from the Step 1 pane
          // needed a manual click on a segment first.
          //
          // A single-chromosome reference (MTBC0) never gets the pseudo-contig,
          // so it always landed on real sequence — that, and not the segment
          // count in itself, is the whole difference in behaviour between the
          // TB and influenza projects.
          //
          // Nothing is lost by turning it off: every reference here exists to
          // align reads against, and the whole-genome view shows neither reads
          // nor sequence. igv.js now opens on the first contig, and the contig
          // dropdown still lists them all.
          wholeGenomeView: false,
        },
        ...(initialLocus ? { locus: initialLocus } : {}),
        tracks: sampleTracks,
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
        // The annotation track, now that a failure can only cost the annotation.
        const notes = skipped.length ? [`skipped: ${skipped.join("; ")}`] : [];
        if (annotationConfig) {
          try {
            await browser.loadTrack(annotationConfig);
          } catch (e) {
            notes.push("reference annotation unavailable (gene track off)");
          }
        }
        // Replay anything clicked while we were loading (see addSample) BEFORE
        // navigating, so the navigation is the last thing to touch the view.
        // It used to come first and without an await, which left a navigation
        // and a track load interleaving inside igv.js's view update.
        const queued = pendingRef.current.splice(0);
        for (const [qProject, qSample] of queued) {
          await addSample(qProject, qSample);
        }
        // Navigate explicitly, even though config.locus was set above:
        // createBrowser drops it on some genomes (observed on MTBC0 4.4 Mb,
        // which lands at whole-contig view with locus set). search() is the
        // same path a user takes when pasting a position by hand.
        const wanted = pendingLocusRef.current || initialLocus;
        pendingLocusRef.current = "";
        const navErr = await goToLocus(browser, wanted);
        if (navErr) notes.push(navErr);
        setStatus(notes.length ? `Loaded ${tracks.length}; ${notes.join("; ")}` : "");
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
