import { useEffect, useMemo, useRef, useState } from "react";

// The VP1 tree button for an FMDV project. POST fmdv/vp1_tree starts
// bin/fmdv_vp1_tree.py as a background job (mafft alignment of the samples'
// VP1 against the serotype's panel and context set, then raxmlHPC or iqtree);
// the tree lands under step2/<stamp>_VP1-tree[-SEROTYPE]/ and the Step 2
// Results list shows it like any comparison. One serotype per tree: the
// VP1 of two serotypes differ too much for one alignment to mean anything.
export default function FmdvVp1Tree({ apiBase, project, fmdvInfo, disabled, onDone }) {
  const [serotype, setSerotype] = useState("");
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState("");
  const pollRef = useRef(null);

  // Serotypes present among the samples Step 1 placed, with counts.
  const serotypes = useMemo(() => {
    const n = {};
    for (const c of Object.values(fmdvInfo?.chosen || {})) {
      if (c?.serotype) n[c.serotype] = (n[c.serotype] || 0) + 1;
    }
    return Object.entries(n).sort();
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
      setStatus("running");
      setMsg(`Aligning VP1 of ${body.samples} sample${body.samples === 1 ? "" : "s"} and building the tree with ${body.tree_tool}. A few minutes with bootstraps.`);
    } catch (e) { setMsg(`VP1 tree: ${e.message || e}`); }
  }

  const tools = fmdvInfo?.vp1_tree || {};
  const running = status === "running" || status === "queued";
  const noSamples = serotypes.length === 0;
  const why = !tools.ready
    ? "mafft or a tree builder is missing: create the fmdv_gui conda env from conda_setup/environment.yml."
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
        {running ? "Building…" : "Build VP1 tree"}
      </button>
      {msg ? <span className="muted" style={{ fontSize: "0.85em", flexBasis: "100%" }}>{msg}</span> : null}
    </div>
  );
}
