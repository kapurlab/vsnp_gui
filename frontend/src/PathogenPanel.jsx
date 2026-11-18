import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Elapsed from "./Elapsed.jsx";

// Pathogen add-ons in the page (backend: app/addons.py, app/addon_run.py).
//
// A project names a pathogen add-on (FMDV, later HPAI, ASF...) when it is
// created, or names none. Everything here is generic: what a pathogen shows
// comes from its add-on's manifest (columns, actions, reports) and its
// outputs (result.json rows, summary.json, action runs), so a new add-on needs
// no change in this file. A project that names no pathogen never renders any
// of it.

// ---- data -----------------------------------------------------------------

export function useAddons(apiBase, enabled) {
  const [info, setInfo] = useState({ addons: [], default_pathogen: null, loaded: false });
  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/addons`);
      if (!res.ok) return;
      const data = await res.json();
      setInfo({ addons: data.addons || [], default_pathogen: data.default_pathogen || null, loaded: true });
    } catch (_) { /* no add-ons is the normal case */ }
  }, [apiBase]);
  useEffect(() => { if (enabled) load(); }, [enabled, load]);
  return { ...info, reload: load };
}

export function addonById(addons, id) {
  return (addons || []).find((a) => a.id === id) || null;
}

// The value a row holds for one of the add-on's columns.
export function pathogenCell(row, col) {
  const v = row ? row[`pathogen:${col.key}`] : undefined;
  if (v === undefined || v === null || v === "") {
    return row && row["pathogen:_status"] === "pending" ? "…" : "-";
  }
  if (typeof v === "number") {
    const s = Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 3 : 1);
    return col.unit ? `${s}${col.unit === "%" ? "%" : ` ${col.unit}`}` : s;
  }
  return String(v);
}

function fileHref(apiBase, project, path, inline = true) {
  return `${apiBase}/api/projects/${encodeURIComponent(project)}/download-file?path=${encodeURIComponent(path)}${inline ? "&inline=1" : ""}`;
}

// Open an add-on output the way the rest of the app opens that kind of file:
// a tree in the tree viewer (every listed sample lit, midpoint-rooted), a
// table in the formatted preview, anything else in the browser.
export function openAddonFile(apiBase, project, f, samples) {
  const p = (f.path || "").toLowerCase();
  if (/\.(tre|nwk|tree)$/.test(p)) {
    const base = window.location.pathname.replace(/[^/]*$/, "");
    const lit = (samples || []).join(",");
    const url = `${base}?view=tree&project=${encodeURIComponent(project)}&path=${encodeURIComponent(f.path)}`
      + (lit ? `&search=${encodeURIComponent(lit)}&fit=1` : "") + "&midpoint=1&bootstrap=1";
    window.open(url, "_blank", "noopener");
  } else if (/\.(xlsx|xlsm)$/.test(p)) {
    window.open(`${apiBase}/api/projects/${encodeURIComponent(project)}/preview-xlsx?path=${encodeURIComponent(f.path)}`, "_blank", "noopener");
  } else {
    window.open(fileHref(apiBase, project, f.path), "_blank", "noopener");
  }
}

// ---- project form -----------------------------------------------------------

function flattenScopes(options, depth = 0, out = []) {
  (options || []).forEach((o) => {
    out.push({ value: o.value, label: `${"  ".repeat(depth)}${o.label || o.value}` });
    flattenScopes(o.children, depth + 1, out);
  });
  return out;
}

export function ScopePicker({ apiBase, addonId, value, onChange, disabled }) {
  const [scopes, setScopes] = useState(null);
  useEffect(() => {
    let cancelled = false;
    setScopes(null);
    if (!addonId) return undefined;
    fetch(`${apiBase}/api/addons/${encodeURIComponent(addonId)}/scopes`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        setScopes(d);
        if (!value && d.default) onChange(d.default);
      })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, addonId]);
  const opts = useMemo(() => flattenScopes(scopes ? scopes.options : []), [scopes]);
  if (!addonId || !opts.length) return null;
  return (
    <select value={value || ""} onChange={(e) => onChange(e.target.value)} disabled={disabled}
            title={`${(scopes && scopes.label) || "Scope"}: which references the add-on may choose from`}
            style={{ minWidth: "10rem" }}>
      {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function PathogenProjectFields({ apiBase, addons, pathogen, setPathogen, scope, setScope }) {
  if (!addons || !addons.length) return null;
  const chosen = addonById(addons, pathogen);
  return (
    <div className="row" style={{ marginTop: "4px", alignItems: "center", flexWrap: "wrap" }}>
      <select value={pathogen || ""} onChange={(e) => { setPathogen(e.target.value); setScope(""); }}
              title="A pathogen add-on chooses each sample's reference and adds its own results and reports. None: a stock vSNP project."
              style={{ minWidth: "10rem" }}>
        <option value="">Pathogen: none (stock vSNP)</option>
        {addons.map((a) => (
          <option key={a.id} value={a.id} disabled={!a.ready}>
            {a.name}{a.ready ? "" : " (not ready)"}
          </option>
        ))}
      </select>
      <ScopePicker apiBase={apiBase} addonId={pathogen} value={scope} onChange={setScope} />
      {chosen && !chosen.ready ? (
        <span className="note error" style={{ fontSize: "0.8em" }}>{(chosen.problems || []).join("; ")}</span>
      ) : chosen ? (
        <span className="muted" style={{ fontSize: "0.8em" }}>
          {chosen.name} {chosen.version}: the reference is chosen per sample.
        </span>
      ) : null}
    </div>
  );
}

// ---- Step 1: the Reference block of a pathogen project ------------------------

export function PathogenStep1Note({ addon, scope, info }) {
  const refs = info && info.samples_by_reference ? Object.keys(info.samples_by_reference) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
        <span style={{ padding: "3px 10px", borderRadius: "10px", background: "var(--badge-success-bg)",
                       color: "var(--badge-success-fg)", fontWeight: 600, fontSize: "0.9em" }}>
          {addon ? addon.name : "Pathogen add-on"}
        </span>
        <span className="muted" style={{ fontSize: "0.8em" }}>
          chooses each sample&apos;s reference{scope ? ` (scope: ${scope})` : ""}
        </span>
      </div>
      {addon && !addon.ready ? (
        <div className="note error">{(addon.problems || []).join("; ")}</div>
      ) : null}
      {refs.length > 1 ? (
        <div className="muted" style={{ fontSize: "0.8em" }}>
          Samples are on {refs.length} references, as expected: Step 2 compares one reference at a time.
        </div>
      ) : null}
    </div>
  );
}

// ---- jobs -----------------------------------------------------------------

function useJob(apiBase, onDone) {
  const [job, setJob] = useState(null);   // {id, label, startedAt, status}
  const timer = useRef(null);
  useEffect(() => () => clearInterval(timer.current), []);
  const start = useCallback((id, label) => {
    clearInterval(timer.current);
    const startedAt = Date.now();
    setJob({ id, label, startedAt, status: "running" });
    const tick = async () => {
      try {
        const res = await fetch(`${apiBase}/api/jobs/${id}`);
        if (!res.ok) return;
        const j = await res.json();
        if (j.status && j.status !== "running" && j.status !== "queued") {
          clearInterval(timer.current);
          setJob({ id, label, startedAt, status: j.status });
          if (onDone) onDone(j);
        }
      } catch (_) { /* transient */ }
    };
    timer.current = setInterval(tick, 1500);
    tick();
  }, [apiBase, onDone]);
  return [job, start, setJob];
}

// ---- the Pathogen tab -------------------------------------------------------

function levelChip(level) {
  // The same badge Step 1 Results uses for its QC verdict.
  return <span className={`qc-badge qc-badge-${level || "pass"}`} title={level}>{level || "-"}</span>;
}

function ActionForm({ action, rows, onRun, busy }) {
  const params = action.params || [];
  const [values, setValues] = useState(() => Object.fromEntries(params.map((p) => [p.name, p.default || ""])));
  const choices = (p) => {
    if (Array.isArray(p.choices)) return p.choices;
    if (typeof p.choices_from === "string" && p.choices_from.startsWith("metric:")) {
      const key = p.choices_from.slice("metric:".length);
      return [...new Set((rows || []).map((r) => (r.metrics || {})[key]).filter(Boolean))].sort();
    }
    return null;
  };
  return (
    <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
      {params.map((p) => {
        const opts = choices(p);
        return opts ? (
          <select key={p.name} value={values[p.name] || ""} title={p.label || p.name}
                  onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}>
            <option value="">{p.label || p.name}…</option>
            {opts.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        ) : (
          <input key={p.name} placeholder={p.label || p.name} value={values[p.name] || ""}
                 onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} style={{ width: "8rem" }} />
        );
      })}
      <button type="button" disabled={busy} title={action.description || ""}
              onClick={() => onRun(action.id, Object.fromEntries(Object.entries(values).filter(([, v]) => v)))}>
        {action.label || action.id}
      </button>
    </div>
  );
}

export function PathogenPanel({ apiBase, project, onChanged }) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadStartedAt, setLoadStartedAt] = useState(0);
  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true); setLoadStartedAt(Date.now());
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/pathogen`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setError(data.detail || `HTTP ${res.status}`); return; }
      setInfo(data); setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [apiBase, project]);
  useEffect(() => { setInfo(null); load(); }, [load]);
  const [job, startJob] = useJob(apiBase, useCallback(() => { load(); if (onChanged) onChanged(); }, [load, onChanged]));

  async function post(url, body, label) {
    setError("");
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setError(data.detail || `HTTP ${res.status}`); return; }
    startJob(data.job_id, label);
  }

  if (!project) return null;
  if (!info) {
    return error ? <div className="note error">{error}</div>
      : <div className="muted">Loading the pathogen results… <Elapsed since={loadStartedAt} /></div>;
  }
  if (!info.pathogen) return <div className="muted">This project names no pathogen add-on.</div>;
  const addon = info.addon;
  if (!addon) {
    return (
      <div className="note error">
        This project&apos;s pathogen is &apos;{info.pathogen}&apos;, and that add-on is not installed here.
        Install it with bdtools (bdtools install {info.pathogen}_addon), then restart vsnp_gui.
      </div>
    );
  }
  const cols = addon.columns || [];
  // The reference the sample was aligned to, unless the add-on shows it itself.
  const showRef = !cols.some((c) => c.key === "reference");
  const rows = info.rows || [];
  const sampleNames = rows.map((r) => r.sample);
  const busy = Boolean(job && job.status === "running");
  const summaryFiles = (info.summary && info.summary.files) || [];
  return (
    <div className="pathogen-panel" style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
        <strong>{addon.name}</strong>
        <span className="muted" style={{ fontSize: "0.85em" }}>
          {addon.version}{info.scope ? ` · scope ${info.scope}` : ""}
          {info.summary && info.summary.text ? ` · ${info.summary.text}` : ""}
        </span>
        {summaryFiles.map((f) => (
          <button key={f.path} type="button" className="ghost" onClick={() => openAddonFile(apiBase, project, f, sampleNames)}>
            {f.label || f.name}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <button type="button" onClick={load} disabled={loading}>
          {loading ? (<>Loading… <Elapsed since={loadStartedAt} /></>) : "Refresh"}
        </button>
        <button type="button" className="ghost" disabled={busy || info.step1_running}
                title={info.step1_running ? "Step 1 is running" : "Run the add-on's per-sample and run steps again, without re-aligning"}
                onClick={() => post(`${apiBase}/api/projects/${encodeURIComponent(project)}/pathogen/rerun`, {}, "Re-run add-on steps")}>
          Re-run add-on steps
        </button>
      </div>
      {error ? <div className="note error">{error}</div> : null}
      {addon.problems && addon.problems.length ? <div className="note error">{addon.problems.join("; ")}</div> : null}
      {job ? (
        <div className={`note${job.status === "failed" ? " error" : ""}`}>
          {job.label}: {job.status === "running" ? (<>running <Elapsed since={job.startedAt} /></>) : job.status}
        </div>
      ) : null}
      {(info.no_reference || []).length ? (
        <div className="note error">
          <strong>No reference chosen</strong> for {info.no_reference.length} sample{info.no_reference.length === 1 ? "" : "s"}:
          {info.no_reference.map((n) => (
            <div key={n.sample}><b>{n.sample}</b> <span className="muted">— {n.reason}</span></div>
          ))}
          <div className="muted" style={{ fontSize: "0.85em" }}>Force re-run in Step 1 tries them again.</div>
        </div>
      ) : null}
      <div style={{ overflowX: "auto" }}>
        <table className="qc-table">
          <thead>
            <tr>
              <th>Sample</th><th>Level</th>{showRef ? <th>Reference</th> : null}
              {cols.map((c) => <th key={c.key}>{c.label}</th>)}
              <th>Files</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((r) => (
              <tr key={r.sample}>
                <td>{r.sample}</td>
                <td title={((r.flags || {}).reasons || []).join("; ")}>{levelChip((r.flags || {}).level)}</td>
                {showRef ? <td>{r.reference || "-"}</td> : null}
                {cols.map((c) => <td key={c.key}>{pathogenCell(Object.fromEntries(Object.entries(r.metrics || {}).map(([k, v]) => [`pathogen:${k}`, v])), c)}</td>)}
                <td>
                  {(r.files || []).map((f) => (
                    <button key={f.path} type="button" className="ghost" style={{ fontSize: "11px", padding: "1px 6px" }}
                            onClick={() => openAddonFile(apiBase, project, f, [r.sample])}>
                      {f.label || f.name}
                    </button>
                  ))}
                </td>
              </tr>
            )) : (
              <tr><td colSpan={(showRef ? 4 : 3) + cols.length} className="muted">No add-on results yet: run Step 1.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {(addon.actions || []).length ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <h3 style={{ margin: 0 }}>Actions</h3>
          {addon.actions.map((a) => (
            <ActionForm key={a.id} action={a} rows={rows} busy={busy}
                        onRun={(id, params) => post(`${apiBase}/api/projects/${encodeURIComponent(project)}/pathogen/actions/${encodeURIComponent(id)}`,
                                                    { params }, a.label || id)} />
          ))}
        </div>
      ) : null}
      {(info.runs || []).length ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <h3 style={{ margin: 0 }}>Runs</h3>
          {info.runs.map((run) => (
            <details key={run.id} className="results-group" open={run === info.runs[0]}>
              <summary>
                <b>{run.action}</b>{" "}
                {Object.entries(run.params || {}).map(([k, v]) => `${k}=${v}`).join(" ")}{" "}
                <span className="muted">{run.id}</span>{" "}
                {run.status === "ok" ? null : <span className={run.status === "failed" ? "note error" : "muted"}>{run.status}</span>}
              </summary>
              {run.summary ? <div className="muted" style={{ fontSize: "0.85em" }}>{run.summary}</div> : null}
              <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                {(run.files || []).map((f) => (
                  <button key={f.path} type="button" className="ghost" onClick={() => openAddonFile(apiBase, project, f, sampleNames)}>
                    {f.label || f.name || f.path.split("/").pop()}
                  </button>
                ))}
              </div>
            </details>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ---- Step 2: which reference this comparison is on ---------------------------

export function CompareOnReference({ apiBase, project, current, onChanged }) {
  const [info, setInfo] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    if (!project) return undefined;
    fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/pathogen`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled) setInfo(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [apiBase, project, current]);
  if (!info || !info.pathogen) return null;
  const byRef = info.samples_by_reference || {};
  const refs = Object.keys(byRef).sort((a, b) => byRef[b].length - byRef[a].length || a.localeCompare(b));
  async function choose(ref) {
    setSaving(true); setError("");
    try {
      const res = await fetch(`${apiBase}/api/projects/${encodeURIComponent(project)}/pathogen`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comparison_reference: ref }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setError(data.detail || `HTTP ${res.status}`); return; }
      setInfo(data);
      if (onChanged) onChanged(ref);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="block">
      <h3>Compare on reference</h3>
      <div className="muted" style={{ fontSize: "0.85em" }}>
        {info.addon ? info.addon.name : "The add-on"} chose each sample&apos;s reference. A comparison is one
        reference at a time; samples on other references are left out of it and listed.
      </div>
      <select value={current || ""} disabled={saving} onChange={(e) => choose(e.target.value)} style={{ minWidth: "14rem" }}>
        <option value="">Choose the comparison reference…</option>
        {refs.map((r) => <option key={r} value={r}>{r} ({byRef[r].length} sample{byRef[r].length === 1 ? "" : "s"})</option>)}
      </select>
      {saving ? <span className="muted"> Saving… <Elapsed /></span> : null}
      {error ? <div className="note error">{error}</div> : null}
    </div>
  );
}
