import React, { useEffect, useRef, useState } from "react";
import igv from "igv";
import { normalizeLocus, goToLocus, landingLocus } from "./igvLocus.js";
import Elapsed from "./Elapsed.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { installIgvTheme, whenShadowRoot } from "./igvTheme.js";
import { formatElapsed } from "./elapsedFormat.js";

const API_BASE = import.meta.env.VITE_API_URL || ".";

function serveUrl(project, absPath) {
  return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
}

// The reads track for one sample. When the backend has samtools, igv.js is
// pointed at the reads-window endpoint (its "htsget" source) and fetches only
// the reads overlapping what is on screen; otherwise it reads the BAM by byte
// range, which on a small genome means every read of the contig — see
// reads_ticket in main.py.
function readsTrack(project, sample, name, data) {
  if (data.reads_window) {
    return {
      type: "alignment",
      format: "bam",
      sourceType: "htsget",
      name,
      url: `${API_BASE}/api/projects/${encodeURIComponent(project)}/reads/${encodeURIComponent(sample)}`,
    };
  }
  return {
    type: "alignment",
    format: "bam",
    name,
    url: serveUrl(project, data.bam),
    indexURL: serveUrl(project, `${data.bam}.bai`),
  };
}

// ---------------------------------------------------------------------------
// What igv.js is fetching right now.
//
// igv.js shows one spinner for the whole load and says nothing about which
// resource it is waiting for or how far along it is. On the HPC that spinner
// sat for minutes with no way to tell a slow transfer from a dead one. igv.js
// loads every resource through XMLHttpRequest, so wrapping open/send ONCE
// records each request to the serve endpoint — which file, which byte range,
// how many bytes have arrived — and the bar above the viewer lists whatever is
// still open, with a counter. When the wait is a 300 MB block of reads on a
// slow link, the bar says so; when nothing is moving, that shows too.
const transfers = new Map();
const transferListeners = new Set();
let transferSeq = 0;
// Everything finished since the page opened, for the summary once loaded.
const transferTotals = { count: 0, bytes: 0 };

function notifyTransfers() {
  for (const fn of transferListeners) fn();
}

function describeServeUrl(url) {
  try {
    const u = new URL(url, window.location.href);
    const window_ = /\/reads\/([^/]+)\/data$/.exec(u.pathname);
    if (window_) {
      const sample = decodeURIComponent(window_[1]);
      const header = u.searchParams.get("class") === "header";
      return { name: header ? `${sample} header` : `${sample} ${u.searchParams.get("referenceName") || ""}:${u.searchParams.get("start") || 0}-${u.searchParams.get("end") || ""}`, kind: header ? "reads header" : "reads in view" };
    }
    if (!/\/serve$/.test(u.pathname)) return null;
    const name = (u.searchParams.get("path") || "").split("/").pop();
    const lower = name.toLowerCase();
    const kind = lower.endsWith(".bai") || lower.endsWith(".csi") ? "reads index"
      : lower.endsWith(".bam") || lower.endsWith(".cram") ? "reads"
      : lower.endsWith(".fai") ? "sequence index"
      : /\.(fa|fasta|fna)$/.test(lower) ? "sequence"
      : /\.vcf(\.gz)?$/.test(lower) ? "calls"
      : /\.gff3?(\.gz)?$/.test(lower) ? "annotation"
      : "file";
    return { name, kind };
  } catch (_) {
    return null;
  }
}

function rangeSize(range) {
  const m = /^bytes=(\d+)-(\d+)$/.exec(range || "");
  return m ? Number(m[2]) - Number(m[1]) + 1 : 0;
}

(function watchIgvTransfers() {
  if (typeof XMLHttpRequest === "undefined" || XMLHttpRequest.prototype.__vsnpWatched) return;
  XMLHttpRequest.prototype.__vsnpWatched = true;
  const open = XMLHttpRequest.prototype.open;
  const setRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__vsnp = { url: String(url), range: "" };
    return open.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (this.__vsnp && String(name).toLowerCase() === "range") this.__vsnp.range = String(value);
    return setRequestHeader.call(this, name, value);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    const info = this.__vsnp && describeServeUrl(this.__vsnp.url);
    if (info) {
      const id = ++transferSeq;
      const entry = {
        id, ...info, range: this.__vsnp.range, started: Date.now(),
        loaded: 0, total: rangeSize(this.__vsnp.range), status: 0,
      };
      transfers.set(id, entry);
      this.addEventListener("progress", (e) => {
        entry.loaded = e.loaded;
        if (e.lengthComputable && e.total) entry.total = e.total;
        notifyTransfers();
      });
      this.addEventListener("loadend", () => {
        entry.status = this.status;
        transfers.delete(id);
        transferTotals.count += 1;
        transferTotals.bytes += entry.loaded;
        notifyTransfers();
      });
      notifyTransfers();
    }
    return send.apply(this, args);
  };
})();

function useIgvTransfers() {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    transferListeners.add(fn);
    const tick = setInterval(fn, 1000);   // the counters tick even while no bytes arrive
    return () => { transferListeners.delete(fn); clearInterval(tick); };
  }, []);
  return Array.from(transfers.values());
}

function formatBytes(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} kB`;
  return `${n} B`;
}

function IgvTransfers({ loading }) {
  const open = useIgvTransfers();
  if (!open.length) {
    // Still loading with nothing on the wire: igv.js is working on what it
    // has, so say how much that was.
    if (loading && transferTotals.count) {
      return (
        <span className="igv-page-transfers">
          received {transferTotals.count} file{transferTotals.count === 1 ? "" : "s"}, {formatBytes(transferTotals.bytes)}
        </span>
      );
    }
    return null;
  }
  const now = Date.now();
  return (
    <span className="igv-page-transfers" title="What the viewer is fetching right now, and how much of it has arrived">
      {open.slice(0, 4).map((t) => (
        <span key={t.id} className="igv-page-transfer">
          {t.kind} <code>{t.name}</code>
          {" "}
          {t.total ? `${formatBytes(t.loaded)} of ${formatBytes(t.total)}` : formatBytes(t.loaded)}
          {" · "}
          {formatElapsed((now - t.started) / 1000)}
        </span>
      ))}
      {open.length > 4 ? <span>+{open.length - 4} more</span> : null}
    </span>
  );
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
      if (data.bam) await browserRef.current.loadTrack(readsTrack(reqProject, sample, `${displayName} · reads`, data));
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
        // Answer immediately, before doing the work. The preview page cannot
        // otherwise tell a live viewer from a tab that is still open but no
        // longer running this app — `window.closed` is false for both, which is
        // routine under OOD where the URL carries a compute node and port that
        // change when the session is recycled. Without this ack it posted into
        // the void and the click did nothing at all.
        try {
          if (ev.source) ev.source.postMessage({ type: "vsnpIgvAck" }, window.location.origin);
        } catch (e) { /* the ack is best-effort; the launch still proceeds */ }
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
    let stopTheme = () => {};
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
          files: data,
          bamPath: data.bam || "",
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
          out.push(readsTrack(t.project, t.sample, `${displayName} · reads`, t.files));
        }
        return out;
      });
      const config = {
        // igv.js otherwise begins by fetching its public genome list from
        // igv.org, then GitHub when that fails — up to twelve seconds of
        // timeouts on a network that blocks either, for a list this viewer
        // never uses: the reference is always the project's own FASTA.
        loadDefaultGenomes: false,
        reference: {
          id: refName.replace(/\.(fa|fasta)$/i, "") || "ref",
          fastaURL: serveUrl(refProject, referenceFastaPath),
          indexURL: serveUrl(refProject, referenceFaiPath),
          // Deliberately NOT `wholeGenomeView: false`, though that is the
          // obvious way to stop igv.js opening on its "all" pseudo-contig where
          // alignment tracks render nothing.
          //
          // v0.4.54 did exactly that and broke the viewer outright, including
          // from the Step 1 pane, which had been fine. Setting the flag skips a
          // whole block of igv.js's genome setup, leaving `wgChromosomeNames`
          // undefined — and igv.js iterates it with `for...of` in places that
          // do not check, so it throws. Turning a library option off is not the
          // same as changing where the viewer looks.
          //
          // So the config stays on the path igv.js is tested for, and landing
          // on a real contig is done by navigating after the browser is built
          // (see landingLocus). Same result, none of the initialisation
          // skipped, and the whole-genome view remains available in the dropdown.
        },
        ...(initialLocus ? { locus: initialLocus } : {}),
        tracks: sampleTracks,
      };
      try {
        // The viewer takes the page's appearance the moment igv.js attaches
        // its shadow root, and follows the switch in the bar from then on.
        whenShadowRoot(containerRef.current, (host) => { stopTheme = installIgvTheme(host); });
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
        const wanted = landingLocus(browser, pendingLocusRef.current || initialLocus);
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
      stopTheme();
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
    <div className="igv-page">
      <div className="igv-page-bar">
        <strong>IGV</strong>
        <span className="igv-page-meta">
          {meta.reference}
          {meta.trackCount ? ` · ${meta.trackCount} track${meta.trackCount === 1 ? "" : "s"}` : ""}
          {distinctProjects.length ? ` · ${distinctProjects.join(", ")}` : ""}
        </span>
        {status ? (
          <span className="igv-page-status">
            {status}
            {status.endsWith("…") ? <> <Elapsed /></> : null}
          </span>
        ) : null}
        <IgvTransfers loading={status.endsWith("…")} />
        <ThemeToggle />
      </div>
      <div ref={containerRef} className="igv-page-body" />
    </div>
  );
}
