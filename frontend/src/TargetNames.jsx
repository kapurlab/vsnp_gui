import { useEffect, useMemo, useState } from "react";
import Elapsed from "./Elapsed";
import { askOnPage } from "./pageAsk.jsx";

// The target-name dropdown and its manager.
//
// A target name is the taxonomy name the read parser looks for in the Kraken
// report (matched exactly, ignoring case). The list is the names that ship with
// the tool plus the names saved here. Saved names live in this user's config
// folder (~/.config/kraken_id_parse_gui/target_names.yaml), not in the tool's
// checkout, so they survive updates — and the vSNP GUI reads the same file, so
// a name saved in either tool is in both dropdowns.
//
// To add one, the manager offers the names Kraken actually reported in this
// project's runs: picking one of those gets the spelling right by
// construction, which is the whole difficulty with typing a taxonomy name.
//
// SHARED with vsnp_gui (frontend/src/TargetNames.jsx there): keep the two
// files identical. Each GUI passes its own API paths as `endpoints`.

// This tool's API. vsnp_gui passes its own (under /api/kraken/…).
export const KRAKEN_GUI_ENDPOINTS = {
  list: "./api/taxa",
  add: "./api/taxa",
  remove: "./api/taxa/remove",
  reportTaxa: (project) => `./api/projects/${encodeURIComponent(project)}/report-taxa?min_percent=0.01`,
};

const RANK_LABEL = {
  D: "domain", K: "kingdom", P: "phylum", C: "class", O: "order", F: "family",
  G: "genus", S: "species",
};
function rankLabel(code) {
  if (!code) return "";
  const base = RANK_LABEL[code[0]] || code;
  return code.length > 1 && RANK_LABEL[code[0]] ? `below ${base}` : base;
}
function pct(x) {
  return x >= 10 ? x.toFixed(1) : x >= 0.1 ? x.toFixed(2) : x.toPrecision(2);
}

export function useTargetNames(endpoints = KRAKEN_GUI_ENDPOINTS) {
  const [entries, setEntries] = useState([]);
  const [savedFile, setSavedFile] = useState("");
  const [loadError, setLoadError] = useState("");
  const apply = (d) => {
    if (Array.isArray(d.entries)) setEntries(d.entries);
    else if (Array.isArray(d.taxa)) setEntries(d.taxa.map((name) => ({ name, source: "shipped" })));
    if (d.saved_file) setSavedFile(d.saved_file);
  };
  useEffect(() => {
    fetch(endpoints.list)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(apply)
      .catch((e) => setLoadError(`Could not load the target names (${e.message}).`));
  }, []);
  async function post(path, name) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    apply(d);
    return d;
  }
  return {
    entries, savedFile, loadError, endpoints,
    add: (name) => post(endpoints.add, name),
    remove: (name) => post(endpoints.remove, name),
  };
}

export default function TargetNamePicker({ value, onChange, disabled, project, store, listId = "kraken-report-names" }) {
  const { entries, savedFile, loadError } = store;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState(null);        // {text, error?}
  const [busy, setBusy] = useState(0);          // since, while saving
  const [reported, setReported] = useState({ project: "", names: [], since: 0, error: "" });

  const shipped = entries.filter((e) => e.source !== "saved");
  const saved = entries.filter((e) => e.source === "saved");
  const known = useMemo(() => new Set(entries.map((e) => e.name.toLowerCase())), [entries]);

  // Names from this project's Kraken reports, fetched when the manager opens.
  useEffect(() => {
    if (!open || !project || reported.project === project) return;
    const since = Date.now();
    setReported({ project, names: [], since, error: "" });
    fetch(store.endpoints.reportTaxa(project))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setReported({ project, names: d.names || [], since: 0, error: "" }))
      .catch((e) => setReported({ project, names: [], since: 0, error: e.message }));
  }, [open, project]);

  const byName = useMemo(() => {
    const m = new Map();
    for (const r of reported.names) m.set(r.name.toLowerCase(), r);
    return m;
  }, [reported.names]);

  // The useful picks are family, genus and species-level names (G1 is a
  // species group such as "Mycobacterium tuberculosis complex"); "Bacteria" or
  // "cellular organisms" would parse nearly every read. Not already listed.
  const suggestions = useMemo(() => reported.names
    .filter((r) => !known.has(r.name.toLowerCase()))
    .filter((r) => /^(F|G|S)\d*$/.test(r.rank || ""))
    .filter((r) => r.percent >= 0.1)
    .slice(0, 16), [reported.names, known]);

  const typed = draft.trim().replace(/\s+/g, " ");
  const typedHit = typed ? byName.get(typed.toLowerCase()) : null;
  const typedDup = typed && known.has(typed.toLowerCase());

  async function save(name = typed) {
    if (!name) return;
    setBusy(Date.now());
    setMsg(null);
    try {
      const d = await store.add(name);
      setDraft("");
      onChange(d.name || name);
      setMsg({ text: d.added ? `Saved “${d.name}” and selected it.` : `“${d.name}” is already in the list — selected it.` });
    } catch (e) {
      setMsg({ text: `Not saved: ${e.message}`, error: true });
    } finally {
      setBusy(0);
    }
  }

  async function removeName(name) {
    if (!(await askOnPage({ okLabel: "Remove", message: `Remove “${name}” from your saved target names? It leaves the dropdown in both Kraken ID Parse and the vSNP GUI. Results already produced with it are not touched.` }))) return;
    try {
      await store.remove(name);
      if (value === name) onChange("");
      setMsg({ text: `Removed “${name}”.` });
    } catch (e) {
      setMsg({ text: `Not removed: ${e.message}`, error: true });
    }
  }

  return (
    <div className="target-picker">
      <div className="row tight">
        <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
          <option value="">Select a target name…</option>
          <optgroup label="Ships with Kraken ID Parse">
            {shipped.map((e) => <option key={e.name} value={e.name}>{e.name}</option>)}
          </optgroup>
          {saved.length > 0 && (
            <optgroup label="Saved by you">
              {saved.map((e) => <option key={e.name} value={e.name}>{e.name}</option>)}
            </optgroup>
          )}
          {value && !known.has(value.toLowerCase()) && <option value={value}>{value}</option>}
        </select>
        <button type="button" className={`ghost${open ? " action" : ""}`} aria-expanded={open}
          onClick={() => setOpen((x) => !x)} title="Add or remove target names">
          {open ? "Done" : "Manage…"}
        </button>
      </div>
      {loadError && <div className="form-hint danger-text">{loadError}</div>}

      {open && (
        <div className="target-manager">
          <div className="tm-head">Add a target name</div>
          <div className="row tight">
            <input
              list={listId}
              placeholder="Taxonomy name exactly as Kraken reports it"
              value={draft}
              onChange={(e) => { setDraft(e.target.value); setMsg(null); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); save(); } }}
              disabled={!!busy}
            />
            <button type="button" onClick={() => save()} disabled={!typed || !!busy || typedDup}>
              {busy ? <>Saving… <Elapsed since={busy} /></> : "Save"}
            </button>
          </div>
          <datalist id={listId}>
            {reported.names.slice(0, 400).map((r) => (
              <option key={r.name} value={r.name}>{`${pct(r.percent)}% · ${rankLabel(r.rank)}`}</option>
            ))}
          </datalist>
          {typed && (
            <div className={`tm-check ${typedDup ? "" : typedHit ? "ok" : reported.names.length ? "warn" : ""}`}>
              {typedDup
                ? "Already in the list."
                : typedHit
                  ? `✓ Kraken reported this name in this project — up to ${pct(typedHit.percent)}% of reads (${typedHit.best_sample}${typedHit.samples > 1 ? ` and ${typedHit.samples - 1} more` : ""}).`
                  : reported.names.length
                    ? "Not in any Kraken report in this project. The parser needs the exact spelling Kraken uses — check it against a Krona chart or report before running."
                    : "No Kraken report in this project to check the spelling against yet."}
            </div>
          )}
          {msg && <div className={`tm-msg${msg.error ? " error" : ""}`}>{msg.text}</div>}

          <div className="tm-head">
            From this project’s Kraken reports
            {reported.since ? <span className="muted"> — reading… <Elapsed since={reported.since} /></span> : null}
          </div>
          {reported.error ? (
            <div className="form-hint danger-text">Could not read the reports ({reported.error}).</div>
          ) : !reported.since && !reported.names.length ? (
            <div className="form-hint">No Kraken runs in this project yet. A “Kraken + Krona only” run lists what is in a sample; its names then show up here.</div>
          ) : suggestions.length ? (
            <div className="tm-chips">
              {suggestions.map((r) => (
                <button key={r.name} type="button" className="tm-chip" onClick={() => setDraft(r.name)}
                  title={`${r.name}\n${pct(r.percent)}% of reads in ${r.best_sample} · ${rankLabel(r.rank)} · seen in ${r.samples} sample${r.samples === 1 ? "" : "s"}\nClick to put it in the box, then Save.`}>
                  <span>{r.name}</span><span className="tm-chip-pct">{pct(r.percent)}%</span>
                </button>
              ))}
            </div>
          ) : !reported.since ? (
            <div className="form-hint">Every prominent name in these reports is already in the list. Type to search all {reported.names.length}.</div>
          ) : null}

          <div className="tm-head">Saved by you <span className="muted">({saved.length})</span></div>
          {saved.length ? (
            <div className="tm-saved">
              {saved.map((e) => (
                <span key={e.name} className="tm-saved-item">
                  {e.name}
                  <button type="button" className="chip-remove" title={`Remove “${e.name}”`} onClick={() => removeName(e.name)}>×</button>
                </span>
              ))}
            </div>
          ) : (
            <div className="form-hint">None yet. The {shipped.length} names that ship with the tool are always listed.</div>
          )}
          <div className="form-hint">
            Saved in {savedFile ? <code>{savedFile}</code> : "your Kraken ID Parse settings folder"} — outside the
            tool’s own files, so updates keep it. Kraken ID Parse and the vSNP GUI both read this list.
          </div>
        </div>
      )}
    </div>
  );
}
