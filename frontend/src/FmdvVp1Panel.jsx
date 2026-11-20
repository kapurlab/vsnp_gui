import { useEffect, useRef, useState } from "react";
import FmdvVp1Tree from "./FmdvVp1Tree.jsx";

// The VP1 tab of Step 2 in FMDV mode: what every sample is, by VP1, and the
// VP1 tree. This is the first cut of Step 2 because serotype, topotype and
// lineage are defined on VP1 and that is the question an outbreak sample
// asks first; the whole-genome SNP comparison on one lineage reference is
// the second tab, for samples already known to share a lineage.
//
// The table reads fmdv_vp1_type.json per sample (bin/fmdv_vp1_type.py:
// blastn of the VP1 consensus against the GenBank VP1 sets). Step 1 types
// each sample as it finishes; "Type VP1" retypes the whole project, for
// samples run before typing existed or after the context sets change.
//
// WHAT IS ON DISK NOW, not what was there when the project was opened. The
// table and the tree's serotype list both come from one fetch of the project's
// FMDV info, and that fetch used to happen only when a project was selected,
// after an import, and after Type VP1. Step 1 writing a typing result for
// every sample changed nothing on screen: a project whose two imported genomes
// were typed at import still read "2 of 2 typed" and offered "O (2 samples)"
// after Step 1 had typed the whole run (Ames HPC, 2026-11-20). The panel now
// reads again when it is shown, and each time another Step 1 sample completes
// (`step1Done`), so it follows a run in progress too.
export default function FmdvVp1Panel({ apiBase, project, fmdvInfo, disabled, onInfo, onTreeDone, step1Done = 0, treeBuilding = false }) {
  const [jobId, setJobId] = useState("");
  const [msg, setMsg] = useState("");
  const pollRef = useRef(null);
  const projectRef = useRef(project);
  projectRef.current = project;

  useEffect(() => { setJobId(""); setMsg(""); }, [project]);

  async function reload() {
    const asked = project;
    if (!asked) return;
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(asked)}/fmdv`);
      if (!res.ok) return;
      const info = await res.json();
      // An answer about a project the user has since left is not shown.
      if (projectRef.current === asked && onInfo) onInfo(info);
    } catch (_) { /* transient */ }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [project, step1Done]);

  useEffect(() => {
    if (!jobId) return undefined;
    const tick = async () => {
      try {
        const res = await fetch(`${apiBase}/api/jobs/${jobId}`);
        if (!res.ok) return;
        const job = await res.json();
        if (job.status && job.status !== "running" && job.status !== "queued") {
          clearInterval(pollRef.current); pollRef.current = null; setJobId("");
          setMsg(job.status === "succeeded" ? "" : `VP1 typing ${job.status}. The job log says which sample failed.`);
          reload();
        }
      } catch (_) { /* transient */ }
    };
    pollRef.current = setInterval(tick, 2000);
    tick();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobId]);

  async function retype() {
    setMsg("");
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/fmdv/vp1_type`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg(body.detail || `HTTP ${res.status}`); return; }
      setJobId(body.job_id);
    } catch (e) { setMsg(`VP1 typing: ${e.message || e}`); }
  }

  const chosen = fmdvInfo?.chosen || {};
  const typing = fmdvInfo?.vp1_typing || {};
  const samples = Array.from(new Set([...Object.keys(chosen), ...Object.keys(typing)])).sort();
  const typed = samples.filter((s) => typing[s]?.status === "typed").length;
  const running = !!jobId;

  const pct = (v) => (typeof v === "number" ? `${v.toFixed(1)}%` : "-");
  const nearest = (t) => {
    const n = t?.nearest || {};
    const bits = [n.accession, n.isolate && n.isolate.length > 28 ? n.isolate.slice(0, 28) + "…" : n.isolate, n.country, n.year]
      .filter((x) => x && String(x).trim() && String(x) !== "0");
    return bits.join(" ");
  };

  return (
    <div className="block">
      <div className="step2-src" title="Serotype, topotype and lineage are defined on VP1 (Samuel and Knowles 2001). Each sample's VP1 consensus is searched against the GenBank VP1 sets in the library; the nearest annotated record names the lineage, the nearest record of all is reported beside it.">
        <div className="step2-src-head">
          <span className="step2-src-num">1</span>
          VP1 typing
          <span className="muted" style={{ marginLeft: "auto", fontSize: "0.85em" }}>
            {samples.length ? `${typed} of ${samples.length} typed` : "no samples yet"}
          </span>
        </div>
        {samples.length ? (
          <div style={{ overflowX: "auto" }}>
            <table className="qc-table" style={{ fontSize: "0.85em", width: "100%" }}>
              <thead>
                <tr>
                  <th>Sample</th>
                  <th title="Serotype from the VP1 consensus (blastn). The reads vote at Step 1 is in the tooltip when it differs.">Serotype</th>
                  <th title="Topotype of the nearest annotated GenBank VP1.">Topotype</th>
                  <th title="Lineage (topotype / lineage / sublineage) of the nearest annotated GenBank VP1 within 3% of the best hit.">Lineage</th>
                  <th title="Identity of the VP1 consensus to the record that named the lineage.">Identity</th>
                  <th title="The nearest VP1 record of all, annotated or not, and its identity.">Nearest record</th>
                  <th title="Percent of VP1 positions with a called base in the consensus. Typing needs 50% or more.">VP1 called</th>
                  <th title="Share of the top annotated hits that carry the same lineage. Below 1 means the VP1 sits between lineages.">Agreement</th>
                </tr>
              </thead>
              <tbody>
                {samples.map((s) => {
                  const t = typing[s];
                  const c = chosen[s] || {};
                  const disagree = t?.serotype && c.serotype && t.serotype !== c.serotype;
                  return (
                    <tr key={s}>
                      <td>{s}</td>
                      <td title={disagree ? `Reads vote at Step 1: ${c.serotype} (${c.safe}). ${t?.note || ""}` : c.safe ? `Reads vote at Step 1: ${c.serotype} ${c.lineage || ""} (${c.safe})` : ""}>
                        {t?.status === "typed" ? t.serotype : (c.serotype ? `${c.serotype} (reads)` : "-")}{disagree ? " ⚠" : ""}
                      </td>
                      <td>{t?.status === "typed" ? (t.topotype || "unassigned") : "-"}</td>
                      <td title={t?.typed_by ? `Named by ${t.typed_by.accession} ${t.typed_by.country || ""} ${t.typed_by.year || ""}` : ""}>
                        {t?.status === "typed" ? (t.lineage || "unassigned") : (t?.status || "not typed")}
                      </td>
                      <td>{pct(t?.lineage_identity ?? t?.identity)}</td>
                      <td title={t?.nearest?.isolate || ""}>{t?.status === "typed" ? `${nearest(t)} (${pct(t.identity)})` : "-"}</td>
                      <td>{pct(t?.vp1_called_percent)}</td>
                      <td>{typeof t?.agreement?.lineage === "number" ? `${Math.round(t.agreement.lineage * 100)}% of ${t.agreement.n_annotated}` : "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted" style={{ fontSize: "0.85em" }}>Run Step 1 first. Each sample is typed as its consensus is written.</p>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6em", marginTop: "0.5em", flexWrap: "wrap" }}>
          <button onClick={retype} disabled={disabled || running || !samples.length || fmdvInfo?.vp1_typing_ready === false}
            title={fmdvInfo?.vp1_typing_ready === false
              ? "blastn is not on the backend's PATH. A bdtools install adds it with bin/bdtools install vsnp_gui; elsewhere BLAST+ is in conda_setup/fmdv_environment.yml."
              : "Type every sample's VP1 consensus again against the library's GenBank VP1 sets (blastn). A few seconds."}>
            {running ? "Typing…" : "Type VP1"}
          </button>
          {msg ? <span className="muted" style={{ fontSize: "0.85em" }}>{msg}</span> : null}
        </div>
      </div>
      <div className="step2-src" style={{ marginTop: "0.8em" }} title="One serotype per tree: the samples typed to it, the 79-genome panel VP1s and the GenBank context for the samples' topotypes, aligned with mafft and built with RAxML. The tree is listed in Step 2 Results and opens in the tree viewer.">
        <div className="step2-src-head">
          <span className="step2-src-num">2</span>
          VP1 tree
        </div>
        <FmdvVp1Tree apiBase={apiBase} project={project} fmdvInfo={fmdvInfo} disabled={disabled} onDone={onTreeDone} building={treeBuilding} />
      </div>
    </div>
  );
}
