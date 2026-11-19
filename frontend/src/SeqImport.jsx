import { useEffect, useRef, useState } from "react";
import { InputHelp } from "./InputsTabs.jsx";

// FMDV mode: import sequences that are not reads. FASTA or GenBank files
// (dropped or chosen) and GenBank accessions (typed) go through one preview:
// one editable row per record with the VP1 the backend found, the metadata
// it read, and a role. A SAMPLE lands in step1/<name>/ and is typed and
// placed on the VP1 tree like any sample; a CONTEXT record joins the
// project's own comparator set (fmdv_context/<SEROTYPE>.*) and stands in the
// tree labelled lineage|country|year|accession. Commit is a background job.
// Backend: app.seq_import; design: docs/FMDV_SEQ_IMPORT.md.

const FIELDS = [
  ["serotype", "Serotype", 64],
  ["lineage", "Lineage", 110],
  ["country", "Country", 100],
  ["year", "Year", 52],
  ["host", "Host", 90],
  ["accession", "Accession", 96],
  ["isolate", "Isolate", 120],
];

export default function SeqImport({ apiBase, project, enabled, onImported }) {
  const [files, setFiles] = useState([]);
  const [accText, setAccText] = useState("");
  const [role, setRole] = useState("sample");
  const [preview, setPreview] = useState(null);
  const [rows, setRows] = useState({});     // id -> edited row
  const [picked, setPicked] = useState({}); // id -> bool
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [jobId, setJobId] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const inputRef = useRef(null);
  const pollRef = useRef(null);

  useEffect(() => {
    setPreview(null); setMsg(""); setJobId(""); setJobStatus(""); setFiles([]); setAccText("");
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
            setMsg("Imported. Samples are in Step 1 and typed; context records are in the project's VP1 set. Build a VP1 tree to place them.");
            setPreview(null); setFiles([]); setAccText("");
          } else {
            setMsg(`Import ${job.status}. The job log says which record failed.`);
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

  function addFiles(list) {
    const next = [...files];
    for (const f of Array.from(list || [])) {
      if (!next.some((x) => x.name === f.name && x.size === f.size)) next.push(f);
    }
    setFiles(next);
  }

  async function doPreview() {
    if (!project || (!files.length && !accText.trim())) return;
    setBusy(true); setMsg(""); setPreview(null); setJobId(""); setJobStatus("");
    try {
      const fd = new FormData();
      for (const f of files) fd.append("files", f, f.name);
      fd.append("accessions", accText);
      fd.append("role", role);
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/import_sequences/preview`, {
        method: "POST", body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg(data.detail || `Preview failed (HTTP ${res.status})`); return; }
      setPreview(data);
      const r = {}; const p = {};
      for (const row of data.rows) {
        r[row.id] = { ...row };
        p[row.id] = !row.problem && !row.exists && !(row.in_context && (row.role || "sample") === "context");
      }
      setRows(r); setPicked(p);
    } catch (e) {
      setMsg(`Preview failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function doImport() {
    if (!preview) return;
    const chosen = preview.rows.filter((r) => picked[r.id]).map((r) => {
      const e = rows[r.id] || r;
      return {
        id: r.id, name: e.name || "", role: e.role || "sample", serotype: e.serotype || "",
        lineage: e.lineage || "", country: e.country || "", year: e.year || "", host: e.host || "",
        accession: e.accession || "", isolate: e.isolate || "",
      };
    });
    if (!chosen.length) { setMsg("Nothing ticked."); return; }
    setBusy(true); setMsg("");
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/import_sequences`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: preview.token, rows: chosen }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg(data.detail || `Import failed (HTTP ${res.status})`); return; }
      setJobId(data.job_id); setJobStatus("running");
      setMsg(`Importing ${data.entries.length} record${data.entries.length === 1 ? "" : "s"}…`);
    } catch (e) {
      setMsg(`Import failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const edit = (id, key, value) => setRows({ ...rows, [id]: { ...(rows[id] || {}), [key]: value } });
  const nameCounts = {};
  for (const r of preview?.rows || []) {
    if (!picked[r.id]) continue;
    const k = ((rows[r.id] || r).name || "").trim();
    nameCounts[k] = (nameCounts[k] || 0) + 1;
  }
  const nPicked = Object.values(picked).filter(Boolean).length;
  const canPreview = enabled && !busy && (files.length > 0 || accText.trim().length > 0);
  const inputStyle = { width: "100%", fontSize: "11px", padding: "1px 3px", boxSizing: "border-box" };

  return (
    <div className="block">
      <InputHelp line="Drop FASTA or GenBank files, or type accessions; check the preview, then import.">
        <p>A consensus genome or a VP1 sequence from another lab, a GenBank flat file, or GenBank accessions
        (fetched from NCBI, or served from the library&apos;s cache when NCBI cannot be reached).</p>
        <p>Imported as a <b>sample</b>, a genome becomes simulated reads and goes through Step 1 like any sample
        (badged Simulated, QC not applied); a VP1 record is typed and placed on the VP1 tree without reads.
        Imported as <b>context</b>, the record joins this project&apos;s own comparator set and stands in the tree
        as lineage|country|year|accession.</p>
        <p>VP1 is cut from a genome by blastn. Country, year, host and isolate come from the GenBank record, a
        NanoFMDV-style header, or a metadata sheet; every field can be edited in the preview.{" "}
        <a href={`${apiBase}/api/fmdv/sequence_sheet_template`} download="sequence_sheet.tsv">Metadata sheet template</a></p>
      </InputHelp>
      <div
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}
        style={{ marginBottom: "4px" }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".fasta,.fa,.fna,.fas,.gb,.gbk,.genbank,.tsv,.csv,.txt"
          style={{ display: "none" }}
          onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
        />
        <button type="button" disabled={!enabled} onClick={() => inputRef.current?.click()}>Choose Files</button>
        <span>
          Or drop FASTA, GenBank (.gb) or a metadata sheet here{" "}
          <a href={`${apiBase}/api/fmdv/sequence_sheet_template`} download="sequence_sheet.tsv"
            title="A tab-separated sheet with a sample column and any of role, serotype, lineage, country, year, host, accession, isolate; drop it beside the FASTA.">(sheet template)</a>
        </span>
      </div>
      {files.length ? (
        <div className="muted" style={{ fontSize: "11px", marginBottom: "4px" }}>
          {files.map((f) => (
            <span key={f.name + f.size} style={{ marginRight: "8px", whiteSpace: "nowrap" }}>
              {f.name}{" "}
              <button type="button" className="ghost" style={{ fontSize: "10px", padding: "0 4px" }}
                onClick={() => setFiles(files.filter((x) => x !== f))} title="Remove from this import">×</button>
            </span>
          ))}
        </div>
      ) : null}
      <textarea
        placeholder="GenBank accessions, one per line (MN123456). Fetched from NCBI, or served from the library's cache when NCBI cannot be reached."
        value={accText}
        onChange={(e) => setAccText(e.target.value)}
        rows={3}
        disabled={!enabled}
        style={{ fontSize: "12px" }}
      />
      <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontSize: "12px" }}>
          Import as{" "}
          <select value={role} onChange={(e) => setRole(e.target.value)} disabled={!enabled} style={{ fontSize: "12px" }}>
            <option value="sample">samples (typed, reported, highlighted in the tree)</option>
            <option value="context">context (comparators in the tree and the typing)</option>
          </select>
        </label>
        <button type="button" onClick={doPreview} disabled={!canPreview}>
          {busy && !preview ? "Reading…" : "Preview"}
        </button>
      </div>
      {preview ? (
        <div style={{ marginTop: "6px" }}>
          <div className="muted" style={{ fontSize: "12px" }}>
            {preview.rows.length} record{preview.rows.length === 1 ? "" : "s"}
            {preview.ncbi?.requested ? ` · ${preview.ncbi.fetched} of ${preview.ncbi.requested} accession${preview.ncbi.requested === 1 ? "" : "s"} fetched${preview.ncbi.present ? `, ${preview.ncbi.present} already in the library or this project` : ""}` : ""}
            {preview.blastn === false ? " · blastn is not on the backend's PATH, so VP1 cannot be checked" : ""}
          </div>
          {preview.ncbi?.message ? (
            <div className="note error" style={{ fontSize: "12px", whiteSpace: "pre-wrap" }}>{preview.ncbi.message}</div>
          ) : null}
          {preview.problems?.length ? (
            <div className="note warning" style={{ fontSize: "12px", whiteSpace: "pre-wrap" }}>{preview.problems.join("\n")}</div>
          ) : null}
          <div style={{ overflowX: "auto" }}>
            <table style={{ fontSize: "11px", marginTop: "4px", minWidth: "900px" }}>
              <thead>
                <tr>
                  <th></th>
                  <th style={{ textAlign: "left" }}>Record</th>
                  <th style={{ textAlign: "left" }}>Name</th>
                  <th>Role</th>
                  {FIELDS.map(([k, label]) => <th key={k} style={{ textAlign: "left" }}>{label}</th>)}
                  <th style={{ textAlign: "left" }}>VP1</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((r) => {
                  const e = rows[r.id] || r;
                  const refused = !!r.problem;
                  const nm = (e.name || "").trim();
                  const dup = picked[r.id] && nameCounts[nm] > 1;
                  const isCtx = (e.role || "sample") === "context";
                  return (
                    <tr key={r.id} style={refused ? { opacity: 0.6 } : undefined}>
                      <td><input type="checkbox" checked={!!picked[r.id]} disabled={refused} onChange={(ev) => setPicked({ ...picked, [r.id]: ev.target.checked })} /></td>
                      <td title={`${r.header}\n${r.source}`} style={{ maxWidth: "160px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <span style={{ fontFamily: "monospace" }}>{r.header.split(" ")[0]}</span>
                        <div className="muted" style={{ fontSize: "10px" }}>
                          {r.kind} · {r.class} · {r.length.toLocaleString()} nt{r.n_count ? ` · ${r.n_count} N` : ""}
                        </div>
                      </td>
                      <td style={{ minWidth: "120px" }}>
                        <input type="text" value={e.name ?? ""} onChange={(ev) => edit(r.id, "name", ev.target.value)} disabled={refused}
                          style={{ ...inputStyle, borderColor: dup ? "var(--danger, #b00)" : undefined }}
                          title={isCtx ? "A context record is named <SEROTYPE>-<lineage or NA>_<accession>; this name is used only when it has no accession." : "The sample name Step 1 and Step 2 will use. Underscores become dashes."} />
                        {r.exists ? <div style={{ fontSize: "10px", color: "var(--danger, #b00)" }}>{r.exists}</div> : null}
                        {r.in_context ? (
                          <div style={{ fontSize: "10px", color: isCtx ? "var(--danger, #b00)" : undefined }} className={isCtx ? "" : "muted"}
                            title="The library or this project already holds this accession as a context record. It cannot be added as context twice; as a sample it is typed and highlighted in the tree.">
                            {r.in_context}{isCtx ? " (cannot be added as context again)" : ""}
                          </div>
                        ) : null}
                        {dup ? <div style={{ fontSize: "10px", color: "var(--danger, #b00)" }}>name used twice</div> : null}
                      </td>
                      <td>
                        <select value={e.role || "sample"} onChange={(ev) => edit(r.id, "role", ev.target.value)} disabled={refused} style={{ fontSize: "11px" }}>
                          <option value="sample">sample</option>
                          <option value="context">context</option>
                        </select>
                      </td>
                      {FIELDS.map(([k, , w]) => (
                        <td key={k} style={{ minWidth: `${w}px` }}>
                          <input type="text" value={e[k] ?? ""} onChange={(ev) => edit(r.id, k, ev.target.value)} disabled={refused} style={inputStyle}
                            title={k === "serotype" && r.serotype_note ? r.serotype_note : k === "lineage" ? "Topotype or lineage as the reference laboratories write it (I, EA-3, ME-SA/PanAsia-2). Left NA when not known; the typer and the tree treat NA as unannotated." : ""} />
                          {k === "serotype" && r.serotype_note ? <div style={{ fontSize: "10px", color: "var(--danger, #b00)" }}>⚠ {r.serotype_note}</div> : null}
                        </td>
                      ))}
                      <td style={{ whiteSpace: "nowrap" }} title={r.vp1 ? `Template ${r.vp1.template} at ${r.vp1.identity}% identity` : r.problem}>
                        {r.vp1 ? (
                          <>
                            {r.vp1.region_source === "imported_vp1" ? "whole record" : `${r.vp1.start}-${r.vp1.end} (${r.vp1.strand})`}
                            {" · "}{r.vp1.length} nt{typeof r.vp1.called_percent === "number" && r.vp1.called_percent < 100 ? ` · ${r.vp1.called_percent}% called` : ""}
                            <div className="muted" style={{ fontSize: "10px" }}>nearest {r.vp1.template} {r.vp1.identity}%</div>
                          </>
                        ) : (
                          <span style={{ color: "var(--danger, #b00)" }}>{r.problem}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "6px" }}>
            <button type="button" onClick={doImport} disabled={!enabled || busy || !!jobId || !nPicked || Object.values(nameCounts).some((c) => c > 1)}>
              Import {nPicked} record{nPicked === 1 ? "" : "s"}
            </button>
            <button type="button" className="ghost" onClick={() => { setPreview(null); setMsg(""); }}>Cancel</button>
          </div>
        </div>
      ) : null}
      {msg ? (
        <div className={`note${/fail|refused|not found|unreachable/i.test(msg) ? " error" : ""}`} style={{ marginTop: "6px", display: "flex", alignItems: "center", gap: "8px" }}>
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
