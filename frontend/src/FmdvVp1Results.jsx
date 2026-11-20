import { useEffect, useMemo, useState } from "react";
import FmdvTreeProgress from "./FmdvTreeProgress.jsx";

// The VP1 half of Step 2 Results in FMDV mode. One VP1 tree run at a time
// (pills, newest first), and inside it the sections a typing report is made
// of: the calls, the tree, each sample's neighbourhood, the variable sites of
// VP1 at the nucleotide and amino acid level (sorted and cascade, from
// fmdv_vp1_tables.py), and the run's files.
export default function FmdvVp1Results({
  apiBase, project, runs, selectedRun, onSelectRun, groups, loading,
  fmdvInfo, previewXlsx, viewInline, downloadOutput, treeUrlFor, onRefresh,
}) {
  const [section, setSection] = useState("typing");
  const [info, setInfo] = useState(null);        // vp1_tree.json
  const [neigh, setNeigh] = useState(null);      // VP1_neighbours.json
  const [tables, setTables] = useState(null);    // vp1_tables.json
  const [sitesLevel, setSitesLevel] = useState("aa");
  const [sitesKind, setSitesKind] = useState("cascade");

  const run = runs.find((r) => r.run_id === selectedRun) || null;
  const vp1Dir = run?.path ? `${run.path}/VP1` : "";

  // A tree still being built is looked at again every 15 s until it is not.
  // The panel says "Sections fill in as its files appear", and that was true
  // only in the browser tab that had started the build and stayed open: after
  // a reload, or from another session, a finished tree read "running" until
  // somebody pressed Refresh. The O tree takes a quarter of an hour and the A
  // tree hours, so that is the usual way to meet one.
  const anyRunning = runs.some((r) => r.state === "running");
  useEffect(() => {
    if (!anyRunning || !onRefresh) return undefined;
    const t = setInterval(() => { onRefresh(); }, 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyRunning, project]);

  useEffect(() => {
    setInfo(null); setNeigh(null); setTables(null);
    if (!vp1Dir || !project) return undefined;
    let cancelled = false;
    const get = async (name) => {
      try {
        const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/download-file?path=${encodeURIComponent(`${vp1Dir}/${name}`)}&inline=1`);
        if (!res.ok) return null;
        return await res.json();
      } catch (_) { return null; }
    };
    (async () => {
      const [i, n, t] = await Promise.all([get("vp1_tree.json"), get("VP1_neighbours.json"), get("vp1_tables.json")]);
      if (cancelled) return;
      setInfo(i); setNeigh(n); setTables(t);
    })();
    return () => { cancelled = true; };
  }, [vp1Dir, project, apiBase, run?.status, run?.state]);

  const files = useMemo(() => {
    const g = (groups || []).find((x) => x.name === "VP1");
    return g ? g.files || [] : [];
  }, [groups]);
  const fileByName = (name) => files.find((f) => (f.label || "").endsWith(name));
  const treeFile = files.find((f) => /\.tre$/i.test(f.label || "") && !/neighbourhood/i.test(f.label || ""));
  const hoodFile = files.find((f) => /VP1_neighbourhood\.tre$/i.test(f.label || ""));
  const [treeSample, setTreeSample] = useState("");
  const sampleTrees = tables?.sample_trees || {};
  const sampleTreeFile = treeSample && sampleTrees[treeSample]
    ? files.find((f) => f.path === sampleTrees[treeSample].file) : null;
  const samples = info?.samples_included || Object.keys(neigh?.neighbourhoods || {});
  const typing = fmdvInfo?.vp1_typing || {};
  const chosen = fmdvInfo?.chosen || {};
  const pct = (v) => (typeof v === "number" ? `${v.toFixed(1)}%` : "-");

  // Every tree link: all the run's samples lit, midpoint root, bootstrap
  // values on, the whole tree in view.
  const treeOpts = (names) => `&search=${encodeURIComponent((names || []).join(","))}&midpoint=1&bootstrap=1&fit=1`;
  const sitesFile = fileByName(`VP1_${sitesKind}_table_${sitesLevel}.xlsx`);
  const sitesUrl = sitesFile
    ? `${apiBase}/api/projects/${encodeURIComponent(project)}/preview-xlsx?path=${encodeURIComponent(sitesFile.path)}`
    : "";

  const sections = [
    ["typing", "Typing"], ["tree", "Tree"], ["neighbours", "Neighbours"], ["sites", "Sites"], ["files", "Files"],
  ];

  function runLabel(r) {
    const m = /^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}_VP1-tree(?:-(.+))?$/.exec(r.run_id);
    const when = m ? `${m[1]} ${m[2]}:${m[3]}` : r.run_id;
    const sero = m && m[4] ? m[4] : "all serotypes";
    const state = r.state === "running" ? " · running" : r.state === "interrupted" ? " · interrupted" : "";
    return `${sero} · ${when}${state}`;
  }

  if (!runs.length) {
    return (
      <div className="note">
        No VP1 tree yet. Build one from the VP1 tab on the left: one serotype per tree, the samples typed to it with the
        library panel and the GenBank context for their topotypes. The <b>Typing</b>, <b>Tree</b>, <b>Neighbours</b>,{" "}
        <b>Sites</b> and <b>Files</b> views appear here once a tree has been started; they belong to a tree, so there is
        nothing to show before the first one.
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", gap: "0.4em", flexWrap: "wrap", alignItems: "center", marginBottom: "0.5em" }}>
        <span className="muted" style={{ fontSize: "0.85em" }}>VP1 tree:</span>
        {runs.map((r) => (
          <button key={r.run_id} className={`ghost${r.run_id === selectedRun ? " active" : ""}`}
            style={r.run_id === selectedRun ? { fontWeight: 600, borderColor: "var(--accent, #4a9)" } : undefined}
            onClick={() => onSelectRun(r.run_id)} title={r.path || r.run_id}>
            {runLabel(r)}
          </button>
        ))}
      </div>
      {run?.path ? <div className="note s2-folder s2-folder-selected">Folder: <span className="s2-folder-path">{run.path}</span></div> : null}
      {run?.state === "running" ? (
        <>
          <FmdvTreeProgress apiBase={apiBase} project={project} runId={run.run_id} />
          <div className="muted" style={{ fontSize: "0.85em", marginTop: "0.25em" }}>
            The sections below fill in by themselves when it finishes. You can leave this page, or close it: the build
            carries on.
          </div>
        </>
      ) : null}
      <div className="mode-toggle" style={{ marginTop: "0.6em", marginBottom: "0.6em" }}>
        {sections.map(([k, label]) => (
          <button key={k} className={section === k ? "active" : ""} onClick={() => setSection(k)}>{label}</button>
        ))}
      </div>

      {loading ? <div className="note"><span className="pulse-dot" /> Loading…</div> : null}

      {section === "typing" ? (
        <div>
          <p className="muted" style={{ fontSize: "0.85em", marginTop: 0 }}>
            The call per sample: serotype from the VP1 consensus, lineage from the nearest annotated GenBank VP1, and the
            nearest record of all. {info ? `${samples.length} sample${samples.length === 1 ? "" : "s"} in this tree, ${info.panel_sequences} panel and context sequences.` : ""}
          </p>
          {run?.path ? (() => {
            const base = run.path.replace(/\/step2\/.*$/, "");
            return (
              <div style={{ display: "flex", gap: "0.5em", alignItems: "center", marginBottom: "0.5em", flexWrap: "wrap" }}>
                <span className="muted" style={{ fontSize: "0.85em" }} title="One page for the outbreak team: every sample's call, the notes that change how a call is read, and the serotype trees. Rewritten after each Step 1 and each VP1 tree.">Run report (outbreak team):</span>
                <button className="ghost" onClick={() => viewInline(`${base}/FMDV_run_report.html`)}>HTML</button>
                <button className="ghost" onClick={() => downloadOutput(`${base}/FMDV_run_report.pdf`, `${project}_FMDV_run_report.pdf`)}>PDF</button>
              </div>
            );
          })() : null}
          <table className="qc-table" style={{ fontSize: "0.85em", width: "100%" }}>
            <thead><tr><th>Sample</th><th title="The sample's one-page report: call, QC, neighbourhood, tree, VP1 differences and sequence. Written at Step 1 and rewritten after each VP1 tree.">Report</th><th>Serotype</th><th>Topotype</th><th>Lineage</th><th>Identity</th><th>Nearest record</th><th>Reads vote</th><th>VP1 called</th></tr></thead>
            <tbody>
              {samples.map((s) => {
                const t = typing[s]; const c = chosen[s] || {}; const n = t?.nearest || {};
                return (
                  <tr key={s}>
                    <td>{s}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {(() => {
                        const base = run?.path ? run.path.replace(/\/step2\/.*$/, "") : "";
                        const dir = base ? `${base}/step1/${s}` : "";
                        if (!dir) return "-";
                        // An imported sequence has no report yet: the report
                        // scripts take folders with a chooser result.
                        if (fmdvInfo?.imports?.[s] && fmdvInfo.imports[s].reads !== "simulated") return <span className="muted" title="Imported sequence, no reads: no report is written for it yet.">imported</span>;
                        return (<>
                          <button className="ghost" onClick={() => viewInline(`${dir}/${s}_FMDV_report.html`)} title="Open the HTML report in a new tab">HTML</button>{" "}
                          <button className="ghost" onClick={() => downloadOutput(`${dir}/${s}_FMDV_report.pdf`, `${s}_FMDV_report.pdf`)} title="Download the PDF">PDF</button>
                        </>);
                      })()}
                    </td>
                    <td>{t?.status === "typed" ? t.serotype : "-"}{t?.note ? " ⚠" : ""}</td>
                    <td>{t?.status === "typed" ? (t.topotype || "unassigned") : "-"}</td>
                    <td>{t?.status === "typed" ? (t.lineage || "unassigned") : (t?.status || "not typed")}</td>
                    <td>{pct(t?.lineage_identity ?? t?.identity)}</td>
                    <td title={n.isolate || ""}>{[n.accession, n.country, n.year && String(n.year) !== "0" ? n.year : ""].filter(Boolean).join(" ")}{typeof t?.identity === "number" ? ` (${pct(t.identity)})` : ""}</td>
                    <td title={c.safe || ""}>{c.serotype ? `${c.serotype} ${c.lineage || ""}`.trim() : "-"}{c.mixed ? " mixed" : ""}{c.distant ? " distant" : ""}</td>
                    <td>{pct(t?.vp1_called_percent)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {section === "tree" ? (
        <div>
          <p className="muted" style={{ fontSize: "0.85em", marginTop: 0 }}>
            RAxML on the VP1 alignment, {info?.bootstraps ?? 100} bootstraps. The neighbourhood tree is the full tree pruned to the
            samples, their neighbourhoods and one record per other lineage, so every label reads; branch lengths are the full tree's.
            {tables?.neighbourhood_tree ? ` ${tables.neighbourhood_tree.leaves} of ${tables.neighbourhood_tree.full_tree_leaves} leaves: ${tables.neighbourhood_tree.samples} samples, ${tables.neighbourhood_tree.neighbourhood} neighbourhood records, ${tables.neighbourhood_tree.lineage_representatives} lineage representatives.` : ""}
            {" "}Context labels read lineage | country | year | accession. The viewer opens midpoint-rooted, fitted to the whole tree, with bootstrap values shown and every sample highlighted.
          </p>
          <div style={{ display: "flex", gap: "0.5em", flexWrap: "wrap" }}>
            {hoodFile ? (
              <button onClick={() => window.open(treeUrlFor(hoodFile.path) + treeOpts(samples), "_blank", "noopener")}
                title="The samples among their relatives: the default view.">Neighbourhood tree</button>
            ) : <span className="muted">No neighbourhood tree for this run{run?.state === "running" ? " yet" : ". Build the tree again; it is written with the tables."}</span>}
            {treeFile ? (
              <button className="ghost" onClick={() => window.open(treeUrlFor(treeFile.path) + treeOpts(samples), "_blank", "noopener")}
                title="Every panel and context sequence the tree was built with.">Full tree</button>
            ) : null}
            {Object.keys(sampleTrees).length ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "0.4em" }}>
                <select value={treeSample} onChange={(e) => setTreeSample(e.target.value)} title="One sample with its relatives and one record per other topotype: the figure its report prints.">
                  <option value="">one sample…</option>
                  {Object.entries(sampleTrees).map(([s_, t]) => <option key={s_} value={s_}>{s_} ({t.leaves} leaves)</option>)}
                </select>
                <button disabled={!sampleTreeFile} onClick={() => sampleTreeFile && window.open(treeUrlFor(sampleTreeFile.path) + treeOpts([treeSample]), "_blank", "noopener")}>
                  Sample tree
                </button>
              </span>
            ) : null}
          </div>
          {info?.context_selection ? (
            <div className="muted" style={{ fontSize: "0.8em", marginTop: "0.5em" }}>
              Context: {Object.entries(info.context_selection).map(([sero, c]) =>
                `${sero}: ${c.selected} of ${c.context_total} records${c.focus && c.focus.length ? ` (focus ${c.focus.join(", ")})` : ""}`).join("; ")}
            </div>
          ) : null}
        </div>
      ) : null}

      {section === "neighbours" ? (
        <div>
          <p className="muted" style={{ fontSize: "0.85em", marginTop: 0 }}>
            Each sample's relatives in the alignment: every record within {neigh?.neighbourhoods ? (Object.values(neigh.neighbourhoods)[0]?.within_pct ?? 3) : 3}% identity of its nearest, at most 20 and at least 5, by identity over the compared bases.
          </p>
          {!neigh ? <span className="muted">No neighbourhood table for this run{run?.state === "running" ? " yet" : ""}.</span> : null}
          {neigh ? samples.map((s) => {
            const v = neigh.neighbourhoods?.[s];
            if (!v) return null;
            return (
              <details key={s} open={samples.length <= 3} style={{ marginBottom: "0.5em" }}>
                <summary><strong>{s}</strong>{v.nearest ? ` · nearest ${v.nearest.name} at ${v.nearest.identity}%` : ""}{typeof v.n_within === "number" ? ` · ${v.n_within} within ${v.within_pct}%` : ""}</summary>
                <table className="qc-table" style={{ fontSize: "0.82em", width: "100%" }}>
                  <thead><tr><th>#</th><th>Identity</th><th>Record</th><th>Lineage</th><th>Country</th><th>Year</th><th>Host</th><th>Isolate</th></tr></thead>
                  <tbody>
                    {(v.records || []).map((r, i) => (
                      <tr key={r.name}>
                        <td>{i + 1}</td><td>{r.identity}%</td><td>{r.name}</td><td>{r.lineage || "-"}</td>
                        <td>{r.country || "-"}</td><td>{r.year && String(r.year) !== "0" ? r.year : "-"}</td><td>{r.host || "-"}</td><td>{r.isolate || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            );
          }) : null}
        </div>
      ) : null}

      {section === "sites" ? (
        <div>
          <div style={{ display: "flex", gap: "1em", alignItems: "center", flexWrap: "wrap" }}>
            <div className="mode-toggle">
              <button className={sitesLevel === "aa" ? "active" : ""} onClick={() => setSitesLevel("aa")}>Amino acids</button>
              <button className={sitesLevel === "nt" ? "active" : ""} onClick={() => setSitesLevel("nt")}>Nucleotides</button>
            </div>
            <div className="mode-toggle">
              <button className={sitesKind === "cascade" ? "active" : ""} onClick={() => setSitesKind("cascade")} title="Sites ordered by how many rows carry the minority state: shared differences first, private ones last.">Cascade</button>
              <button className={sitesKind === "sorted" ? "active" : ""} onClick={() => setSitesKind("sorted")} title="Sites in VP1 order.">Sorted</button>
            </div>
            {sitesFile ? <>
              <button onClick={() => previewXlsx(sitesFile.path)} title="Open in a new tab">Open</button>
              <button onClick={() => downloadOutput(sitesFile.path, sitesFile.download_name)}>DL</button>
            </> : null}
            {tables?.counts ? <span className="muted" style={{ fontSize: "0.8em" }}>{tables.counts[sitesLevel]?.rows} rows (samples and neighbourhoods, tree order), {tables.counts[sitesLevel]?.variable_sites} variable sites</span> : null}
          </div>
          {sitesUrl ? (
            <iframe title={`VP1 ${sitesKind} ${sitesLevel}`} src={sitesUrl} style={{ width: "100%", height: "60vh", border: "1px solid var(--rule, #ccc)", borderRadius: "6px", marginTop: "0.5em", background: "#fff" }} />
          ) : <p className="muted" style={{ fontSize: "0.85em" }}>No site table for this run{tables?.status === "failed" ? `: ${tables.error}` : run?.state === "running" ? " yet" : ". Build the tree again; tables are written with it."}</p>}
        </div>
      ) : null}

      {section === "files" ? (
        <div>
          {files.map((item) => (
            <div key={item.path} className="results-item">
              <div className="results-main"><div className="results-name">{item.label}</div><div className="results-path">{item.path}</div></div>
              <div className="results-actions">
                {/\.tre$/i.test(item.label || "") ? <button onClick={() => window.open(treeUrlFor(item.path) + treeOpts(samples), "_blank", "noopener")}>View tree</button>
                  : /\.xlsx$/i.test(item.label || "") ? <button onClick={() => previewXlsx(item.path)}>View</button>
                  : /\.(fasta|fa|tsv|json|log|txt|tre)$/i.test(item.label || "") ? <button onClick={() => viewInline(item.path)}>View</button> : null}
                <button onClick={() => downloadOutput(item.path, item.download_name)}>DL</button>
              </div>
            </div>
          ))}
          {!files.length ? <span className="muted">No files listed yet.</span> : null}
        </div>
      ) : null}
    </div>
  );
}
