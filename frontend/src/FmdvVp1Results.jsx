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

  // Each view gets a name AND a line saying what it answers. The bare names —
  // Typing, Neighbours, Sites — told a reader who did not already know the
  // method nothing about which one to open (Ames HPC, 2026-11-20).
  const sections = [
    ["typing", "Typing", "what each sample is"],
    ["tree", "Tree", "where each sample sits"],
    ["neighbours", "Neighbours", "its closest published relatives"],
    ["sites", "Sites", "where the sequences differ"],
    ["files", "Files", "everything this run wrote"],
  ];

  function runLabel(r) {
    const m = /^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}_VP1-tree(?:-(.+))?$/.exec(r.run_id);
    const when = m ? `${m[1]} ${m[2]}:${m[3]}` : r.run_id;
    const sero = m && m[4] ? `Serotype ${m[4]}` : "All serotypes";
    const state = r.state === "running" ? " · building" : r.state === "interrupted" ? " · interrupted" : "";
    return `${sero} · ${when}${state}`;
  }

  if (!runs.length) {
    return (
      <div className="vp1-explain">
        <h4>No VP1 tree yet</h4>
        <p>
          Build one on the left: pick a serotype, then <b>Build VP1 tree</b>. The tree places this project&apos;s
          samples of that serotype among the published FMDV sequences most like them.
        </p>
        <p className="vp1-use">
          Five views then appear here, all belonging to the tree you pick: <b>Typing</b> (what each sample is),{" "}
          <b>Tree</b> (where it sits), <b>Neighbours</b> (its closest published relatives), <b>Sites</b> (where the
          sequences differ) and <b>Files</b>. Nothing can be shown before the first tree is built.
        </p>
      </div>
    );
  }

  const nSamples = samples.length;
  const projectDir = run?.path ? run.path.replace(/\/step2\/.*$/, "") : "";

  return (
    <div>
      <div className="vp1-row vp1-pick">
        <span className="vp1-label" title="Each button is one tree build. Everything below belongs to whichever is picked.">
          Tree to show:
        </span>
        {runs.map((r) => (
          <button key={r.run_id} className={`ghost${r.run_id === selectedRun ? " active" : ""}`}
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
      <div className="vp1-tabs" role="tablist" aria-label="Views of this VP1 tree">
        {sections.map(([k, label, caption]) => (
          <button key={k} role="tab" aria-selected={section === k}
            className={`vp1-tab${section === k ? " active" : ""}`} onClick={() => setSection(k)}>
            <b>{label}</b><span>{caption}</span>
          </button>
        ))}
      </div>

      {loading ? <div className="note"><span className="pulse-dot" /> Loading…</div> : null}

      {section === "typing" ? (
        <div>
          <div className="vp1-explain">
            <h4>What each sample is</h4>
            <p>
              The VP1 gene read out of each sample is compared against thousands of published FMDV VP1 sequences, and
              the closest match names the sample&apos;s <b>serotype</b>, <b>topotype</b> and <b>lineage</b>. No sample
              is used as a reference for another: every call is made against the published collection.
            </p>
            <p className="vp1-use">
              <b>Use it to</b> say what each sample is, in the terms reference laboratories use, and to see how
              confident that call is — a match at 100% is the same lineage; a low one means the nearest published
              sequence is still distant, so read the lineage with care.
            </p>
            <details>
              <summary>What the columns mean</summary>
              <dl>
                <dt>Serotype, Topotype, Lineage</dt>
                <dd>The call, from the closest published VP1. Narrowest on the right.</dd>
                <dt>Identity</dt>
                <dd>How close that published sequence is to the sample, over the part they share.</dd>
                <dt>Nearest record</dt>
                <dd>The single published sequence closest to this sample, with where and when it came from.</dd>
                <dt>Reads vote</dt>
                <dd>What the whole genome said, as a cross-check. It names a whole genome the reads mapped best to, so
                  it is coarser than the VP1 call; the two disagreeing is worth a look, not an error.</dd>
                <dt>VP1 called</dt>
                <dd>How much of VP1 the reads actually covered. A call from well under 100% rests on less sequence.</dd>
                <dt>Report</dt>
                <dd>This sample&apos;s own one-page report, to print or send.</dd>
              </dl>
            </details>
          </div>
          <p className="vp1-hint" style={{ marginTop: 0 }}>
            {info ? `${nSamples} sample${nSamples === 1 ? "" : "s"} in this tree, compared against ${info.panel_sequences} published sequences.` : ""}
          </p>
          {projectDir ? (
            <div className="vp1-row">
              <span className="vp1-label" title="One page covering the whole run: every sample's call, the notes that change how a call is read, and the serotype trees. Rewritten after each Step 1 and each VP1 tree.">
                Report for the whole run:
              </span>
              <button className="ghost" onClick={() => viewInline(`${projectDir}/FMDV_run_report.html`)}>Open</button>
              <button className="ghost" onClick={() => downloadOutput(`${projectDir}/FMDV_run_report.pdf`, `${project}_FMDV_run_report.pdf`)}>Download PDF</button>
            </div>
          ) : null}
          {/* The table scrolls inside this box. It used to carry .qc-table on
              the <table> itself, where overflow does nothing: more columns
              than the pane is wide ran off its edge with no way to reach the
              last of them (Ames HPC, 2026-11-20). */}
          <div className="vp1-scroll">
          <table>
            <thead><tr><th>Sample</th><th title="This sample's one-page report: its call, read quality, closest relatives, tree and VP1 differences.">Report</th><th>Serotype</th><th>Topotype</th><th>Lineage</th><th>Identity<small>to nearest</small></th><th>Nearest record</th><th>Reads vote<small>whole genome</small></th><th>VP1 called<small>coverage</small></th></tr></thead>
            <tbody>
              {samples.map((s) => {
                const t = typing[s]; const c = chosen[s] || {}; const n = t?.nearest || {};
                return (
                  <tr key={s}>
                    <td>{s}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {(() => {
                        const dir = projectDir ? `${projectDir}/step1/${s}` : "";
                        if (!dir) return "-";
                        // An imported sequence has no report yet: the report
                        // scripts take folders with a chooser result.
                        if (fmdvInfo?.imports?.[s] && fmdvInfo.imports[s].reads !== "simulated") return <span className="muted" title="Imported sequence, no reads: no report is written for it yet.">imported</span>;
                        return (<>
                          <button className="ghost" onClick={() => viewInline(`${dir}/${s}_FMDV_report.html`)} title="Open this sample's report in a new tab">Open</button>{" "}
                          <button className="ghost" onClick={() => downloadOutput(`${dir}/${s}_FMDV_report.pdf`, `${s}_FMDV_report.pdf`)} title="Download this sample's report as a PDF">PDF</button>
                        </>);
                      })()}
                    </td>
                    <td>{t?.status === "typed" ? t.serotype : "-"}{t?.note ? " ⚠" : ""}</td>
                    <td>{t?.status === "typed" ? (t.topotype || "unassigned") : "-"}</td>
                    <td>{t?.status === "typed" ? (t.lineage || "unassigned") : (t?.status || "not typed")}</td>
                    <td className="vp1-num">{pct(t?.lineage_identity ?? t?.identity)}</td>
                    <td title={n.isolate || ""}>{[n.accession, n.country, n.year && String(n.year) !== "0" ? n.year : ""].filter(Boolean).join(" ")}{typeof t?.identity === "number" ? ` (${pct(t.identity)})` : ""}</td>
                    <td title={c.safe || ""}>{c.serotype ? `${c.serotype} ${c.lineage || ""}`.trim() : "-"}{c.mixed ? " mixed" : ""}{c.distant ? " distant" : ""}</td>
                    <td className="vp1-num">{pct(t?.vp1_called_percent)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </div>
      ) : null}

      {section === "tree" ? (
        <div>
          <div className="vp1-explain">
            <h4>Where each sample sits among known strains</h4>
            <p>
              A family tree of the VP1 gene: this project&apos;s samples and the published sequences they were compared
              against, arranged so that the most similar sit closest together. It is built from the sequences
              themselves — no sample is the reference for any other.
            </p>
            <p className="vp1-use">
              <b>Use it to</b> see whether your samples fall together (one introduction) or apart (more than one), and
              which published strains they sit beside. <b>Bootstrap</b> numbers on the branches say how firmly the
              data support each grouping: near 100 is firm, under about 70 is not.
            </p>
            <details>
              <summary>Which tree to open, and how to read the labels</summary>
              <p>
                <b>Samples with their relatives</b> is the one to start with: the same tree with distant strains
                thinned out, so the labels can be read. <b>Every sequence</b> is the whole tree, and{" "}
                <b>One sample on its own</b> is the figure that sample&apos;s report prints.
              </p>
              <p>
                Published sequences are labelled <b>lineage | country | year | accession</b>; your own samples keep
                their own names and are highlighted when the viewer opens.
              </p>
              <p className="vp1-use">
                Built with {info?.tree || "RAxML"} from a {info?.aligner || "mafft"} alignment, {info?.bootstraps ?? 100} bootstraps.
                {tables?.neighbourhood_tree ? ` The thinned tree shows ${tables.neighbourhood_tree.leaves} of ${tables.neighbourhood_tree.full_tree_leaves} sequences: ${tables.neighbourhood_tree.samples} of your samples, ${tables.neighbourhood_tree.neighbourhood} close relatives and ${tables.neighbourhood_tree.lineage_representatives} stand-ins for the other lineages. Branch lengths are the full tree's.` : ""}
              </p>
            </details>
          </div>
          <div style={{ display: "flex", gap: "0.5em", flexWrap: "wrap" }}>
            {hoodFile ? (
              <button onClick={() => window.open(treeUrlFor(hoodFile.path) + treeOpts(samples), "_blank", "noopener")}
                title="Your samples with the published strains closest to them, and one stand-in per other lineage. Start here.">Samples with their relatives</button>
            ) : <span className="muted">Not written for this run{run?.state === "running" ? " yet" : ". Build the tree again; it is written with the tables."}</span>}
            {treeFile ? (
              <button className="ghost" onClick={() => window.open(treeUrlFor(treeFile.path) + treeOpts(samples), "_blank", "noopener")}
                title="Every published sequence this tree was built from. Crowded, but complete.">Every sequence</button>
            ) : null}
            {Object.keys(sampleTrees).length ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "0.4em" }}>
                <select value={treeSample} onChange={(e) => setTreeSample(e.target.value)} title="One sample with its own relatives and one stand-in per other lineage: the figure that sample's report prints.">
                  <option value="">One sample on its own…</option>
                  {Object.entries(sampleTrees).map(([s_, t]) => <option key={s_} value={s_}>{s_} ({t.leaves} sequences)</option>)}
                </select>
                <button disabled={!sampleTreeFile} onClick={() => sampleTreeFile && window.open(treeUrlFor(sampleTreeFile.path) + treeOpts([treeSample]), "_blank", "noopener")}>
                  Open
                </button>
              </span>
            ) : null}
          </div>
          {info?.context_selection ? (
            <p className="vp1-hint">
              Published sequences in this tree: {Object.entries(info.context_selection).map(([sero, c]) =>
                `${c.selected} of the ${c.context_total} known for serotype ${sero}${c.focus && c.focus.length ? ` (around ${c.focus.join(", ")})` : ""}`).join("; ")}.
              The rest are left out so the tree stays readable; nothing close to a sample is dropped.
            </p>
          ) : null}
        </div>
      ) : null}

      {section === "neighbours" ? (
        <div>
          <div className="vp1-explain">
            <h4>The published strains closest to each sample</h4>
            <p>
              For every sample, the published sequences most like it, nearest first, with where and when each was
              collected. The list stops at {neigh?.neighbourhoods ? (Object.values(neigh.neighbourhoods)[0]?.within_pct ?? 3) : 3}% away from the
              nearest one, so it holds the strains genuinely in the same neighbourhood rather than a fixed count.
            </p>
            <p className="vp1-use">
              <b>Use it to</b> answer where a sample looks like it came from: a row at 99–100% from a named country
              and year is the closest published relative on record. It is the evidence behind the lineage call in{" "}
              <b>Typing</b>, read sample by sample.
            </p>
          </div>
          {!neigh ? <span className="muted">Not written for this run{run?.state === "running" ? " yet" : ""}.</span> : null}
          {neigh ? samples.map((s) => {
            const v = neigh.neighbourhoods?.[s];
            if (!v) return null;
            return (
              <details key={s} className="vp1-neighbour" open={samples.length <= 3}>
                <summary>
                  <strong>{s}</strong>
                  <span>{v.nearest ? ` — closest is ${v.nearest.name} at ${v.nearest.identity}%` : ""}
                    {typeof v.n_within === "number" ? `, ${v.n_within} more within ${v.within_pct}%` : ""}</span>
                </summary>
                <div className="vp1-scroll vp1-short">
                  <table>
                    <thead><tr><th>#</th><th>Identity</th><th>Published sequence</th><th>Lineage</th><th>Country</th><th>Year</th><th>Host</th><th>Isolate</th></tr></thead>
                    <tbody>
                      {(v.records || []).map((r, i) => (
                        <tr key={r.name}>
                          <td className="vp1-num">{i + 1}</td><td className="vp1-num">{r.identity}%</td><td>{r.name}</td><td>{r.lineage || "-"}</td>
                          <td>{r.country || "-"}</td><td className="vp1-num">{r.year && String(r.year) !== "0" ? r.year : "-"}</td><td>{r.host || "-"}</td><td>{r.isolate || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            );
          }) : null}
        </div>
      ) : null}

      {section === "sites" ? (
        <div>
          <div className="vp1-explain">
            <h4>The positions where these sequences differ</h4>
            <p>
              One column per position in VP1 that is not the same in every sequence, one row per sample and close
              relative. Positions where everything agrees are left out, so what remains is only what separates them.
            </p>
            <p className="vp1-use">
              <b>Use it to</b> see exactly which changes two samples share and which belong to one of them alone —
              the detail behind their place in the tree, and what you would quote in a report.{" "}
              <b>Amino acids</b> shows changes to the protein (where antigenic differences live); <b>nucleotides</b>{" "}
              shows every change, including the silent ones.
            </p>
            <details>
              <summary>Which ordering to use</summary>
              <p>
                <b>Shared differences first</b> puts the positions that group several sequences at the left and the
                one-off changes at the right, so patterns stand out. <b>In gene order</b> keeps VP1&apos;s own
                numbering, for quoting a position.
              </p>
            </details>
          </div>
          <div className="vp1-row">
            <span className="vp1-label">Show:</span>
            <div className="mode-toggle">
              <button className={sitesLevel === "aa" ? "active" : ""} onClick={() => setSitesLevel("aa")} title="Changes to the protein VP1 codes for.">Amino acids</button>
              <button className={sitesLevel === "nt" ? "active" : ""} onClick={() => setSitesLevel("nt")} title="Every change in the DNA, including those that do not change the protein.">Nucleotides</button>
            </div>
            <span className="vp1-label">Order:</span>
            <div className="mode-toggle">
              <button className={sitesKind === "cascade" ? "active" : ""} onClick={() => setSitesKind("cascade")} title="Positions shared by several sequences first, one-off changes last.">Shared first</button>
              <button className={sitesKind === "sorted" ? "active" : ""} onClick={() => setSitesKind("sorted")} title="Positions in VP1's own order.">In gene order</button>
            </div>
            {sitesFile ? <>
              <button className="ghost" onClick={() => previewXlsx(sitesFile.path)} title="Open this table in a new tab">Open</button>
              <button className="ghost" onClick={() => downloadOutput(sitesFile.path, sitesFile.download_name)} title="Download as a spreadsheet">Download</button>
            </> : null}
          </div>
          {tables?.counts ? (
            <p className="vp1-hint" style={{ marginTop: 0 }}>
              {tables.counts[sitesLevel]?.rows} sequences (your samples and their relatives, in tree order),{" "}
              {tables.counts[sitesLevel]?.variable_sites} positions that differ.
            </p>
          ) : null}
          {sitesUrl ? (
            <iframe title={`VP1 ${sitesKind} ${sitesLevel}`} src={sitesUrl} style={{ width: "100%", height: "60vh", border: "1px solid var(--border)", borderRadius: "10px", marginTop: "0.5em", background: "#fff" }} />
          ) : <p className="vp1-hint">Not written for this run{tables?.status === "failed" ? `: ${tables.error}` : run?.state === "running" ? " yet" : ". Build the tree again; the tables are written with it."}</p>}
        </div>
      ) : null}

      {section === "files" ? (
        <div>
          <div className="vp1-explain">
            <h4>Everything this tree run wrote</h4>
            <p>
              The sequences that went in, the alignment, the tree files and the tables, on disk in the run folder
              shown above. Nothing here is needed to read the views; it is what to take away.
            </p>
            <p className="vp1-use">
              <b>Use it to</b> open a file in another program, attach one to a report, or keep the run as a record.
            </p>
          </div>
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
