import { useEffect, useState } from "react";

// What vSNP3 reads for the lineage a Step 2 comparison runs on: the
// defining-SNP groups, the positions filtered out of the tables, and the
// samples on the remove list, with the way in to change them.
//
// In an ordinary project the reference is one name chosen up front, and the
// Reference Editor opens on it. In FMDV mode the reference is picked per
// sample and the comparison reference is picked here, in Step 2, so nothing
// said that a lineage has these files at all, let alone where to edit them
// (Ames HPC, 2026-11-20: "the reference dependencies aren't being shown like
// they are in a general vSNP workflow"). This is that statement, beside the
// choice it belongs to.
//
// `refreshKey` changes whenever the Reference Editor reloads its files (it
// does after every edit), so the counts here follow an edit made below.
export default function FmdvRefFiles({ apiBase, reference, known, refreshKey, onEdit }) {
  const [info, setInfo] = useState(null);

  useEffect(() => {
    setInfo(null);
    if (!reference || !known) return undefined;
    let stop = false;
    (async () => {
      const base = `${apiBase}/api/references/${encodeURIComponent(reference)}`;
      try {
        const [g, r, f] = await Promise.all([
          fetch(`${base}/define-filter/groups`).then((x) => (x.ok ? x.json() : null)),
          fetch(`${base}/remove-from-analysis/entries`).then((x) => (x.ok ? x.json() : null)),
          fetch(`${base}/files`).then((x) => (x.ok ? x.json() : null)),
        ]);
        if (!stop) setInfo({ groups: g, removed: r, files: f });
      } catch {
        if (!stop) setInfo({ failed: true });
      }
    })();
    return () => { stop = true; };
  }, [apiBase, reference, known, refreshKey]);

  if (!reference) return null;
  if (!known) {
    return (
      <div className="step2-src-hint" data-testid="fmdv-ref-files">
        <strong>{reference}</strong> is not in the FMDV reference library on this machine, so its
        defining SNPs, filter positions and remove list cannot be shown or edited here.
      </div>
    );
  }
  if (!info) return <div className="step2-src-hint" data-testid="fmdv-ref-files">Reading what vSNP3 uses for {reference}…</div>;
  if (info.failed) {
    return (
      <div className="step2-src-hint" data-testid="fmdv-ref-files">
        The files vSNP3 reads for {reference} could not be listed.{" "}
        <button className="ghost" onClick={onEdit}>Open it in the Reference Editor</button>
      </div>
    );
  }
  const g = info.groups;
  const groups = (g && g.available && g.groups) || [];
  const allCount = g && g.available && g.all ? g.all.count : 0;
  const groupFiltered = groups.reduce((n, x) => n + (x.count || 0), 0);
  const removed = (info.removed && info.removed.entries) || [];
  const readOnly = info.files && info.files.ref_dir_writable === false;
  const names = groups.map((x) => x.name);
  const shown = names.slice(0, 8).join(", ") + (names.length > 8 ? `, and ${names.length - 8} more` : "");
  return (
    <div className="step2-src-hint" data-testid="fmdv-ref-files">
      <div>
        <strong>What vSNP3 reads for {reference}</strong>
        {info.files && info.files.ref_path ? (
          <span className="muted" style={{wordBreak:"break-all"}}> ({info.files.ref_path})</span>
        ) : null}
      </div>
      <ul style={{margin:"0.25em 0 0.35em 1.2em", padding:0}}>
        <li>
          {g && !g.available
            ? <>Defining SNPs: {g.reason || "no defining-SNP file"}.</>
            : groups.length
            ? <>Defining SNPs: {groups.length} group{groups.length === 1 ? "" : "s"} ({shown}). A sample that
                carries a group's SNPs gets that group's own table and tree.</>
            : <>Defining SNPs: no groups yet, so a run makes one table and tree for all samples.</>}
        </li>
        <li>
          Filtered positions: {allCount} left out of every table
          {groups.length ? `, ${groupFiltered} left out of single groups` : ""}.
        </li>
        <li>
          Remove list: {removed.length
            ? `${removed.length} sample${removed.length === 1 ? "" : "s"} kept out of every run on this lineage`
            : "empty, so no sample is kept out"}.
        </li>
      </ul>
      <button className="ghost action" style={{fontSize:"0.9em"}} onClick={onEdit} data-testid="fmdv-ref-files-edit">
        {readOnly ? "View these in the Reference Editor" : "Edit these in the Reference Editor"}
      </button>
      {readOnly ? (
        <span className="muted"> This account cannot write to that folder, so edits will be refused.</span>
      ) : null}
    </div>
  );
}
