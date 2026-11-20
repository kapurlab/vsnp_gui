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
export default function FmdvVp1Panel({ apiBase, project, fmdvInfo, disabled, onInfo, onTreeDone, onLogJob, step1Done = 0, treeBuilding = false }) {
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
      <div className="step2-src">
        <div className="step2-src-head">
          <span className="step2-src-num">1</span>
          What each sample is
          <span className="muted" style={{ marginLeft: "auto", fontSize: "0.85em" }}>
            {samples.length ? `${typed} of ${samples.length} identified` : "no samples yet"}
          </span>
        </div>
        <p className="vp1-hint" style={{ marginTop: 0, marginBottom: "0.4em" }}>
          Each sample&apos;s VP1 gene is compared against thousands of published FMDV VP1 sequences; the closest match
          names its <b>serotype</b>, <b>topotype</b> and <b>lineage</b>. Step 1 does this for each sample as it
          finishes — these are its results, not something you need to start.
        </p>
        {samples.length ? (
          <div className="vp1-scroll vp1-short">
            <table>
              <thead>
                <tr>
                  <th>Sample</th>
                  <th title="The serotype of the closest published VP1 sequence. ⚠ means the whole-genome reads pointed somewhere else; the tooltip on the cell says where.">Serotype</th>
                  <th title="The broad geographic group within the serotype, from the closest published sequence that carries one.">Topotype</th>
                  <th title="The narrowest named group the closest published sequences agree on.">Lineage</th>
                  <th title="How close that published sequence is to this sample, over the part they share.">Identity<small>to nearest</small></th>
                  <th title="The single closest published sequence of all, with where and when it was collected.">Nearest published sequence</th>
                  <th title="How much of the VP1 gene the reads covered. Below 50% no call is made at all.">VP1 called<small>coverage</small></th>
                  <th title="How many of the closest published sequences agree on this lineage. Less than 100% means the sample sits between named lineages.">Agreement<small>on the lineage</small></th>
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
          <p className="vp1-hint">Run Step 1 first: each sample is identified as its sequence is written.</p>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6em", marginTop: "0.5em", flexWrap: "wrap" }}>
          <button className="ghost" onClick={retype} disabled={disabled || running || !samples.length || fmdvInfo?.vp1_typing_ready === false}
            title={fmdvInfo?.vp1_typing_ready === false
              ? "BLAST+ (blastn) is missing from the environment vSNP3 runs in, so nothing can be compared. On your own computer: bin/bdtools install vsnp_gui; on a shared install: bin/bdtools update-packages vsnp_gui."
              : "Compare every sample against the published sequences again. Worth doing after the reference library is updated; it takes a few seconds and changes nothing else."}>
            {running ? "Working…" : "Identify again"}
          </button>
          {msg ? <span className="muted" style={{ fontSize: "0.85em" }}>{msg}</span> : null}
        </div>
      </div>
      <div className="step2-src" style={{ marginTop: "0.8em" }}>
        <div className="step2-src-head">
          <span className="step2-src-num">2</span>
          Where they sit among known strains
        </div>
        <p className="vp1-hint" style={{ marginTop: 0, marginBottom: "0.4em" }}>
          Builds a family tree of one serotype at a time: the samples identified as that serotype, with the published
          strains closest to them and a backbone of the serotype&apos;s known lineages. It opens in{" "}
          <b>Step 2 Results</b> on the right, with the tables that go with it.
        </p>
        <FmdvVp1Tree apiBase={apiBase} project={project} fmdvInfo={fmdvInfo} disabled={disabled} onDone={onTreeDone} onLogJob={onLogJob} building={treeBuilding} />
      </div>
    </div>
  );
}
