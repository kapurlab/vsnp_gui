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
    igv_app_path: "",
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
  const [step1FilesCache, setStep1FilesCache] = useState({});
  const [openStep1FilesRow, setOpenStep1FilesRow] = useState("");
  const [step2SetupMsg, setStep2SetupMsg] = useState("");
  const [refLock, setRefLock] = useState({ references: [] });
  const [step2Outputs, setStep2Outputs] = useState([]);
  const [step2Groups, setStep2Groups] = useState([]);
  const [step2OutputsError, setStep2OutputsError] = useState("");
  const [step2Mode, setStep2Mode] = useState("custom");
  const [step2RunId, setStep2RunId] = useState("");
  const [step2BuiltAt, setStep2BuiltAt] = useState("");
  const [step2VcfCount, setStep2VcfCount] = useState(0);
  const [step2AutoRefreshPending, setStep2AutoRefreshPending] = useState(false);
  const [importSourcesText, setImportSourcesText] = useState("");
  const [importReference, setImportReference] = useState("");
  const [importAction, setImportAction] = useState("copy");
  const [importConflict, setImportConflict] = useState("skip");
  const [importStatus, setImportStatus] = useState("");
  const [importIncludeStep1, setImportIncludeStep1] = useState(true);
  const [importAllowMismatch, setImportAllowMismatch] = useState(false);
  const [importMismatchReport, setImportMismatchReport] = useState("");
  const [importPrefixDupes, setImportPrefixDupes] = useState(true);
  const [importDedupe, setImportDedupe] = useState(true);
  const [importFuzzyMatch, setImportFuzzyMatch] = useState(true);
  const [importPreset, setImportPreset] = useState("");
  const [importProjectLock, setImportProjectLock] = useState("");
  const [preflight, setPreflight] = useState(null);
  const [preflightError, setPreflightError] = useState("");
  const [showSetup, setShowSetup] = useState(true);
  const [showRowProjects, setShowRowProjects] = useState(true);
  const [showRowStep1, setShowRowStep1] = useState(true);
  const [showRowStep2, setShowRowStep2] = useState(true);
  const [showRowLogs, setShowRowLogs] = useState(true);

  const canPickPath = typeof window !== "undefined" && window.vsnp?.selectPath;

  const settingsReady = Boolean(
    settings.vsnp3_path && settings.projects_root && (settings.conda_env || settings.conda_env_path)
  );

  const sampleKey = (row) => row?._sample || row?.sample || (row?._file ? row._file.split("/").pop() : "");
  const excludeKey = (row) => row?._file || sampleKey(row);

  function formatPercent(value) {
    if (value === null || value === undefined || value === "") return "-";
    if (typeof value === "number" && !Number.isNaN(value)) {
      return value.toFixed(1);
    }
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return "-";
      if (trimmed.endsWith("%")) {
        const num = Number.parseFloat(trimmed.replace("%", ""));
        if (!Number.isNaN(num)) {
          return `${num.toFixed(1)}%`;
        }
      }
      return value;
    }
    return value;
  }

  const selected = useMemo(
    () => projects.find((p) => p.name === selectedProject),
    [projects, selectedProject]
  );

  async function pickPath(kind, title, currentValue, onPick) {
    if (!window?.vsnp?.selectPath) return;
    const picked = await window.vsnp.selectPath({
      kind,
      title,
      defaultPath: currentValue || undefined
    });
    if (picked) onPick(picked);
  }

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
      igv_app_path: cfg.igv_app_path || "",
      sra_allow_insecure_https: Boolean(cfg.sra?.allow_insecure_https)
    });
    if (selectedProject && !proj.find((p) => p.name === selectedProject)) {
      setSelectedProject("");
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setReference("");
    setImportReference("");
    setRefLock({ references: [] });
    if (!selectedProject) return () => { cancelled = true; };
    (async () => {
      try {
        const lockRes = await fetch(`${API_BASE}/api/projects/${selectedProject}/reference_lock`);
        if (!lockRes.ok) return;
        const lock = await lockRes.json();
        if (cancelled) return;
        setRefLock(lock);
        if (lock.references && lock.references.length === 1) {
          setReference(lock.references[0]);
          setImportReference(lock.references[0]);
        }
      } catch {
        // keep defaults
      }
    })();
    return () => { cancelled = true; };
  }, [selectedProject]);

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
    if (!selectedProject || !settingsReady) return;
    setExcluded({});
    loadQC();
    loadStep1Status();
    loadStep2Outputs();
    setStep2RunId("");
    setStep2BuiltAt("");
    setStep2VcfCount(0);
  }, [selectedProject]);

  useEffect(() => {
    if (step2Mode !== "custom") return;
    if (importReference && reference !== importReference) {
      setReference(importReference);
    }
  }, [step2Mode, importReference, reference]);

  useEffect(() => {
    if (!selectedProject || !settingsReady) return;
    if (jobStatus !== "running") return;
    const id = setInterval(() => {
      loadStep1Status();
    }, 5000);
    return () => clearInterval(id);
  }, [jobStatus, selectedProject]);

  useEffect(() => {
    if (!selectedProject || !settingsReady) return;
    if (jobStatus === "succeeded" || jobStatus === "failed") {
      loadStep1Status();
    }
  }, [jobStatus, selectedProject]);

  useEffect(() => {
    if (!selectedProject || !settingsReady) return;
    if (!step2AutoRefreshPending) return;
    if (jobStatus !== "succeeded") return;
    loadStep2Outputs();
    setStep2AutoRefreshPending(false);
  }, [jobStatus, selectedProject, step2AutoRefreshPending]);

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

  async function refreshProjects(nextSelected = selectedProject) {
    const proj = await fetch(`${API_BASE}/api/projects`).then((r) => r.json());
    setProjects(proj);
    if (nextSelected && proj.find((p) => p.name === nextSelected)) {
      return;
    }
    setSelectedProject(proj[0]?.name || "");
  }

  async function archiveProject(name) {
    if (!window.confirm(`Archive project "${name}" to projects_archive?`)) return;
    await fetch(`${API_BASE}/api/projects/${name}/archive`, { method: "POST" });
    await refreshProjects(selectedProject === name ? "" : selectedProject);
  }

  async function deleteProject(name) {
    if (!window.confirm(`Delete project "${name}" permanently?`)) return;
    await fetch(`${API_BASE}/api/projects/${name}`, { method: "DELETE" });
    await refreshProjects(selectedProject === name ? "" : selectedProject);
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
        igv_app_path: settings.igv_app_path,
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
    if (!selectedProject || !settingsReady) return;
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
          if (!importReference) {
            setImportReference(lock.references[0]);
          }
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
    const countRes = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/vcf_count`);
    if (countRes.ok) {
      const countData = await countRes.json();
      setStep2VcfCount(countData.count || 0);
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

  async function runBootstrap() {
    const res = await fetch(`${API_BASE}/api/bootstrap`, { method: "POST" });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "Bootstrap failed to start");
      return;
    }
    const data = await res.json();
    setJobId(data.job_id);
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

  async function downloadQcXlsx() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_summary.xlsx`);
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedProject}_combined_excelworksheets.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function saveExclusions() {
    if (!selectedProject) return;
    const samples = new Set();
    qcRows.forEach((row) => {
      const key = excludeKey(row);
      if (excluded[key]) {
        const sample = sampleKey(row);
        if (sample) samples.add(sample);
      }
    });
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_exclude`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples: Array.from(samples) })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "Failed to save exclusions");
      return;
    }
    window.alert("Exclusions saved");
  }

  async function linkLocal() {
    if (!selectedProject || !settingsReady || !localPath) return;
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
    if (!selectedProject || !settingsReady || !fileList?.length) return;
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
    if (!selectedProject || !settingsReady) return;
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

  async function importVcfs() {
    if (!selectedProject || !settingsReady) return;
    const sources = parseAccessions(importSourcesText);
    if (!sources.length && !importIncludeStep1) {
      setImportStatus("Provide at least one source path or include Step 1.");
      return;
    }
    if (!importReference) {
      setImportStatus("Reference is required");
      return;
    }
    setImportStatus("");
    const payload = {
      source_paths: sources,
      include_step1: importIncludeStep1,
      reference: importReference,
      action: importAction,
      on_conflict: importConflict,
      allow_mismatch: importAllowMismatch,
      prefix_duplicates: importPrefixDupes,
      dedupe: importDedupe,
      allow_fuzzy_match: importFuzzyMatch
    };
    let res = await fetch(`${API_BASE}/api/projects/${selectedProject}/import-vcfs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const data = await res.json();
      if (data.detail && data.detail.startsWith("Large import")) {
        const ok = window.confirm(`${data.detail} Continue?`);
        if (!ok) return;
        payload.confirm_large = true;
        res = await fetch(`${API_BASE}/api/projects/${selectedProject}/import-vcfs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          const retry = await res.json();
          setImportStatus(`Import failed: ${retry.detail || res.statusText}`);
          return;
        }
      } else {
        setImportStatus(`Import failed: ${data.detail || res.statusText}`);
        return;
      }
      return;
    }
    const data = await res.json();
    setImportMismatchReport(data.mismatch_report || "");
    if (typeof data.total_found === "number") {
      setStep2VcfCount(data.total_found);
    }
    const parts = [
      `Imported ${data.imported}`,
      data.renamed ? `Renamed ${data.renamed}` : null,
      data.skipped ? `Skipped ${data.skipped}` : null,
      data.mismatched ? `Mismatched ${data.mismatched}` : null,
      data.detected_reference ? `Ref: ${data.detected_reference}` : null
    ].filter(Boolean);
    setImportStatus(parts.join(" | "));
    setImportProjectLock(selectedProject);
    setStep2BuiltAt(new Date().toISOString());
    await refreshProjects(selectedProject);
  }

  async function step1Setup() {
    if (!selectedProject || !settingsReady) return;
    await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/setup`, { method: "POST" });
    await loadAll();
    await loadStep1Status();
  }

  async function step1Run() {
    if (!selectedProject || !settingsReady || !reference) return;
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
    if (!selectedProject || !settingsReady) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/setup`, { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      const total = data.total ?? data.linked;
      setStep2SetupMsg(`VCFs ready for Step 2: ${total} (linked ${data.linked})`);
    }
    await loadAll();
  }

  async function step2Clear() {
    if (!selectedProject || !settingsReady) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/clear`, { method: "POST" });
    if (res.ok) {
      setStep2SetupMsg("VCF set cleared");
      setStep2BuiltAt("");
      setStep2VcfCount(0);
      setImportStatus("");
      setImportMismatchReport("");
      setStep2Outputs([]);
      setStep2Groups([]);
      setStep2OutputsError("");
      await loadAll();
    }
  }

  async function step2Run() {
    if (!selectedProject || !settingsReady) return;
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
    setStep2Outputs([]);
    setStep2Groups([]);
    setStep2OutputsError("");
    setStep2RunId(new Date().toISOString());
    setStep2AutoRefreshPending(true);
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

  async function getStep1Files(sample) {
    if (!selectedProject) return null;
    if (step1FilesCache[sample]) return step1FilesCache[sample];
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/files?sample=${encodeURIComponent(sample)}`);
    if (!res.ok) return null;
    const data = await res.json();
    setStep1FilesCache((prev) => ({ ...prev, [sample]: data }));
    return data;
  }

  async function openStep1File(sample, type) {
    const data = await getStep1Files(sample);
    if (!data) return;
    const path = data[type];
    if (path) {
      await openOutput(path);
    }
  }

  async function openStep1Igv(sample) {
    if (!selectedProject) return;
    const data = await getStep1Files(sample);
    if (!data || !data.sample_dir) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/igv_session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: data.sample_dir })
    });
    if (res.ok) {
      const payload = await res.json();
      if (!payload.igv_commands_sent) {
        const detail = payload.igv_error ? ` (${payload.igv_error})` : "";
        window.alert(`IGV command server did not accept commands${detail}.`);
      }
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-brand">
          <img className="app-logo" src="/vSNP_icon_align_256.png" alt="vSNP alignment icon" />
          <div>
            <h1>vSNP GUI</h1>
            <p>Local workflows for vSNP3 implementation</p>
          </div>
        </div>
        <div className="status-pill">
          <span className="dot" data-state={jobStatus}></span>
          <span>{jobStatus}</span>
        </div>
      </header>

      <main className="layout">
        <datalist id="reference-options">
          {references.map((ref) => (
            <option key={ref.name} value={ref.name} />
          ))}
        </datalist>
        {!settingsReady ? (
          <div className="panel alert-banner">
            <strong>Setup required:</strong> Set vSNP3 path, projects root, and conda env in Settings,
            then click Save + Preflight.
          </div>
        ) : null}
        <section className="status-strip">
          <div className="status-item">
            <span className="status-label">Project</span>
            <span className="status-value">{selected?.name || "None"}</span>
          </div>
          <div className="status-item">
            <span className="status-label">FASTQ</span>
            <span className="status-value">{selected?.fastq_count ?? 0}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Step 1 Samples</span>
            <span className="status-value">{selected?.step1_samples ?? 0}</span>
          </div>
          <div className="status-item">
            <span className="status-label">VCFs Ready</span>
            <span className="status-value">{selected?.step2_vcfs ?? 0}</span>
          </div>
          <div className="status-item">
            <span className="status-label">Job Status</span>
            <span className="status-value">{jobStatus}</span>
          </div>
        </section>

        <div className="row-header">
          <h2>Setup</h2>
          <button className="ghost" onClick={() => setShowSetup(!showSetup)}>
            {showSetup ? "Hide" : "Show"}
          </button>
        </div>

        {showSetup ? (
          <div className="row-grid">
            <section className="panel">
            <div className="panel-header">
              <h2>Settings</h2>
              <div className="panel-actions">
                <button className="ghost action" onClick={runBootstrap} title="Run setup assistant">Setup</button>
                <button className="ghost action" onClick={runPreflight} title="Run preflight">Preflight</button>
                <button className="ghost action-primary" onClick={saveSettings} title="Save settings">Save</button>
              </div>
            </div>
            <div className="settings-grid">
                <div className="settings-row">
                  <label className="label">vSNP3 path</label>
                  <input
                    placeholder="/Users/vivekkapur/vsnp3"
                    value={settings.vsnp3_path}
                    onChange={(e) => setSettings({ ...settings, vsnp3_path: e.target.value })}
                  />
                  {canPickPath ? (
                    <button
                      className="ghost action"
                      onClick={() =>
                        pickPath(
                          "directory",
                          "Select vSNP3 folder",
                          settings.vsnp3_path,
                          (value) => setSettings({ ...settings, vsnp3_path: value })
                        )
                      }
                    >
                      Choose
                    </button>
                  ) : null}
                </div>
                <div className="settings-row">
                  <label className="label">Projects root</label>
                  <input
                    placeholder="/Users/vivekkapur/vsnp3/projects"
                    value={settings.projects_root}
                    onChange={(e) => setSettings({ ...settings, projects_root: e.target.value })}
                  />
                  {canPickPath ? (
                    <button
                      className="ghost action"
                      onClick={() =>
                        pickPath(
                          "directory",
                          "Select projects root",
                          settings.projects_root,
                          (value) => setSettings({ ...settings, projects_root: value })
                        )
                      }
                    >
                      Choose
                    </button>
                  ) : null}
                </div>
                <div className="settings-row">
                  <label className="label">Conda env for vSNP3</label>
                  <input
                    placeholder="vsnp3"
                    value={settings.conda_env}
                    onChange={(e) => setSettings({ ...settings, conda_env: e.target.value })}
                  />
                </div>
                <div className="settings-row">
                  <label className="label">Conda executable (optional)</label>
                  <input
                    placeholder="/Users/vivekkapur/anaconda3/bin/conda"
                    value={settings.conda_exe}
                    onChange={(e) => setSettings({ ...settings, conda_exe: e.target.value })}
                  />
                  {canPickPath ? (
                    <button
                      className="ghost action"
                      onClick={() =>
                        pickPath(
                          "file",
                          "Select conda executable",
                          settings.conda_exe,
                          (value) => setSettings({ ...settings, conda_exe: value })
                        )
                      }
                    >
                      Choose
                    </button>
                  ) : null}
                </div>
                <div className="settings-row">
                  <label className="label">Conda env path (optional)</label>
                  <input
                    placeholder="/Users/vivekkapur/anaconda3/envs/vivek"
                    value={settings.conda_env_path}
                    onChange={(e) => setSettings({ ...settings, conda_env_path: e.target.value })}
                  />
                  {canPickPath ? (
                    <button
                      className="ghost action"
                      onClick={() =>
                        pickPath(
                          "directory",
                          "Select conda env folder",
                          settings.conda_env_path,
                          (value) => setSettings({ ...settings, conda_env_path: value })
                        )
                      }
                    >
                      Choose
                    </button>
                  ) : null}
                </div>
                <div className="settings-row">
                  <label className="label">IGV app (optional)</label>
                  <input
                    placeholder="/Applications/IGV_2.14.0.app"
                    value={settings.igv_app_path}
                    onChange={(e) => setSettings({ ...settings, igv_app_path: e.target.value })}
                  />
                  {canPickPath ? (
                    <button
                      className="ghost action"
                      onClick={() =>
                        pickPath(
                          "file",
                          "Select IGV app",
                          settings.igv_app_path,
                          (value) => setSettings({ ...settings, igv_app_path: value })
                        )
                      }
                    >
                      Choose
                    </button>
                  ) : null}
                </div>
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
            </section>

            <section className="panel checklist">
            <div className="panel-header">
              <h2>Start-Up Checklist</h2>
              <div className="panel-actions">
                <button className="ghost" onClick={runPreflight} title="Run preflight">Preflight</button>
              </div>
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
          </div>
        ) : null}

        <div className="row-header">
          <h2>Projects & Inputs</h2>
          <button className="ghost" onClick={() => setShowRowProjects(!showRowProjects)}>
            {showRowProjects ? "Hide" : "Show"}
          </button>
        </div>

        {showRowProjects ? (
          <div className="row-grid">
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
                <div
                  key={p.name}
                  className={`list-item ${p.name === selectedProject ? "active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedProject(p.name)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") setSelectedProject(p.name);
                  }}
                >
                  <div className="list-details">
                    <div className="list-title">{p.name}</div>
                    <div className="list-meta">
                      FASTQ: {p.fastq_count} | Step1: {p.step1_samples} | VCF: {p.step1_vcfs}
                    </div>
                  </div>
                  <div className="list-actions">
                    <button
                      className="ghost-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        archiveProject(p.name);
                      }}
                    >
                      Archive
                    </button>
                    <button
                      className="ghost-btn danger"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteProject(p.name);
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Inputs</h2>
            <div className="input-columns">
              <div className="input-column">
                <h3>Bring Your Own FASTQ</h3>
                <input
                  placeholder="/path/to/fastq_dir"
                  value={localPath}
                  onChange={(e) => setLocalPath(e.target.value)}
                />
                <button onClick={linkLocal} disabled={!selectedProject || !settingsReady}>Link Local Files</button>
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
                  {uploadStatus ? <div className="note">{uploadStatus}</div> : null}
                </div>
              </div>
              <div className="input-column">
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
                <button onClick={sraDownload} disabled={!selectedProject || !settingsReady}>Download</button>
              </div>
            </div>
          </section>
        </div>
        ) : null}

        <div className="row-header">
          <h2>Step 1</h2>
          <button className="ghost" onClick={() => setShowRowStep1(!showRowStep1)}>
            {showRowStep1 ? "Hide" : "Show"}
          </button>
        </div>

        {showRowStep1 ? (
          <div className="row-grid row-grid-split">
          <section className="panel run-panel">
            <h2>Step 1</h2>
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
                <div className="inline-help">
                  <span className="muted">Reference detected</span>
                  <span
                    className="help-icon"
                    data-tooltip={`Detected reference from Step 1: ${refLock.references[0]}. You can override for a new Step 1 run.`}
                  >
                    ?
                  </span>
                </div>
              ) : null}
            </div>
            <div className="block">
              <button onClick={step1Setup} disabled={!selectedProject || !settingsReady}>Setup</button>
              <button onClick={step1Run} disabled={!selectedProject || !settingsReady || !reference}>Run</button>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={debugMode}
                  onChange={(e) => setDebugMode(e.target.checked)}
                />
                Debug (keep intermediates, skip cleanup)
              </label>
            </div>
            <div className="step1-status">
              <div className="step1-status-header">
                <span>Samples</span>
                <button onClick={loadStep1Status} disabled={!selectedProject}>Refresh</button>
              </div>
              {step1StatusError ? <div className="note error">{step1StatusError}</div> : null}
              {step1Status.length ? (
                <ul className="sample-list">
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
              {step1Status.length > 6 ? <div className="scroll-note">Scroll for more samples.</div> : null}
              {step1LogSample ? (
                <div className="log-viewer">
                  <div className="log-title">
                    Log: {step1LogSample}
                  </div>
                  <pre>{step1LogLoading ? "Loading..." : (step1LogText || "No log content")}</pre>
                </div>
              ) : null}
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
                <button onClick={downloadQcXlsx} disabled={!selectedProject}>Download XLSX</button>
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
            <div className="qc-table scrollable">
              <table>
                <thead>
                  <tr>
                    <th>Exclude</th>
                    <th>Sample</th>
                    <th>Files</th>
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
                            checked={Boolean(excluded[excludeKey(row)])}
                            onChange={(e) =>
                              setExcluded({ ...excluded, [excludeKey(row)]: e.target.checked })
                            }
                          />
                        </td>
                        <td>{row._sample || row.sample || "-"}</td>
                        <td>
                          <details
                            className="inline-details"
                            open={openStep1FilesRow === sampleKey(row)}
                          >
                            <summary
                              onClick={(e) => {
                                e.preventDefault();
                                const key = sampleKey(row);
                                setOpenStep1FilesRow((prev) => (prev === key ? "" : key));
                              }}
                            >
                              Files
                            </summary>
                            <div className="inline-files">
                              <button
                                onClick={() => openStep1File(sampleKey(row), "sample_dir")}
                                disabled={!sampleKey(row)}
                              >
                                Open Folder
                              </button>
                              <button
                                onClick={() => openStep1Igv(sampleKey(row))}
                                disabled={!sampleKey(row)}
                              >
                                IGV
                              </button>
                              {row._file ? (
                                <button onClick={() => openOutput(row._file)}>Stats</button>
                              ) : null}
                            </div>
                          </details>
                        </td>
                        <td>{row.Reference || "-"}</td>
                        <td>{row["Average Depth"] || "-"}</td>
                        <td>{formatPercent(row["Percent Ref with Zero Coverage"])}</td>
                        <td>{formatPercent(row["Duplicate Percent of Mapped Reads"])}</td>
                        <td>{formatPercent(row["R1 Passing Q20"])}</td>
                        <td>{formatPercent(row["R2 Passing Q20"])}</td>
                        <td>{formatPercent(row["Genome with Coverage"])}</td>
                        <td>{row["Quality SNPs"] || "-"}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            {qcRows.length > 8 ? <div className="scroll-note">Scroll for more samples.</div> : null}
          </section>
        </div>
        ) : null}

        <div className="row-header">
          <h2>Step 2</h2>
          <button className="ghost" onClick={() => setShowRowStep2(!showRowStep2)}>
            {showRowStep2 ? "Hide" : "Show"}
          </button>
        </div>

        {showRowStep2 ? (
          <div className="row-grid row-grid-split">
          <section className="panel run-panel">
            <h2>Step 2</h2>
            <div className="block">
              <div className="mode-toggle">
                <button
                  className={step2Mode === "custom" ? "active" : ""}
                  onClick={() => setStep2Mode("custom")}
                >
                  Use custom VCF set
                </button>
                <button
                  className={step2Mode === "step1" ? "active" : ""}
                  onClick={() => setStep2Mode("step1")}
                >
                  Use Step 1 only
                </button>
              </div>
            </div>

            {step2Mode === "custom" ? (
              <div className="block">
                <h3>
                  VCF Sources (Step 2)
                  <span
                    className="help-icon"
                    data-tooltip="Paste one or more folders (one per line). All subfolders are searched for *_zc.vcf and *_zc.vcf.gz. Use the same reference across all sources."
                  >
                    ?
                  </span>
                </h3>
                <textarea
                  placeholder="/path/to/step2/vcf_database (one per line, searched recursively)"
                  value={importSourcesText}
                  onChange={(e) => setImportSourcesText(e.target.value)}
                  rows={4}
                />
                <div className="row">
                  <select
                    value={importPreset}
                    onChange={(e) => {
                      const value = e.target.value;
                      setImportPreset(value);
                      const vsnpRoot = settings.vsnp3_path || "";
                      const guiRoot = config?.gui_root || "";
                      if (value === "mtbc0") {
                        if (!vsnpRoot) {
                          setImportStatus("Set vSNP3 path in Settings first.");
                          return;
                        }
                        setImportSourcesText([
                          `${vsnpRoot}/VCF_REFS/mtbc_representative`,
                          `${vsnpRoot}/VCF_REFS/minimum_tree`,
                          `${vsnpRoot}/VCF_REFS/faked_from_assembly`
                        ].join("\n"));
                        setImportReference("mtbc0_v1.1");
                        setImportIncludeStep1(true);
                        setImportAction("copy");
                        setImportConflict("rename");
                        setImportPrefixDupes(true);
                        setImportDedupe(true);
                        setImportFuzzyMatch(true);
                      } else if (value === "lite") {
                        if (!guiRoot) {
                          setImportStatus("GUI root not available yet. Try again after reload.");
                          return;
                        }
                        setImportSourcesText([
                          `${guiRoot}/sample_data/vcf_lite`
                        ].join("\n"));
                        setImportReference("mtbc0_v1.1");
                        setImportIncludeStep1(false);
                        setImportAction("copy");
                        setImportConflict("rename");
                        setImportPrefixDupes(true);
                        setImportDedupe(true);
                        setImportFuzzyMatch(true);
                      }
                    }}
                  >
                    <option value="">Preset...</option>
                    <option value="mtbc0">MTBC0 + VCF_REFS</option>
                    <option value="lite">VCF Lite Pack (repo)</option>
                  </select>
                </div>
                {canPickPath ? (
                  <button
                    className="ghost action"
                    onClick={() =>
                      pickPath(
                        "directory",
                        "Select Step 2 VCF database",
                        "",
                        (value) =>
                          setImportSourcesText((prev) => (prev ? `${prev}\n${value}` : value))
                      )
                    }
                  >
                    Add Folder
                  </button>
                ) : null}
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={importIncludeStep1}
                    onChange={(e) => setImportIncludeStep1(e.target.checked)}
                  />
                  Include current project Step 1 ZC VCFs
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={importAllowMismatch}
                    onChange={(e) => setImportAllowMismatch(e.target.checked)}
                  />
                  Allow reference mismatches (not recommended)
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={importPrefixDupes}
                    onChange={(e) => setImportPrefixDupes(e.target.checked)}
                  />
                  Prefix duplicates with source folder name
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={importDedupe}
                    onChange={(e) => setImportDedupe(e.target.checked)}
                  />
                  Deduplicate identical sample IDs (keep newest)
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={importFuzzyMatch}
                    onChange={(e) => setImportFuzzyMatch(e.target.checked)}
                  />
                  Allow fuzzy reference match (mtbc0_v1 ≈ mtbc0_v1.1) (TEMP)
                </label>
                <select
                  value={importReference}
                  onChange={(e) => setImportReference(e.target.value)}
                >
                  <option value="">Select reference</option>
                  {references.map((r) => (
                    <option key={r.name} value={r.name}>{r.name}</option>
                  ))}
                </select>
                <div className="row">
                  <select value={importAction} onChange={(e) => setImportAction(e.target.value)}>
                    <option value="copy">Copy files</option>
                    <option value="link">Link files</option>
                  </select>
                  <select value={importConflict} onChange={(e) => setImportConflict(e.target.value)}>
                    <option value="skip">Skip conflicts</option>
                    <option value="rename">Rename conflicts</option>
                    <option value="overwrite">Overwrite conflicts</option>
                  </select>
                </div>
                <div className="selection-box">
                  <div>
                    <strong>Selections:</strong>
                  </div>
                  <div>Sources: {parseAccessions(importSourcesText).length || 0}</div>
                  <div>Include Step 1: {importIncludeStep1 ? "Yes" : "No"}</div>
                  <div>Reference: {importReference || "None"}</div>
                  <div>Action: {importAction} | Conflicts: {importConflict}</div>
                  <button
                    className="ghost action"
                    onClick={() => {
                      setImportSourcesText("");
                      setImportMismatchReport("");
                      setImportStatus("");
                    }}
                  >
                    Clear sources
                  </button>
                </div>
                <div className="row">
                  <button onClick={importVcfs} disabled={!selectedProject || !settingsReady}>Build VCF set</button>
                  <button className="ghost action" onClick={step2Clear} disabled={!selectedProject || !settingsReady}>Clear VCF set</button>
                  <button
                    className="ghost action"
                    onClick={() => openOutput(`${settings.projects_root}/${selectedProject}/step2/vcf_source`)}
                    disabled={!selectedProject}
                  >
                    Open vcf_source
                  </button>
                </div>
                <div className="note">
                  VCFs in set: {step2VcfCount}
                  {step2BuiltAt ? ` • Built at: ${step2BuiltAt}` : ""}
                </div>
                {importMismatchReport ? (
                  <button
                    className="ghost action"
                    onClick={() => openOutput(importMismatchReport)}
                  >
                    Open mismatch report
                  </button>
                ) : null}
                {importStatus ? <div className="note">{importStatus}</div> : null}
                {importProjectLock && selectedProject !== importProjectLock ? (
                  <div className="note error">
                    VCF set built for {importProjectLock}. Switch back to run Step 2 there.
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="block">
                <div className="note">
                  Step 2 will use only this project's Step 1 ZC VCFs.
                </div>
              </div>
            )}

            <div className="block">
              {step2Mode === "step1" ? (
                <button onClick={step2Setup} disabled={!selectedProject || !settingsReady}>Setup</button>
              ) : null}
                <button
                  onClick={step2Run}
                  disabled={
                    !selectedProject ||
                    !settingsReady ||
                    !reference ||
                    (step2Mode === "custom"
                      ? step2VcfCount === 0
                      : selected && selected.step2_vcfs === 0) ||
                    (refLock.references && refLock.references.length > 1)
                  }
                >
                Run
              </button>
              <div className="note">
                {step2SetupMsg || (selected ? `VCFs ready: ${selected.step2_vcfs || 0}` : "")}
              </div>
              {selected ? (
                <div className="note">
                  Outputs will be written to: {settings.projects_root}/{selected.name}/step2
                </div>
              ) : null}
            </div>
          </section>

          <section className="panel results-panel">
            <div className="qc-header">
              <h2>Step 2 Results</h2>
              <div className="qc-actions">
                <button onClick={loadStep2Outputs} disabled={!selectedProject}>Refresh</button>
              </div>
            </div>
            {step2RunId ? <div className="note">Run ID: {step2RunId}</div> : null}
            {step2OutputsError ? <div className="note error">{step2OutputsError}</div> : null}
            {(() => {
              const groupCount = step2Groups.reduce((sum, g) => sum + (g.files?.length || 0), 0);
              const totalCount = step2Outputs.length + groupCount;
              return (
                <>
                  <div className="results-list">
                  {step2Outputs.length ? (
                    step2Outputs.map((item) => (
                      <div key={item.path} className="results-item">
                        <div className="results-main">
                      <div className="results-name">{item.label}</div>
                      <div className="results-path">{item.path}</div>
                    </div>
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
                          <div className="results-main">
                            <div className="results-name">{item.label}</div>
                            <div className="results-path">{item.path}</div>
                          </div>
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
                  {totalCount > 8 ? <div className="scroll-hint">Scroll for more results</div> : null}
                </>
              );
            })()}
          </section>
        </div>
        ) : null}

        <div className="row-header">
          <h2>Logs</h2>
          <button className="ghost" onClick={() => setShowRowLogs(!showRowLogs)}>
            {showRowLogs ? "Hide" : "Show"}
          </button>
        </div>

        {showRowLogs ? (
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
        ) : null}
      </main>
    </div>
  );
}
