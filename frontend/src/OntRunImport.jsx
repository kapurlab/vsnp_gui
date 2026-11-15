import { useEffect, useRef, useState } from "react";

// Import a MinKNOW run folder into the project's download/ as one FASTQ per
// barcode. Preview first (POST import_run/preview: the barcodes found, their
// chunk counts and sizes, and the sample names the run's sample_sheet.csv
// proposes), let the names be edited, then commit (POST import_run), which
// runs the concatenation as a background job. Not FMDV specific: the stock
// Inputs page treats every *.fastq.gz as its own sample, so a raw run folder
// gave nothing or one sample per 4,000-read chunk. See backend/app/ont_import.py.

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

export default function OntRunImport({ apiBase, project, enabled, onImported }) {
  const [runDir, setRunDir] = useState("");
  const [preview, setPreview] = useState(null);
  const [names, setNames] = useState({});      // barcode -> sample name as edited
  const [picked, setPicked] = useState({});    // barcode -> bool
  const [unclassified, setUnclassified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [jobId, setJobId] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const pollRef = useRef(null);

  useEffect(() => {
    // A different project: the preview belonged to the old one.
    setPreview(null); setMsg(""); setJobId(""); setJobStatus("");
  }, [project]);

  useEffect(() => {
    if (!jobId) return undefined;
    const tick = async () => {
      try {
        const res = await fetch(`${apiBase}/api/jobs/${jobId}`);
        if (!res.ok) return;
        const job = await res.json();
        setJobStatus(job.status || "");
        if (job.status && job.status !== "running" && job.status !== "queued") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          if (job.status === "succeeded") {
            setMsg("Imported. The samples are in download/ and ready for Step 1.");
            setPreview(null);
          } else {
            setMsg(`Import ${job.status}. See the job log for the sample that failed.`);
          }
          if (onImported) onImported(job);
        }
      } catch (_) { /* transient */ }
    };
    pollRef.current = setInterval(tick, 1500);
    tick();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function doPreview() {
    if (!project || !runDir.trim()) return;
    setBusy(true); setMsg(""); setPreview(null); setJobId(""); setJobStatus("");
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/import_run/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_dir: runDir.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg(data.detail || `Preview failed (HTTP ${res.status})`); return; }
      setPreview(data);
      const n = {}; const p = {};
      for (const r of data.rows) { n[r.barcode] = r.sample; p[r.barcode] = !r.exists; }
      setNames(n); setPicked(p); setUnclassified(false);
    } catch (e) {
      setMsg(`Preview failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    if (!preview) return;
    const samples = preview.rows
      .filter((r) => picked[r.barcode])
      .map((r) => ({ barcode: r.barcode, sample: (names[r.barcode] || "").trim() }));
    if (!samples.length && !unclassified) { setMsg("Tick at least one barcode."); return; }
    setBusy(true); setMsg("");
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/import_run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_dir: preview.run_dir, samples, include_unclassified: unclassified }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg(data.detail || `Import failed (HTTP ${res.status})`); return; }
      setJobId(data.job_id);
      setJobStatus("running");
      setMsg(`Importing ${data.samples.length} sample${data.samples.length === 1 ? "" : "s"}…`);
    } catch (e) {
      setMsg(`Import failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const nameCounts = {};
  for (const r of preview?.rows || []) {
    if (!picked[r.barcode]) continue;
    const k = (names[r.barcode] || "").trim();
    nameCounts[k] = (nameCounts[k] || 0) + 1;
  }
  const selectedBytes = (preview?.rows || []).filter((r) => picked[r.barcode]).reduce((a, r) => a + r.bytes, 0);

  return (
    <div className="block">
      <h3>Import ONT run</h3>
      <div className="muted" style={{ fontSize: "12px", margin: "0 0 6px" }}>
        A MinKNOW run folder (fastq_pass/barcodeNN/). Each barcode&apos;s chunk files
        become one FASTQ named from the run&apos;s <code>sample_sheet.csv</code>
        (columns <code>barcode,sample_id</code>) or from the barcode.{" "}
        <a href={`${apiBase}/api/ont/sample_sheet_template`} download="sample_sheet.csv">Sheet template</a>
      </div>
      <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
        <input
          type="text"
          value={runDir}
          onChange={(e) => setRunDir(e.target.value)}
          placeholder="/path/to/run folder (contains fastq_pass/ or barcodeNN/)"
          style={{ flex: 1, fontSize: "12px" }}
          disabled={!enabled}
          onKeyDown={(e) => { if (e.key === "Enter") doPreview(); }}
        />
        <button type="button" onClick={doPreview} disabled={!enabled || busy || !runDir.trim()}>
          {busy && !preview ? "Scanning…" : "Preview"}
        </button>
      </div>
      {preview ? (
        <div style={{ marginTop: "6px" }}>
          <div className="muted" style={{ fontSize: "12px" }}>
            {preview.rows.length} barcode{preview.rows.length === 1 ? "" : "s"} ({preview.layout}){" "}
            {preview.sheet ? `· names from ${preview.sheet}` : "· no sample_sheet.csv in the run, names default to the barcode"}
          </div>
          {preview.sheet_problems?.length ? (
            <div className="note warning" style={{ fontSize: "12px", whiteSpace: "pre-wrap" }}>
              {preview.sheet_problems.join("\n")}
            </div>
          ) : null}
          <table style={{ width: "100%", fontSize: "12px", marginTop: "4px" }}>
            <thead>
              <tr><th></th><th style={{ textAlign: "left" }}>Barcode</th><th>Files</th><th>Size</th><th style={{ textAlign: "left" }}>Sample name</th></tr>
            </thead>
            <tbody>
              {preview.rows.map((r) => {
                const nm = (names[r.barcode] || "").trim();
                const dup = picked[r.barcode] && nameCounts[nm] > 1;
                return (
                  <tr key={r.barcode}>
                    <td><input type="checkbox" checked={!!picked[r.barcode]} onChange={(e) => setPicked({ ...picked, [r.barcode]: e.target.checked })} /></td>
                    <td style={{ fontFamily: "monospace" }}>{r.barcode}</td>
                    <td style={{ textAlign: "right" }}>{r.files}</td>
                    <td style={{ textAlign: "right" }}>{fmtBytes(r.bytes)}</td>
                    <td>
                      <input
                        type="text"
                        value={names[r.barcode] ?? ""}
                        onChange={(e) => setNames({ ...names, [r.barcode]: e.target.value })}
                        style={{ width: "100%", fontSize: "12px", borderColor: dup ? "var(--danger, #b00)" : undefined }}
                        title={r.renamed ? `Sheet says ${r.sheet_sample_id}; underscores become dashes because vsnp3 cuts a name at its first underscore` : ""}
                      />
                      {r.exists ? <div className="muted" style={{ fontSize: "11px" }}>already in download/</div> : null}
                      {r.renamed ? <div className="muted" style={{ fontSize: "11px" }}>sheet: {r.sheet_sample_id}</div> : null}
                      {dup ? <div style={{ fontSize: "11px", color: "var(--danger, #b00)" }}>name used twice</div> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {preview.unclassified?.files ? (
            <label style={{ fontSize: "12px", display: "block", marginTop: "4px" }}>
              <input type="checkbox" checked={unclassified} onChange={(e) => setUnclassified(e.target.checked)} disabled={preview.unclassified.exists} />{" "}
              also import unclassified ({preview.unclassified.files} files, {fmtBytes(preview.unclassified.bytes)})
              {preview.unclassified.exists ? " (already in download/)" : ""}
            </label>
          ) : null}
          <div style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "6px" }}>
            <button type="button" onClick={doImport} disabled={!enabled || busy || !!jobId || Object.values(nameCounts).some((c) => c > 1)}>
              Import {Object.values(picked).filter(Boolean).length} sample{Object.values(picked).filter(Boolean).length === 1 ? "" : "s"} ({fmtBytes(selectedBytes)})
            </button>
            <button type="button" className="ghost" onClick={() => { setPreview(null); setMsg(""); }}>Cancel</button>
          </div>
        </div>
      ) : null}
      {msg ? (
        <div className={`note${/fail|refused|not found/i.test(msg) ? " error" : ""}`} style={{ marginTop: "6px", display: "flex", alignItems: "center", gap: "8px" }}>
          {jobStatus === "running" || jobStatus === "queued" ? <span className="pulse-dot" /> : null}
          <span style={{ flex: 1 }}>{msg}</span>
          {!jobId || (jobStatus && jobStatus !== "running" && jobStatus !== "queued") ? (
            <button type="button" className="ghost" style={{ fontSize: "11px", padding: "2px 8px" }} onClick={() => { setMsg(""); setJobId(""); setJobStatus(""); }}>Dismiss</button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
