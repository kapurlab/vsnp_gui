import { useEffect, useMemo, useRef, useState } from "react";
import Elapsed from "./Elapsed.jsx";
import FmdvTreeProgress from "./FmdvTreeProgress.jsx";

// The VP1 tree button for an FMDV project. POST fmdv/vp1_tree starts
// bin/fmdv_vp1_tree.py as a background job (mafft alignment of the samples'
// VP1 against the serotype's panel and context set, then raxmlHPC or iqtree);
// the tree lands under step2/<stamp>_VP1-tree[-SEROTYPE]/ and the Step 2
// Results list shows it like any comparison. One serotype per tree: the
// VP1 of two serotypes differ too much for one alignment to mean anything.
//
// `building` is true while Step 2 Results lists a VP1 tree of this project as
// running. This button knew only about a build IT had started, in this tab:
// after a reload it read "Build VP1 tree" again beside a build still going,
// and a second click started a second RAxML on the same cores.
export default function FmdvVp1Tree({ apiBase, project, fmdvInfo, disabled, onDone, building = false }) {
  const [serotype, setSerotype] = useState("");
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState("");
  const pollRef = useRef(null);

  // Serotypes present among the samples Step 1 placed, plus the sequences
  // imported without reads (their serotype is the one entered at import),
  // with counts.
  const serotypes = useMemo(() => {
    const n = {};
    for (const c of Object.values(fmdvInfo?.chosen || {})) {
      if (c?.serotype) n[c.serotype] = (n[c.serotype] || 0) + 1;
    }
    for (const [name, imp] of Object.entries(fmdvInfo?.imports || {})) {
      if (imp?.serotype && !fmdvInfo?.chosen?.[name]) n[imp.serotype] = (n[imp.serotype] || 0) + 1;
    }
    // Most samples first, so the default tree is the one most of the run is in.
    return Object.entries(n).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [fmdvInfo]);

  useEffect(() => {
    setJobId(""); setStatus(""); setMsg("");
  }, [project]);

  useEffect(() => {
    // Default to the only serotype, or the first when the choice is stale.
    if (serotypes.length && !serotypes.some(([s]) => s === serotype)) setSerotype(serotypes[0][0]);
  }, [serotypes, serotype]);

  useEffect(() => {
    if (!jobId) return undefined;
    const tick = async () => {
      try {
        const res = await fetch(`${apiBase}/api/jobs/${jobId}`);
        if (!res.ok) return;
        const job = await res.json();
        setStatus(job.status || "");
        if (job.status && job.status !== "running" && job.status !== "queued") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setJobId("");
          if (job.status === "succeeded") {
            setMsg(`VP1 tree built${serotype ? ` for ${serotype}` : ""}. It is listed in Step 2 Results (the VP1-tree comparison).`);
          } else {
            setMsg(`VP1 tree ${job.status}. The job log says which step failed.`);
          }
          if (onDone) onDone(job);
        }
      } catch (_) { /* transient */ }
    };
    pollRef.current = setInterval(tick, 3000);
    tick();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobId]);

  async function start() {
    setMsg("");
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/fmdv/vp1_tree`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ serotype }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setMsg(body.detail || `HTTP ${res.status}`); return; }
      setJobId(body.job_id);
      // Step 2 Results lists the run as soon as its folder exists, marked
      // running. Without this it went on saying "No VP1 tree yet" for the
      // whole build, beside a button that said Building.
      if (onDone) setTimeout(() => onDone({ status: "running" }), 3000);
      setStatus("running");
      // No "a few minutes": that is true of one lineage and wrong by hours for
      // a serotype spread over many. The line under the button says where the
      // build is.
      setMsg(`Aligning VP1 of ${body.samples} sample${body.samples === 1 ? "" : "s"} with their GenBank neighbours, then building the tree with ${body.tree_tool}. Minutes when the samples sit in one lineage; an hour or more when they are spread across a serotype, because the tree then carries hundreds of GenBank records.`);
    } catch (e) { setMsg(`VP1 tree: ${e.message || e}`); }
  }

  const tools = fmdvInfo?.vp1_tree || {};
  const mine = status === "running" || status === "queued";
  const running = mine || building;
  const noSamples = serotypes.length === 0;
  const why = !tools.ready
    ? "mafft or a tree builder is missing. A bdtools install adds mafft with bin/bdtools install vsnp_gui; elsewhere create the fmdv_gui conda env from conda_setup/fmdv_environment.yml."
    : noSamples ? "Run Step 1 first; the tree uses each sample's VP1 consensus."
    : running ? "A VP1 tree is being built." : "";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5em", flexWrap: "wrap", marginTop: "0.5em" }}>
      <span className="muted" style={{ fontSize: "0.85em" }}>VP1 tree</span>
      <select value={serotype} disabled={disabled || running || noSamples} onChange={(e) => setSerotype(e.target.value)}
        title="One serotype per tree: the samples placed on this serotype, with the library panel and the GenBank context set for it.">
        {serotypes.map(([s, n]) => <option key={s} value={s}>{s} ({n} sample{n === 1 ? "" : "s"})</option>)}
        {noSamples ? <option value="">no samples yet</option> : null}
      </select>
      <button onClick={start} disabled={disabled || running || noSamples || !tools.ready || !serotype}
        title={why || `Align the ${serotype} samples' VP1 with the library panel and context set, build a tree with ${tools.tree_tool}, and list it in Step 2 Results.`}>
        {running ? (<>Building… {mine ? <Elapsed /> : null}</>) : "Build VP1 tree"}
      </button>
      {running ? <FmdvTreeProgress apiBase={apiBase} project={project} /> : null}
      {msg ? <span className="muted" style={{ fontSize: "0.85em", flexBasis: "100%" }}>{msg}</span> : null}
    </div>
  );
}
