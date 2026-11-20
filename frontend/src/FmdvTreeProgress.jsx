import { useEffect, useState } from "react";
import Elapsed from "./Elapsed.jsx";

// Where a VP1 tree build has got to: how long it has run, how many sequences
// are in the tree, which bootstrap it is on, or where the search is.
//
// "Building…" and "A few minutes with bootstraps" was all the page said, for a
// build that takes minutes for one lineage and hours for a serotype spread
// over many. Ten minutes into the full test set's O tree, with nothing on the
// page moving, the only question left was whether it had hung (Ames HPC,
// 2026-11-20). The backend reads RAxML's own log (app.fmdv_tree_progress), so
// this answers the same after a reload and from another session.
//
// `runId` names the tree; without it the project's newest tree is reported.
export default function FmdvTreeProgress({ apiBase, project, runId = "", onStage }) {
  const [p, setP] = useState(null);

  useEffect(() => {
    setP(null);
    if (!project) return undefined;
    let stop = false;
    const tick = async () => {
      try {
        const q = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
        const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/fmdv/vp1_tree/progress${q}`);
        if (!res.ok || stop) return;
        const body = await res.json();
        if (stop) return;
        setP(body);
        if (onStage) onStage(body.stage);
      } catch (_) { /* transient: the next tick asks again */ }
    };
    tick();
    const t = setInterval(tick, 10000);
    return () => { stop = true; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, project, runId]);

  if (p && p.stage === "done") return null;
  const mins = (s) => (s < 90 ? `${Math.max(1, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
  return (
    <div className="note" style={{ flexBasis: "100%" }}>
      <span className="pulse-dot" />{" "}
      {p ? (
        <>
          <strong>Building for <Elapsed since={p.started_at || undefined} title="Since this tree build started" /></strong>.{" "}
          {p.text}
          {p.stage === "bootstraps" && typeof p.bootstrap_seconds_left === "number" && p.bootstraps_done > 0
            ? ` About ${mins(p.bootstrap_seconds_left)} of bootstraps left.` : ""}
        </>
      ) : (
        <>This tree is being built. <Elapsed /></>
      )}
    </div>
  );
}
