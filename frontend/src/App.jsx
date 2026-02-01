import React, { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

function parseAccessions(text) {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [projects, setProjects] = useState([]);
  const [references, setReferences] = useState([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [settings, setSettings] = useState({
    vsnp3_path: "",
    projects_root: "",
    conda_env: "",
    conda_exe: "",
    conda_env_path: "",
    sra_allow_insecure_https: false
  });
  const [sraText, setSraText] = useState("");
  const [sraFolder, setSraFolder] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [reference, setReference] = useState("");
  const [debugMode, setDebugMode] = useState(false);
  const [jobId, setJobId] = useState("");
  const [jobStatus, setJobStatus] = useState("idle");
  const [logs, setLogs] = useState([]);
  const [uploadStatus, setUploadStatus] = useState("");
  const [qcRows, setQcRows] = useState([]);
  const [qcLoading, setQcLoading] = useState(false);
  const [qcError, setQcError] = useState("");
  const [showFlaggedOnly, setShowFlaggedOnly] = useState(false);
  const [excluded, setExcluded] = useState({});
  const [step1Status, setStep1Status] = useState([]);
  const [step1StatusError, setStep1StatusError] = useState("");
  const [step1LogSample, setStep1LogSample] = useState("");
  const [step1LogText, setStep1LogText] = useState("");
  const [step1LogLoading, setStep1LogLoading] = useState(false);
  const [step2SetupMsg, setStep2SetupMsg] = useState("");
  const [refLock, setRefLock] = useState({ references: [] });
  const [step2Outputs, setStep2Outputs] = useState([]);
  const [step2Groups, setStep2Groups] = useState([]);
  const [step2OutputsError, setStep2OutputsError] = useState("");
  const [preflight, setPreflight] = useState(null);
  const [preflightError, setPreflightError] = useState("");
  const [showChecklist, setShowChecklist] = useState(true);

  const selected = useMemo(
    () => projects.find((p) => p.name === selectedProject),
    [projects, selectedProject]
  );

  async function loadAll() {
    const [cfg, proj, refs] = await Promise.all([
      fetch(`${API_BASE}/api/config`).then((r) => r.json()),
      fetch(`${API_BASE}/api/projects`).then((r) => r.json()),
      fetch(`${API_BASE}/api/references`).then((r) => r.json())
    ]);
    setConfig(cfg);
    setProjects(proj);
    setReferences(refs);
    setSettings({
      vsnp3_path: cfg.vsnp3_path || "",
      projects_root: cfg.projects_root || "",
      conda_env: cfg.conda_env || "",
      conda_exe: cfg.conda_exe || "",
      conda_env_path: cfg.conda_env_path || "",
      sra_allow_insecure_https: Boolean(cfg.sra?.allow_insecure_https)
    });
    if (!selectedProject && proj.length) {
      setSelectedProject(proj[0].name);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  useEffect(() => {
    if (!settings.conda_env) return;
    runPreflight();
  }, [settings.conda_env]);

  useEffect(() => {
    if (!jobId) return;
    setLogs([]);
    setJobStatus("running");
    const es = new EventSource(`${API_BASE}/api/jobs/${jobId}/events`);
    es.onmessage = (evt) => {
      const line = evt.data;
      if (line.startsWith("[job:")) {
        const status = line.replace("[job:", "").replace("]", "");
        setJobStatus(status);
        es.close();
        return;
      }
      setLogs((prev) => [...prev, line]);
    };
    es.onerror = () => {
      setJobStatus("error");
      es.close();
    };
    return () => es.close();
  }, [jobId]);

  useEffect(() => {
    if (!selectedProject) return;
    setExcluded({});
    loadQC();
    loadStep1Status();
    loadStep2Outputs();
  }, [selectedProject]);

  useEffect(() => {
    if (!selectedProject) return;
    if (jobStatus === "succeeded" || jobStatus === "failed") {
      loadStep1Status();
    }
  }, [jobStatus, selectedProject]);

  async function createProject() {
    if (!newProjectName.trim()) return;
    await fetch(`${API_BASE}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newProjectName.trim() })
    });
    setNewProjectName("");
    await loadAll();
  }

  async function saveSettings() {
    await fetch(`${API_BASE}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vsnp3_path: settings.vsnp3_path,
        projects_root: settings.projects_root,
        conda_env: settings.conda_env,
        conda_exe: settings.conda_exe,
        conda_env_path: settings.conda_env_path,
        sra: { allow_insecure_https: settings.sra_allow_insecure_https }
      })
    });
    await loadAll();
  }

  async function runPreflight() {
    setPreflight(null);
    setPreflightError("");
    const res = await fetch(`${API_BASE}/api/preflight`);
    if (!res.ok) {
      const msg = await res.json();
      setPreflightError(msg.detail || "Preflight failed");
      return;
    }
    const data = await res.json();
    setPreflight(data);
  }

  function parsePercent(value) {
    if (value === undefined || value === null) return null;
    const s = String(value).replace("%", "").trim();
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function parseDepth(value) {
    if (value === undefined || value === null) return null;
    const s = String(value).replace("X", "").trim();
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function isFlagged(row) {
    const avgDepth = parseDepth(row["Average Depth"]);
    const dupPct = parsePercent(row["Duplicate Percent of Mapped Reads"]);
    const r1q20 = parsePercent(row["R1 Passing Q20"]);
    const r2q20 = parsePercent(row["R2 Passing Q20"]);
    if (avgDepth !== null && avgDepth < 40) return true;
    if (dupPct !== null && dupPct > 80) return true;
    if (r1q20 !== null && r1q20 < 50) return true;
    if (r2q20 !== null && r2q20 < 50) return true;
    return false;
  }

  function normalizeReferenceName(ref) {
    if (!ref) return "";
    return String(ref)
      .replace(" Forced", "")
      .replace(" by Best Reference", "")
      .trim();
  }

  async function loadQC() {
    if (!selectedProject) return;
    setQcLoading(true);
    setQcError("");
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_summary`);
    if (!res.ok) {
      const msg = await res.json();
      setQcError(msg.detail || "QC summary failed");
      setQcLoading(false);
      return;
    }
    const data = await res.json();
    setQcRows(data);
    if (!reference) {
      const refCandidate = data
        .map((r) => normalizeReferenceName(r.Reference))
        .find((r) => r && references.some((ref) => ref.name === r));
      if (refCandidate) {
        setReference(refCandidate);
      }
    }
    try {
      const lockRes = await fetch(`${API_BASE}/api/projects/${selectedProject}/reference_lock`);
      if (lockRes.ok) {
        const lock = await lockRes.json();
        setRefLock(lock);
        if (lock.references && lock.references.length === 1) {
          setReference(lock.references[0]);
        }
      }
    } catch {
      setRefLock({ references: [] });
    }
    setQcLoading(false);
  }

  async function loadStep2Outputs() {
    if (!selectedProject) return;
    setStep2OutputsError("");
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2_outputs`);
    if (!res.ok) {
      const msg = await res.json();
      setStep2OutputsError(msg.detail || "Failed to load Step 2 outputs");
      return;
    }
    const data = await res.json();
    if (Array.isArray(data)) {
      setStep2Outputs(data);
      setStep2Groups([]);
    } else {
      setStep2Outputs(data.top || []);
      setStep2Groups(data.groups || []);
    }
  }

  async function openOutput(path) {
    if (!selectedProject) return;
    await fetch(`${API_BASE}/api/projects/${selectedProject}/open`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path })
    });
  }

  async function downloadQC() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_summary.csv`);
    if (!res.ok) return;
    const csv = await res.text();
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedProject}_qc_summary.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function saveExclusions() {
    if (!selectedProject) return;
    const samples = Object.keys(excluded).filter((k) => excluded[k]);
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_exclude`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "Failed to save exclusions");
      return;
    }
    window.alert("Exclusions saved");
  }

  async function linkLocal() {
    if (!selectedProject || !localPath) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/link-local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: localPath })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "Link local failed");
      return;
    }
    await loadAll();
  }

  async function uploadFiles(fileList) {
    if (!selectedProject || !fileList?.length) return;
    setUploadStatus("Uploading...");
    const formData = new FormData();
    Array.from(fileList).forEach((file) => formData.append("files", file));
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/upload`, {
      method: "POST",
      body: formData
    });
    if (!res.ok) {
      const msg = await res.json();
      setUploadStatus(msg.detail || "Upload failed");
      return;
    }
    const data = await res.json();
    setUploadStatus(`Uploaded ${data.uploaded} files`);
    await loadAll();
  }

  async function sraDownload() {
    if (!selectedProject) return;
    const accessions = parseAccessions(sraText);
    if (!accessions.length) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/sra/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accessions, folder: sraFolder || null })
    });
    const data = await res.json();
    setJobId(data.job_id);
  }

  async function step1Setup() {
    if (!selectedProject) return;
    await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/setup`, { method: "POST" });
    await loadAll();
    await loadStep1Status();
  }

  async function step1Run() {
    if (!selectedProject || !reference) return;
    const refValue = reference === "__auto__" ? null : reference;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference: refValue, debug: debugMode })
    });
    const data = await res.json();
    setJobId(data.job_id);
    await loadStep1Status();
  }

  async function step2Setup() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/setup`, { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      const total = data.total ?? data.linked;
      setStep2SetupMsg(`VCFs ready for Step 2: ${total} (linked ${data.linked})`);
    }
    await loadAll();
  }

  async function step2Run() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference: reference || null })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "Step 2 failed to start");
      return;
    }
    const data = await res.json();
    setJobId(data.job_id);
  }

  async function loadStep1Status() {
    if (!selectedProject) return;
    setStep1StatusError("");
    try {
      const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/status`);
      if (!res.ok) {
        const msg = await res.json();
        setStep1StatusError(msg.detail || "Failed to load Step 1 status");
        return;
      }
      const data = await res.json();
      setStep1Status(data.samples || []);
    } catch (err) {
      setStep1StatusError("Failed to load Step 1 status");
    }
  }

  async function viewStep1Log(sample) {
    if (!selectedProject || !sample) return;
    setStep1LogSample(sample);
    setStep1LogText("");
    setStep1LogLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/log?sample=${encodeURIComponent(sample)}`);
      if (!res.ok) {
        const msg = await res.json();
        setStep1LogText(msg.detail || "Log not found");
        return;
      }
      const data = await res.json();
      setStep1LogText(data.log || "");
    } catch (err) {
      setStep1LogText("Failed to load log");
    } finally {
      setStep1LogLoading(false);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>vSNP GUI</h1>
          <p>Local workflow for vSNP3 with clean logs and resilient SRA download fallbacks.</p>
        </div>
        <div className="status-pill">
          <span className="dot" data-state={jobStatus}></span>
          <span>{jobStatus}</span>
        </div>
      </header>

      <main className="grid">
        {showChecklist ? (
          <section className="panel checklist">
            <div className="checklist-header">
              <h2>Start-Up Checklist</h2>
              <button className="ghost" onClick={() => setShowChecklist(false)}>
                Hide
              </button>
            </div>
            <div className="checklist-body">
              <div className="checklist-item">
                <span className="check-title">Set vSNP3 path</span>
                <span className={settings.vsnp3_path ? "ok" : "warn"}>
                  {settings.vsnp3_path || "Missing"}
                </span>
              </div>
              <div className="checklist-item">
                <span className="check-title">Set projects root</span>
                <span className={settings.projects_root ? "ok" : "warn"}>
                  {settings.projects_root || "Missing"}
                </span>
              </div>
              <div className="checklist-item">
                <span className="check-title">Conda env</span>
                <span className={settings.conda_env ? "ok" : "warn"}>
                  {settings.conda_env || "Missing"}
                </span>
              </div>
              <div className="checklist-item">
                <span className="check-title">Preflight</span>
                {preflightError ? (
                  <span className="warn">{preflightError}</span>
                ) : preflight ? (
                  <span className={preflight.missing.length || preflight.issues?.length ? "warn" : "ok"}>
                    {preflight.missing.length
                      ? `Missing: ${preflight.missing.join(", ")}`
                      : "All good"}
                  </span>
                ) : (
                  <span className="muted">Not run</span>
                )}
              </div>
              <div className="checklist-actions">
                <button onClick={runPreflight}>Run Preflight</button>
              </div>
              {preflight?.missing?.length ? (
                <div className="note error">
                  Install missing deps with:
                  <div className="code-line">conda install -n {settings.conda_env || "<env>"} pandas biopython pysam</div>
                </div>
              ) : null}
              {preflight?.issues?.length ? (
                <div className="note error">
                  {preflight.issues.join("; ")}. Fix with:
                  <div className="code-line">conda run -n {settings.conda_env || "<env>"} python -m pip install "pandas&lt;2"</div>
                </div>
              ) : null}
            </div>
          </section>
        ) : (
          <section className="panel checklist collapsed">
            <div className="checklist-header">
              <h2>Start-Up Checklist</h2>
              <button className="ghost" onClick={() => setShowChecklist(true)}>
                Show
              </button>
            </div>
          </section>
        )}
        <section className="panel">
          <h2>Settings</h2>
          <div className="block">
            <label className="label">vSNP3 path</label>
            <input
              placeholder="/Users/vivekkapur/vsnp3"
              value={settings.vsnp3_path}
              onChange={(e) => setSettings({ ...settings, vsnp3_path: e.target.value })}
            />
          </div>
          <div className="block">
            <label className="label">Projects root</label>
            <input
              placeholder="/Users/vivekkapur/vsnp3/projects"
              value={settings.projects_root}
              onChange={(e) => setSettings({ ...settings, projects_root: e.target.value })}
            />
          </div>
          <div className="block">
            <label className="label">Conda env for vSNP3</label>
            <input
              placeholder="vsnp3"
              value={settings.conda_env}
              onChange={(e) => setSettings({ ...settings, conda_env: e.target.value })}
            />
          </div>
          <div className="block">
            <label className="label">Conda executable (optional)</label>
            <input
              placeholder="/Users/vivekkapur/anaconda3/bin/conda"
              value={settings.conda_exe}
              onChange={(e) => setSettings({ ...settings, conda_exe: e.target.value })}
            />
          </div>
          <div className="block">
            <label className="label">Conda env path (optional, overrides conda run)</label>
            <input
              placeholder="/Users/vivekkapur/anaconda3/envs/vivek"
              value={settings.conda_env_path}
              onChange={(e) => setSettings({ ...settings, conda_env_path: e.target.value })}
            />
          </div>
          <div className="block">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={settings.sra_allow_insecure_https}
                onChange={(e) =>
                  setSettings({ ...settings, sra_allow_insecure_https: e.target.checked })
                }
              />
              Allow insecure HTTPS fallback for ENA
            </label>
          </div>
          <div className="block">
            <button onClick={runPreflight}>Run Preflight</button>
            {preflightError ? (
              <div className="note error">{preflightError}</div>
            ) : null}
            {preflight ? (
              <div className="note">
                Checked: {preflight.checked.join(", ")} | Missing:{" "}
                {preflight.missing.length ? preflight.missing.join(", ") : "none"}
              </div>
            ) : null}
          </div>
          <button onClick={saveSettings}>Save Settings</button>
        </section>
        <section className="panel">
          <h2>Projects</h2>
          <div className="row">
            <input
              placeholder="New project name"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
            />
            <button onClick={createProject}>Create</button>
          </div>
          <div className="list">
            {projects.map((p) => (
              <button
                key={p.name}
                className={`list-item ${p.name === selectedProject ? "active" : ""}`}
                onClick={() => setSelectedProject(p.name)}
              >
                <div className="list-title">{p.name}</div>
                <div className="list-meta">
                  FASTQ: {p.fastq_count} | Step1: {p.step1_samples} | VCF: {p.step1_vcfs}
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>Inputs</h2>
          <div className="block">
            <h3>SRA Download</h3>
            <textarea
              placeholder="SRR/SRX/SRS accessions (one per line)"
              value={sraText}
              onChange={(e) => setSraText(e.target.value)}
              rows={6}
            />
            <input
              placeholder="Optional subfolder (e.g. 2026-02-01_batch1)"
              value={sraFolder}
              onChange={(e) => setSraFolder(e.target.value)}
            />
            <button onClick={sraDownload} disabled={!selectedProject}>Download</button>
          </div>
          <div className="block">
            <h3>Bring Your Own FASTQ</h3>
            <input
              placeholder="/path/to/fastq_dir"
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
            />
            <button onClick={linkLocal} disabled={!selectedProject}>Link Local Files</button>
          </div>
          <div className="block">
            <h3>Upload / Drag & Drop</h3>
            <div
              className="dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                uploadFiles(e.dataTransfer.files);
              }}
            >
              <input
                type="file"
                multiple
                onChange={(e) => uploadFiles(e.target.files)}
              />
              <span>Drop FASTQ.GZ files here or click to select</span>
            </div>
            <div className="folder-pick">
              <label className="label">Pick a folder (Safari supported)</label>
              <input
                type="file"
                multiple
                webkitdirectory="true"
                onChange={(e) => uploadFiles(e.target.files)}
              />
            </div>
            {uploadStatus ? <div className="note">{uploadStatus}</div> : null}
          </div>
        </section>


        <section className="panel">
          <h2>Run</h2>
          <div className="block">
            <h3>Reference</h3>
            <select
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            >
              <option value="">Select reference</option>
              <option value="__auto__">Auto-detect (best match)</option>
              {references.map((r) => (
                <option key={r.name} value={r.name}>{r.name}</option>
              ))}
            </select>
            {refLock.references && refLock.references.length > 1 ? (
              <div className="note error">
                Mixed references detected: {refLock.references.join(", ")}. Split into separate runs.
              </div>
            ) : refLock.references && refLock.references.length === 1 ? (
              <div className="note">
                Detected reference from Step 1: {refLock.references[0]}. You can override for a new Step 1 run.
              </div>
            ) : null}
          </div>
          <div className="block">
            <h3>Step 1</h3>
            <button onClick={step1Setup} disabled={!selectedProject}>Setup</button>
            <button onClick={step1Run} disabled={!selectedProject || !reference}>Run</button>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={debugMode}
                onChange={(e) => setDebugMode(e.target.checked)}
              />
              Debug (keep intermediates, skip cleanup)
            </label>
            <div className="step1-status">
              <div className="step1-status-header">
                <span>Samples</span>
                <button onClick={loadStep1Status} disabled={!selectedProject}>Refresh</button>
              </div>
              {step1StatusError ? <div className="note error">{step1StatusError}</div> : null}
              {step1Status.length ? (
                <ul>
                  {step1Status.map((s) => (
                    <li key={s.sample}>
                      <span className={`badge ${s.status}`}>{s.status.replace("_", " ")}</span>
                      <span className="sample-name">{s.sample}</span>
                      <button onClick={() => viewStep1Log(s.sample)} disabled={!s.has_log}>
                        View log
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="note">No Step 1 samples yet.</div>
              )}
              {step1LogSample ? (
                <div className="log-viewer">
                  <div className="log-title">
                    Log: {step1LogSample}
                  </div>
                  <pre>{step1LogLoading ? "Loading..." : (step1LogText || "No log content")}</pre>
                </div>
              ) : null}
            </div>
          </div>
          <div className="block">
            <h3>Step 2</h3>
            <button onClick={step2Setup} disabled={!selectedProject}>Setup</button>
            <button onClick={step2Run} disabled={!selectedProject || !reference || (selected && selected.step2_vcfs === 0) || (refLock.references && refLock.references.length > 1)}>Run</button>
            <div className="note">
              {step2SetupMsg || (selected ? `VCFs ready: ${selected.step2_vcfs || 0}` : "")}
            </div>
          </div>
        </section>

        <section className="panel qc-panel">
          <div className="qc-header">
            <h2>QC Summary (Step 1)</h2>
            <div className="qc-actions">
              <button onClick={loadQC} disabled={!selectedProject || qcLoading}>
                {qcLoading ? "Loading..." : "Refresh"}
              </button>
              <button onClick={downloadQC} disabled={!selectedProject}>Download CSV</button>
              <button onClick={saveExclusions} disabled={!selectedProject}>Save Exclusions</button>
            </div>
          </div>
          <div className="note">
            {qcRows.length ? `Loaded ${qcRows.length} sample(s) for ${selectedProject}.` : "No stats loaded yet."}
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={showFlaggedOnly}
              onChange={(e) => setShowFlaggedOnly(e.target.checked)}
            />
            Show only flagged samples
          </label>
          {qcError ? <div className="note error">{qcError}</div> : null}
          <div className="qc-table">
            <table>
              <thead>
                <tr>
                  <th>Exclude</th>
                  <th>Sample</th>
                  <th>Reference</th>
                  <th>Avg Depth</th>
                  <th>Zero Cov %</th>
                  <th>Dup %</th>
                  <th>R1 Q20</th>
                  <th>R2 Q20</th>
                  <th>Genome Cov</th>
                  <th>Quality SNPs</th>
                </tr>
              </thead>
              <tbody>
                {qcRows
                  .filter((r) => !showFlaggedOnly || isFlagged(r))
                  .map((row) => (
                    <tr key={row._file} className={isFlagged(row) ? "flagged" : ""}>
                      <td>
                        <input
                          type="checkbox"
                          checked={Boolean(excluded[row._sample])}
                          onChange={(e) =>
                            setExcluded({ ...excluded, [row._sample]: e.target.checked })
                          }
                        />
                      </td>
                      <td>{row._sample || row.sample || "-"}</td>
                      <td>{row.Reference || "-"}</td>
                      <td>{row["Average Depth"] || "-"}</td>
                      <td>{row["Percent Ref with Zero Coverage"] || "-"}</td>
                      <td>{row["Duplicate Percent of Mapped Reads"] || "-"}</td>
                      <td>{row["R1 Passing Q20"] || "-"}</td>
                      <td>{row["R2 Passing Q20"] || "-"}</td>
                      <td>{row["Genome with Coverage"] || "-"}</td>
                      <td>{row["Quality SNPs"] || "-"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel results-panel">
          <div className="qc-header">
            <h2>Step 2 Results</h2>
            <div className="qc-actions">
              <button onClick={loadStep2Outputs} disabled={!selectedProject}>Refresh</button>
            </div>
          </div>
          {step2OutputsError ? <div className="note error">{step2OutputsError}</div> : null}
          <div className="results-list">
            {step2Outputs.length ? (
              step2Outputs.map((item) => (
                <div key={item.path} className="results-item">
                  <div className="results-name">{item.label}</div>
                  <div className="results-path">{item.path}</div>
                  <div className="results-actions">
                    <button onClick={() => openOutput(item.path)}>Open</button>
                  </div>
                </div>
              ))
            ) : null}
            {step2Groups.length ? (
              <div className="results-groups">
                {step2Groups.map((group) => (
                  <details key={group.name} className="results-group">
                    <summary>{group.name}</summary>
                    {group.files.map((item) => (
                      <div key={item.path} className="results-item">
                        <div className="results-name">{item.label}</div>
                        <div className="results-path">{item.path}</div>
                        <div className="results-actions">
                          <button onClick={() => openOutput(item.path)}>Open</button>
                        </div>
                      </div>
                    ))}
                  </details>
                ))}
              </div>
            ) : null}
            {!step2Outputs.length && !step2Groups.length ? (
              <div className="note">No Step 2 outputs found yet.</div>
            ) : null}
          </div>
        </section>

        <section className="panel log-panel">
          <h2>Live Logs</h2>
          <div className="log">
            {jobId ? (
              logs.length ? logs.map((l, i) => <div key={i}>{l}</div>) : <div>Waiting for output...</div>
            ) : (
              <div>No job running</div>
            )}
          </div>
          <div className="footer-note">
            Project: {selected?.name || "None"}
          </div>
        </section>
      </main>
    </div>
  );
}
