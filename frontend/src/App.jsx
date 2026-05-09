import React, { useEffect, useMemo, useRef, useState } from "react";
import igv from "igv";
import { APP_VERSION } from "./version";

const API_BASE = import.meta.env.VITE_API_URL || ".";

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
    figtree_app_path: "",
    bcftools_path: "",
    step1_max_parallel: 3,
    sra_allow_insecure_https: false
  });
  const [sraText, setSraText] = useState("");
  const [sraFolder, setSraFolder] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [reference, setReference] = useState("");
  const [debugMode, setDebugMode] = useState(false);
  const [assembleUnmap, setAssembleUnmap] = useState(false);
  const [nanoporeMode, setNanoporeMode] = useState(false);
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
  const [folderModal, setFolderModal] = useState({ open: false, project: "", sample: "", files: [], sampleDir: "", loading: false, error: "" });
  const [igvPanel, setIgvPanel] = useState({ open: false, project: "", referenceFastaPath: "", referenceFaiPath: "", tracks: [], status: "", height: 45, fullscreen: false });
  const [igvPopoutOpen, setIgvPopoutOpen] = useState(false);
  const igvBrowserRef = useRef(null);
  const igvContainerRef = useRef(null);
  const igvPopoutRef = useRef(null);
  const [step1ResultsTab, setStep1ResultsTab] = useState("results");
  const [posthocFolders, setPosthocFolders] = useState([]);
  const [posthocRows, setPosthocRows] = useState([]);
  const [posthocLoading, setPosthocLoading] = useState(false);
  const [posthocError, setPosthocError] = useState("");
  const [posthocSampleInput, setPosthocSampleInput] = useState("");
  const [posthocSampleList, setPosthocSampleList] = useState("");
  const [posthocResolveMsg, setPosthocResolveMsg] = useState("");
  const [step1Edits, setStep1Edits] = useState({});
  const [step1EditedCount, setStep1EditedCount] = useState(0);
  const [editVcfOpen, setEditVcfOpen] = useState(false);
  const [editVcfSample, setEditVcfSample] = useState("");
  const [editVcfProject, setEditVcfProject] = useState("");
  const [editVcfLocus, setEditVcfLocus] = useState("");
  const [editVcfAlt, setEditVcfAlt] = useState("");
  const [editVcfNote, setEditVcfNote] = useState("");
  const [editVcfReason, setEditVcfReason] = useState("");
  const [editVcfCurrent, setEditVcfCurrent] = useState(null);
  const [step2SetupMsg, setStep2SetupMsg] = useState("");
  const [refLock, setRefLock] = useState({ references: [] });
  const [step2Outputs, setStep2Outputs] = useState([]);
  const [step2Groups, setStep2Groups] = useState([]);
  const [step2OutputsError, setStep2OutputsError] = useState("");
  const [step2EditedCount, setStep2EditedCount] = useState(0);
  const [step2Mode, setStep2Mode] = useState("custom");
  // Step 2 run options
  const [s2NoFilters, setS2NoFilters] = useState(false);
  const [s2QualThreshold, setS2QualThreshold] = useState(150);
  const [s2NThreshold, setS2NThreshold] = useState(50);
  const [s2MqThreshold, setS2MqThreshold] = useState(56);
  const [s2AllVcf, setS2AllVcf] = useState(true);
  const [s2FindNewFilters, setS2FindNewFilters] = useState(false);
  const [s2HashGroups, setS2HashGroups] = useState(false);
  const [s2ShowGroups, setS2ShowGroups] = useState(false);
  const [s2HtmlTree, setS2HtmlTree] = useState(false);
  const [s2LabelStyle, setS2LabelStyle] = useState("short");
  const [s2Dp, setS2Dp] = useState(false);
  const [s2DensityThreshold, setS2DensityThreshold] = useState("");
  const [s2DensityWindow, setS2DensityWindow] = useState("");
  const [s2Bootstrap, setS2Bootstrap] = useState("");
  const [step2RunId, setStep2RunId] = useState("");
  const [step2BuiltAt, setStep2BuiltAt] = useState("");
  const [step2VcfCount, setStep2VcfCount] = useState(0);
  const [step1AutoRefreshPending, setStep1AutoRefreshPending] = useState(false);
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
  const [vcfDbFolders, setVcfDbFolders] = useState([]);
  const [manualVcfFolderPath, setManualVcfFolderPath] = useState("");
  const [preflight, setPreflight] = useState(null);
  const [preflightError, setPreflightError] = useState("");
  const [pathValidation, setPathValidation] = useState({});
  const [showSetup, setShowSetup] = useState(true);
  const [showRowProjects, setShowRowProjects] = useState(true);
  const [showRowStep1, setShowRowStep1] = useState(true);
  const [showRowStep2, setShowRowStep2] = useState(true);
  const [showRowLogs, setShowRowLogs] = useState(true);
  const [showRowRefEditor, setShowRowRefEditor] = useState(true);
  // Item 2: Reference path management
  const [refPaths, setRefPaths] = useState([]);
  const [showRefPaths, setShowRefPaths] = useState(false);
  const [refPathInput, setRefPathInput] = useState("");
  // Item 3: Genome download
  const [showGenomeDownload, setShowGenomeDownload] = useState(false);
  const [genomeAccession, setGenomeAccession] = useState("");
  const [genomeOutputDir, setGenomeOutputDir] = useState("");
  const [genomeDownloadStatus, setGenomeDownloadStatus] = useState("");
  const [genomeJobId, setGenomeJobId] = useState("");
  // Item 5: SRA download feedback
  const [sraJobId, setSraJobId] = useState("");
  const [sraStatus, setSraStatus] = useState("");
  // Item 6: Reference Editor
  const [refEditorRef, setRefEditorRef] = useState("");
  const [refEditorFiles, setRefEditorFiles] = useState([]);
  const [refEditorPath, setRefEditorPath] = useState("");

  const canPickPath = typeof window !== "undefined" && window.vsnp?.selectPath;

  const settingsReady = Boolean(
    settings.vsnp3_path && settings.projects_root
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

  const posthocFilteredRows = useMemo(() => {
    if (!posthocRows.length) return [];
    if (!reference) return posthocRows;
    const refNorm = normalizeReferenceName(reference);
    return posthocRows.filter((r) => normalizeReferenceName(r.Reference) === refNorm);
  }, [posthocRows, reference]);

  async function pickPath(kind, title, currentValue, onPick) {
    if (!window?.vsnp?.selectPath) return;
    const picked = await window.vsnp.selectPath({
      kind,
      title,
      defaultPath: currentValue || undefined
    });
    if (picked) onPick(picked);
  }

  async function pickMultiPaths(kind, title, onPick) {
    if (!window?.vsnp?.selectPath) return;
    const picked = await window.vsnp.selectPath({
      kind,
      title,
      multiSelect: true
    });
    if (picked && Array.isArray(picked) && picked.length) onPick(picked);
  }

  // Item 2: Reference path management
  async function loadRefPaths() {
    const res = await fetch(`${API_BASE}/api/references/paths`);
    if (res.ok) {
      const data = await res.json();
      setRefPaths(data.paths || []);
    }
  }

  async function addRefPath(dirPath) {
    const res = await fetch(`${API_BASE}/api/references/paths`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: dirPath })
    });
    if (res.ok) {
      const data = await res.json();
      setRefPaths(data.paths || []);
      setReferences(data.references || []);
    }
  }

  async function removeRefPath(dirPath) {
    const res = await fetch(`${API_BASE}/api/references/paths`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: dirPath })
    });
    if (res.ok) {
      const data = await res.json();
      setRefPaths(data.paths || []);
      setReferences(data.references || []);
    }
  }

  // VCF DB folder management
  async function loadVcfDbFolders() {
    const res = await fetch(`${API_BASE}/api/vcf-db-folders`);
    if (res.ok) {
      const data = await res.json();
      setVcfDbFolders(data || []);
    }
  }

  async function addVcfDbFolder(path) {
    try {
      const res = await fetch(`${API_BASE}/api/vcf-db-folders`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add", path })
      });
      if (res.ok) {
        const data = await res.json();
        setVcfDbFolders(data || []);
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Failed to add VCF folder: ${err.detail || res.statusText}`);
      }
    } catch (e) {
      alert(`Cannot reach backend to add VCF folder. Is the backend running?\n\n${e.message}`);
    }
  }

  async function removeVcfDbFolder(index) {
    const res = await fetch(`${API_BASE}/api/vcf-db-folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "remove", index })
    });
    if (res.ok) {
      const data = await res.json();
      setVcfDbFolders(data || []);
    }
  }

  async function toggleVcfDbFolder(index) {
    const res = await fetch(`${API_BASE}/api/vcf-db-folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "toggle", index })
    });
    if (res.ok) {
      const data = await res.json();
      setVcfDbFolders(data || []);
    }
  }

  // Item 3: Genome download
  async function downloadGenome() {
    if (!genomeAccession.trim() || !genomeOutputDir.trim()) {
      setGenomeDownloadStatus("Accession and output directory are required.");
      return;
    }
    const finalDir = genomeOutputDir.trim();
    setGenomeDownloadStatus("Starting download...");
    const res = await fetch(`${API_BASE}/api/references/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accession: genomeAccession.trim(), output_dir: finalDir })
    });
    if (!res.ok) {
      const data = await res.json();
      setGenomeDownloadStatus(`Error: ${data.detail || "Download failed"}`);
      return;
    }
    const data = await res.json();
    setGenomeJobId(data.job_id);
    setGenomeDownloadStatus(`Downloading ${data.accession}...`);
    setJobId(data.job_id);
  }

  // Item 6: Reference Editor
  async function loadRefEditorFiles(refName) {
    if (!refName) {
      setRefEditorFiles([]);
      setRefEditorPath("");
      return;
    }
    const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(refName)}/files`);
    if (res.ok) {
      const data = await res.json();
      setRefEditorFiles(data.files || []);
      setRefEditorPath(data.ref_path || "");
    }
  }

  async function openRefFile(refName, filename) {
    await fetch(`${API_BASE}/api/references/${encodeURIComponent(refName)}/open-file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename })
    });
  }

  async function createRefFile(refName, fileType) {
    const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(refName)}/create-file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_type: fileType })
    });
    if (res.ok) {
      await loadRefEditorFiles(refName);
    } else {
      const data = await res.json();
      window.alert(data.detail || "Failed to create file");
    }
  }

  async function loadAll() {
    const [cfg, proj, refs, dbFolders] = await Promise.all([
      fetch(`${API_BASE}/api/config`).then((r) => r.json()),
      fetch(`${API_BASE}/api/projects`).then((r) => r.json()),
      fetch(`${API_BASE}/api/references`).then((r) => r.json()),
      fetch(`${API_BASE}/api/vcf-db-folders`).then((r) => r.json()).catch(() => [])
    ]);
    setConfig(cfg);
    setProjects(proj);
    setReferences(refs);
    setVcfDbFolders(dbFolders || []);
    setSettings({
      vsnp3_path: cfg.vsnp3_path || "",
      projects_root: cfg.projects_root || "",
      figtree_app_path: cfg.figtree_app_path || "",
      bcftools_path: cfg.bcftools_path || "",
      step1_max_parallel: cfg.step1_max_parallel ?? 3,
      sra_allow_insecure_https: Boolean(cfg.sra?.allow_insecure_https)
    });
    if (cfg._validation) {
      setPathValidation(cfg._validation);
    }
    if (selectedProject && !proj.find((p) => p.name === selectedProject)) {
      setSelectedProject("");
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  useEffect(() => {
    document.title = `vSNP GUI ${APP_VERSION}`;
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
    if (!settings.vsnp3_path) return;
    runPreflight();
  }, [settings.vsnp3_path]);

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
        // Update SRA status if this was an SRA job
        if (sraJobId && jobId === sraJobId) {
          if (status === "succeeded") {
            setSraStatus("Download complete");
            loadAll();
          } else {
            setSraStatus(`Download ${status}`);
          }
        }
        // Update genome download status if this was a genome download job
        if (genomeJobId && jobId === genomeJobId) {
          if (status === "succeeded") {
            setGenomeDownloadStatus("Download complete. Refreshing references...");
            loadAll().then(() => setGenomeDownloadStatus("Download complete."));
          } else {
            setGenomeDownloadStatus(`Download ${status}`);
          }
        }
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
    if (!step1AutoRefreshPending) return;
    if (jobStatus !== "succeeded" && jobStatus !== "failed") return;
    loadQC();
    setStep1AutoRefreshPending(false);
  }, [jobStatus, selectedProject, step1AutoRefreshPending]);

  useEffect(() => {
    if (!selectedProject || !settingsReady) return;
    if (!step2AutoRefreshPending) return;
    if (jobStatus !== "succeeded" && jobStatus !== "failed") return;
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
        figtree_app_path: settings.figtree_app_path,
        bcftools_path: settings.bcftools_path,
        step1_max_parallel: settings.step1_max_parallel,
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
    try {
      const editsRes = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/edits`);
      if (editsRes.ok) {
        const edits = await editsRes.json();
        setStep1Edits(edits || {});
        const editedCount = Object.values(edits || {}).filter((e) => e?.edited).length;
        setStep1EditedCount(editedCount);
      } else {
        setStep1Edits({});
        setStep1EditedCount(0);
      }
    } catch {
      setStep1Edits({});
      setStep1EditedCount(0);
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
      setStep2EditedCount(countData.edited_count || 0);
    } else {
      setStep2EditedCount(0);
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

  function downloadOutput(path) {
    if (!selectedProject) return;
    const url = `${API_BASE}/api/projects/${selectedProject}/download-file?path=${encodeURIComponent(path)}`;
    window.open(url, "_blank");
  }

  async function openPosthocOutput(path) {
    if (!path) return;
    await fetch(`${API_BASE}/api/posthoc/open`, {
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

  async function linkLocal(pathOverride = "") {
    const pathToUse = pathOverride || localPath;
    if (!selectedProject || !settingsReady || !pathToUse) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/link-local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: pathToUse })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "Link local failed");
      return;
    }
    await loadAll();
  }

  async function uploadFiles(fileList) {
    if (!selectedProject || !settingsReady) {
      setUploadStatus("Select a project and complete Settings before uploading.");
      return;
    }
    if (!fileList?.length) {
      setUploadStatus("No files selected.");
      return;
    }
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
    setSraStatus(`Downloading ${accessions.length} accession${accessions.length > 1 ? "s" : ""}...`);
    setShowRowLogs(true);
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/sra/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accessions, folder: sraFolder || null })
    });
    const data = await res.json();
    setJobId(data.job_id);
    setSraJobId(data.job_id);
  }

  async function importVcfs() {
    if (!selectedProject || !settingsReady) return;
    // Auto-populate from enabled vcfDbFolders
    const enabledPaths = vcfDbFolders.filter((f) => f.enabled).map((f) => f.path);
    const manualPaths = parseAccessions(importSourcesText);
    const allPaths = [...new Set([...enabledPaths, ...manualPaths])];
    const sourcesText = allPaths.join("\n");
    setImportSourcesText(sourcesText);
    const sources = allPaths;
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
    setStep2SetupMsg("Step 1 rerun started. Rebuild Step 2 VCF set before running Step 2.");
    setStep2BuiltAt("");
    setStep2VcfCount(0);
    setStep2Outputs([]);
    setStep2Groups([]);
    setStep2OutputsError("");
    setStep2RunId("");
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference: refValue, debug: debugMode, assemble_unmap: assembleUnmap, nanopore: nanoporeMode })
    });
    const data = await res.json();
    setJobId(data.job_id);
    setStep1AutoRefreshPending(true);
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
      body: JSON.stringify({
        reference: reference || null,
        no_filters: s2NoFilters,
        qual_threshold: s2QualThreshold,
        n_threshold: s2NThreshold,
        mq_threshold: s2MqThreshold,
        all_vcf: s2AllVcf,
        label_style: s2LabelStyle,
        find_new_filters: s2FindNewFilters,
        hash_groups: s2HashGroups,
        show_groups: s2ShowGroups,
        html_tree: s2HtmlTree,
        dp: s2Dp,
        density_threshold: s2DensityThreshold !== "" ? parseInt(s2DensityThreshold, 10) : null,
        density_window: s2DensityWindow !== "" ? parseInt(s2DensityWindow, 10) : null,
        bootstrap: s2Bootstrap !== "" ? parseInt(s2Bootstrap, 10) || 0 : 0
      })
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

  function downloadStep1Stats(project, sample) {
    if (!project || !sample) return;
    const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/samples/${encodeURIComponent(sample)}/stats/download`;
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function openStep1FolderModal(project, sample) {
    if (!project || !sample) return;
    setFolderModal({ open: true, project, sample, files: [], sampleDir: "", loading: true, error: "" });
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/samples/${encodeURIComponent(sample)}/files`);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setFolderModal((prev) => ({ ...prev, loading: false, error: detail.detail || `Failed to load files (${res.status})` }));
        return;
      }
      const data = await res.json();
      setFolderModal({ open: true, project, sample, files: data.files || [], sampleDir: data.sample_dir || "", loading: false, error: "" });
    } catch (err) {
      setFolderModal((prev) => ({ ...prev, loading: false, error: String(err) }));
    }
  }

  function closeFolderModal() {
    setFolderModal({ open: false, project: "", sample: "", files: [], sampleDir: "", loading: false, error: "" });
  }

  function downloadFolderFile(project, path) {
    if (!project || !path) return;
    const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/download-file?path=${encodeURIComponent(path)}`;
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function formatBytes(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let v = bytes / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
  }

  function igvServeUrl(project, absPath) {
    return `${API_BASE}/api/projects/${encodeURIComponent(project)}/serve?path=${encodeURIComponent(absPath)}`;
  }

  async function openSampleInIgv(project, sample) {
    if (!project || !sample) {
      window.alert("IGV: missing project or sample.");
      return;
    }
    if (igvPopoutOpen) {
      const w = igvPopoutRef.current;
      if (w && !w.closed) {
        try {
          w.postMessage({ type: "vsnpAddSample", project, sample }, window.location.origin);
          w.focus();
        } catch (e) { /* fall through to drawer */ }
        return;
      }
      igvPopoutRef.current = null;
      setIgvPopoutOpen(false);
    }
    const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/files?sample=${encodeURIComponent(sample)}`);
    if (!res.ok) {
      window.alert(`IGV: cannot resolve sample files (HTTP ${res.status}).`);
      return;
    }
    const data = await res.json();
    if (!data.bam || !data.reference_fasta) {
      window.alert("IGV: BAM or reference FASTA not found for this sample.");
      return;
    }
    const baiPath = data.bam.endsWith(".bam") ? `${data.bam}.bai` : "";
    setIgvPanel((prev) => {
      if (!prev.open) {
        const firstTrack = { project, sample, bamPath: data.bam, baiPath, displayName: sample };
        return {
          open: true,
          project,
          referenceFastaPath: data.reference_fasta,
          referenceFaiPath: `${data.reference_fasta}.fai`,
          tracks: [firstTrack],
          status: "",
        };
      }
      const prevRefName = (prev.referenceFastaPath || "").split("/").pop();
      const newRefName = (data.reference_fasta || "").split("/").pop();
      if (prevRefName !== newRefName) {
        return { ...prev, status: `Cannot mix references (${prevRefName} vs ${newRefName}).` };
      }
      if (prev.tracks.some((t) => t.project === project && t.sample === sample)) {
        return { ...prev, status: `${sample} already loaded.` };
      }
      const displayName = project !== prev.project ? `${project}/${sample}` : sample;
      const newTrack = { project, sample, bamPath: data.bam, baiPath, displayName };
      return { ...prev, tracks: [...prev.tracks, newTrack], status: "" };
    });
  }

  function closeIgvPanel() {
    if (igvBrowserRef.current) {
      try { igv.removeBrowser(igvBrowserRef.current); } catch (e) { /* ignore */ }
      igvBrowserRef.current = null;
    }
    setIgvPanel({ open: false, project: "", referenceFastaPath: "", referenceFaiPath: "", tracks: [], status: "", height: 45, fullscreen: false });
  }

  function toggleIgvFullscreen() {
    setIgvPanel((prev) => ({ ...prev, fullscreen: !prev.fullscreen }));
  }

  function popOutIgv() {
    if (!igvPanel.open || igvPanel.tracks.length === 0) return;
    const trackParts = igvPanel.tracks.map((t) => `${t.project}:${t.sample}`);
    const base = window.location.pathname.replace(/[^/]*$/, "");
    const url = `${base}?view=igv&tracks=${encodeURIComponent(trackParts.join(","))}`;
    const w = window.open(url, "vsnp_igv_popout");
    if (!w) {
      window.alert("Pop out blocked by browser. Allow popups for this site and try again.");
      return;
    }
    igvPopoutRef.current = w;
    setIgvPopoutOpen(true);
    if (igvBrowserRef.current) {
      try { igv.removeBrowser(igvBrowserRef.current); } catch (e) { /* ignore */ }
      igvBrowserRef.current = null;
    }
    setIgvPanel({ open: false, project: "", referenceFastaPath: "", referenceFaiPath: "", tracks: [], status: "", height: 45, fullscreen: false });
  }

  function startIgvResize(e) {
    e.preventDefault();
    const onMove = (ev) => {
      const distFromBottom = window.innerHeight - ev.clientY;
      const newHeight = Math.min(95, Math.max(15, (distFromBottom / window.innerHeight) * 100));
      setIgvPanel((prev) => ({ ...prev, height: newHeight }));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  useEffect(() => {
    if (!igvPanel.open || igvPanel.tracks.length === 0) return;
    let cancelled = false;
    if (!igvBrowserRef.current) {
      const config = {
        reference: {
          id: igvPanel.referenceFastaPath.split("/").pop().replace(/\.(fa|fasta)$/i, "") || "ref",
          fastaURL: igvServeUrl(igvPanel.project, igvPanel.referenceFastaPath),
          indexURL: igvServeUrl(igvPanel.project, igvPanel.referenceFaiPath),
        },
        tracks: igvPanel.tracks.map((t) => ({
          type: "alignment",
          format: "bam",
          name: t.displayName || t.sample,
          url: igvServeUrl(t.project || igvPanel.project, t.bamPath),
          indexURL: igvServeUrl(t.project || igvPanel.project, t.baiPath),
        })),
      };
      const target = igvContainerRef.current;
      if (!target) return;
      igv.createBrowser(target, config).then((browser) => {
        if (cancelled) {
          try { igv.removeBrowser(browser); } catch (e) { /* ignore */ }
          return;
        }
        igvBrowserRef.current = browser;
      }).catch((err) => {
        setIgvPanel((prev) => ({ ...prev, status: `IGV failed to load: ${err && err.message ? err.message : err}` }));
      });
      return () => { cancelled = true; };
    }
    const browser = igvBrowserRef.current;
    const loadedSet = new Set(
      (browser.trackViews || [])
        .map((tv) => tv && tv.track && tv.track.name)
        .filter(Boolean)
    );
    const toLoad = igvPanel.tracks.filter((t) => !loadedSet.has(t.displayName || t.sample));
    for (const t of toLoad) {
      browser.loadTrack({
        type: "alignment",
        format: "bam",
        name: t.displayName || t.sample,
        url: igvServeUrl(t.project || igvPanel.project, t.bamPath),
        indexURL: igvServeUrl(t.project || igvPanel.project, t.baiPath),
      }).catch((err) => {
        setIgvPanel((prev) => ({ ...prev, status: `Track ${t.sample} failed: ${err && err.message ? err.message : err}` }));
      });
    }
  }, [igvPanel.open, igvPanel.tracks, igvPanel.referenceFastaPath, igvPanel.project]);

  function openEditVcf(sample, project = "") {
    if (!sample) return;
    setEditVcfSample(sample);
    setEditVcfProject(project || selectedProject || "");
    setEditVcfLocus("");
    setEditVcfAlt("");
    setEditVcfNote("");
    setEditVcfReason("");
    setEditVcfCurrent(null);
    setEditVcfOpen(true);
  }

  async function fetchCurrentVcfCall() {
    const project = editVcfProject || selectedProject;
    if (!project || !editVcfSample || !editVcfLocus) {
      window.alert("Select a sample and locus first.");
      return;
    }
    const res = await fetch(`${API_BASE}/api/projects/${project}/vcf_lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sample: editVcfSample,
        locus: editVcfLocus
      })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(typeof msg.detail === "string" ? msg.detail : "Lookup failed");
      return;
    }
    const data = await res.json();
    setEditVcfCurrent(data);
    if (data?.alt) {
      setEditVcfAlt(String(data.alt).toUpperCase());
    }
  }

  async function submitEditVcf() {
    const project = editVcfProject || selectedProject;
    if (!project || !editVcfSample) {
      window.alert("Select a project to edit this sample.");
      return;
    }
    if (!settings.bcftools_path) {
      window.alert("bcftools path is not configured in Settings.");
      return;
    }
    if (!editVcfLocus || !editVcfAlt) {
      window.alert("Locus and ALT are required.");
      return;
    }
    if (!editVcfReason) {
      window.alert("Please select a reason for the edit.");
      return;
    }
    const res = await fetch(`${API_BASE}/api/projects/${project}/vcf_edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sample: editVcfSample,
        locus: editVcfLocus,
        new_alt: editVcfAlt,
        note: editVcfNote,
        reason: editVcfReason
      })
    });
    if (!res.ok) {
      const msg = await res.json();
      window.alert(msg.detail || "VCF edit failed");
      return;
    }
    const data = await res.json();
    window.alert(`VCF patched:\\n${data.patched_vcf}`);
    setEditVcfOpen(false);
    if (project === selectedProject) {
      await loadQC();
      setEditVcfCurrent(null);
      if (step2Mode === "step1") {
        await step2Setup();
      } else {
        setStep2SetupMsg("Patched VCF created. Rebuild the Step 2 VCF set to use it.");
      }
      await loadStep2Outputs();
    }
  }

  async function addPosthocFolder() {
    let picked = "";
    if (window?.vsnp?.selectPath) {
      picked = await window.vsnp.selectPath({
        kind: "folder",
        title: "Select Step 1 folder",
        defaultPath: settings.projects_root || undefined
      });
    } else {
      picked = window.prompt("Enter Step 1 folder path:");
    }
    if (!picked) return;
    setPosthocFolders((prev) => (prev.includes(picked) ? prev : [...prev, picked]));
  }

  function removePosthocFolder(path) {
    setPosthocFolders((prev) => prev.filter((p) => p !== path));
  }

  function togglePosthocCurrentProject() {
    if (!selectedProject || !settings.projects_root) return;
    const step1Path = `${settings.projects_root}/${selectedProject}/step1`;
    setPosthocFolders((prev) =>
      prev.includes(step1Path) ? prev.filter((p) => p !== step1Path) : [...prev, step1Path]
    );
  }

  async function resolvePosthocSamples(samples) {
    if (!samples.length) return;
    setPosthocResolveMsg("");
    try {
      const res = await fetch(`${API_BASE}/api/posthoc/step1/resolve_samples`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ samples })
      });
      if (!res.ok) {
        const msg = await res.json();
        setPosthocResolveMsg(msg.detail || "Sample lookup failed");
        return;
      }
      const data = await res.json();
      const foundDirs = (data.found || []).map((row) => row.sample_dir).filter(Boolean);
      setPosthocFolders((prev) => {
        const merged = [...prev];
        for (const dir of foundDirs) {
          if (!merged.includes(dir)) {
            merged.push(dir);
          }
        }
        return merged;
      });
      const missing = data.missing || [];
      const noteParts = [];
      if (foundDirs.length) noteParts.push(`Added ${foundDirs.length}`);
      if (missing.length) noteParts.push(`Missing ${missing.length}`);
      setPosthocResolveMsg(noteParts.join(" · "));
      if (missing.length) {
        setPosthocResolveMsg(`${noteParts.join(" · ")} (e.g., ${missing.slice(0, 3).join(", ")})`);
      }
    } catch {
      setPosthocResolveMsg("Sample lookup failed");
    }
  }

  async function loadPosthoc() {
    if (!posthocFolders.length) return;
    setPosthocLoading(true);
    setPosthocError("");
    try {
      const res = await fetch(`${API_BASE}/api/posthoc/step1/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folders: posthocFolders })
      });
      if (!res.ok) {
        const msg = await res.json();
        setPosthocError(msg.detail || "Post-hoc scan failed");
        setPosthocRows([]);
      } else {
        const data = await res.json();
        setPosthocRows(data || []);
      }
    } catch {
      setPosthocError("Post-hoc scan failed");
      setPosthocRows([]);
    } finally {
      setPosthocLoading(false);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-brand">
          <img className="app-logo" src="/vSNP_icon_align_256.png" alt="vSNP alignment icon" />
          <div>
            <h1>
              vSNP GUI <span className="version-tag">{APP_VERSION}</span>
            </h1>
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
            <span className="status-value">{selected?.display_name || selected?.name || "None"}</span>
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
          <div className="row-grid" style={{gridTemplateColumns: "1fr"}}>
            <section className="panel">
            <div className="panel-header">
              <h2>Settings</h2>
            </div>
            <div className="input-columns">
              <div className="input-column">
                <label className="label" style={{fontWeight:600, color:"var(--text)", fontSize:"13px", marginBottom:0}}>Required</label>
                <div className="settings-row">
                  <label className="label">vSNP3 path</label>
                  <input
                    placeholder="/path/to/vsnp3"
                    value={settings.vsnp3_path}
                    onChange={(e) => setSettings({ ...settings, vsnp3_path: e.target.value })}
                  />
                  <span style={{display:"inline-flex", alignItems:"center", gap:"4px"}}>
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
                    {pathValidation.vsnp3_path === true ? (
                      <span style={{color:"var(--success)", fontWeight:600, fontSize:"14px"}}>&#10003;</span>
                    ) : (
                      <span style={{color:"var(--danger)", fontWeight:600, fontSize:"14px"}}>&#10007;</span>
                    )}
                  </span>
                </div>
                <div className="settings-row">
                  <label className="label">Projects root</label>
                  <input
                    placeholder="/path/to/projects"
                    value={settings.projects_root}
                    onChange={(e) => setSettings({ ...settings, projects_root: e.target.value })}
                  />
                  <span style={{display:"inline-flex", alignItems:"center", gap:"4px"}}>
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
                    {pathValidation.projects_root === true ? (
                      <span style={{color:"var(--success)", fontWeight:600, fontSize:"14px"}}>&#10003;</span>
                    ) : (
                      <span style={{color:"var(--danger)", fontWeight:600, fontSize:"14px"}}>&#10007;</span>
                    )}
                  </span>
                </div>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={settings.sra_allow_insecure_https}
                    onChange={(e) =>
                      setSettings({ ...settings, sra_allow_insecure_https: e.target.checked })
                    }
                  />
                  Allow insecure HTTPS for ENA
                </label>
              </div>
              <div className="input-column">
                <label className="label" style={{fontWeight:600, color:"var(--text)", fontSize:"13px", marginBottom:0}}>Optional</label>
                <div className="settings-row">
                  <label className="label">FigTree app</label>
                  <input
                    placeholder="/Applications/FigTree.app"
                    value={settings.figtree_app_path}
                    onChange={(e) => setSettings({ ...settings, figtree_app_path: e.target.value })}
                  />
                  <span style={{display:"inline-flex", alignItems:"center", gap:"4px"}}>
                    {canPickPath ? (
                      <button
                        className="ghost action"
                        onClick={() =>
                          pickPath(
                            "file",
                            "Select FigTree app",
                            settings.figtree_app_path,
                            (value) => setSettings({ ...settings, figtree_app_path: value })
                          )
                        }
                      >
                        Choose
                      </button>
                    ) : null}
                    {pathValidation.figtree_app_path === true ? (
                      <span style={{color:"var(--success)", fontWeight:600, fontSize:"14px"}}>&#10003;</span>
                    ) : pathValidation.figtree_app_path === false ? (
                      <span style={{color:"var(--danger)", fontWeight:600, fontSize:"14px"}}>&#10007;</span>
                    ) : null}
                  </span>
                </div>
                <div className="settings-row">
                  <label className="label">bcftools</label>
                  <input
                    placeholder="/path/to/bcftools"
                    value={settings.bcftools_path}
                    onChange={(e) => setSettings({ ...settings, bcftools_path: e.target.value })}
                  />
                  <span style={{display:"inline-flex", alignItems:"center", gap:"4px"}}>
                    {canPickPath ? (
                      <button
                        className="ghost action"
                        onClick={() =>
                          pickPath("file", "Select bcftools", settings.bcftools_path, (value) =>
                            setSettings({ ...settings, bcftools_path: value })
                          )
                        }
                      >
                        Choose
                      </button>
                    ) : null}
                    {pathValidation.bcftools_path === true ? (
                      <span style={{color:"var(--success)", fontWeight:600, fontSize:"14px"}}>&#10003;</span>
                    ) : pathValidation.bcftools_path === false ? (
                      <span style={{color:"var(--danger)", fontWeight:600, fontSize:"14px"}}>&#10007;</span>
                    ) : null}
                  </span>
                </div>
                <div className="settings-row">
                  <label className="label">Max parallel</label>
                  <input
                    type="number"
                    min="1"
                    max="8"
                    style={{width:"60px"}}
                    value={settings.step1_max_parallel}
                    onChange={(e) =>
                      setSettings({ ...settings, step1_max_parallel: Number(e.target.value) || 1 })
                    }
                  />
                  <span />
                </div>
              </div>
            </div>
            <div style={{display:"flex", justifyContent:"flex-end", marginTop:"12px"}}>
              <button onClick={saveSettings} title="Save settings">Save</button>
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
                    <div className="list-title">{p.display_name || p.name}</div>
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
                <button
                  onClick={async () => {
                    if (window?.vsnp?.selectPath) {
                      const picked = await window.vsnp.selectPath({
                        kind: "folder",
                        title: "Select FASTQ folder",
                        defaultPath: settings.projects_root || undefined
                      });
                      if (picked) {
                        setLocalPath(picked);
                        await linkLocal(picked);
                      }
                    } else {
                      const picked = window.prompt("Enter FASTQ folder path:");
                      if (picked) {
                        setLocalPath(picked);
                        await linkLocal(picked);
                      }
                    }
                  }}
                  disabled={!selectedProject || !settingsReady}
                >
                  Choose Folder
                </button>
                {localPath ? <div className="note">Selected: {localPath}</div> : null}
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
                  {!selectedProject || !settingsReady ? (
                    <div className="note warning">Select a project and complete Settings to enable uploads.</div>
                  ) : null}
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
                {sraStatus ? (
                  <div className={`note${sraStatus.toLowerCase().includes("fail") ? " error" : ""}`}>
                    {sraStatus.includes("Downloading") ? (
                      <span className="pulse-dot" />
                    ) : null}
                    {sraStatus}
                  </div>
                ) : null}
              </div>
            </div>
          </section>
        </div>
        ) : null}

        <div className="row-header">
          <h2>Reference Editor</h2>
          <button className="ghost" onClick={() => setShowRowRefEditor(!showRowRefEditor)}>
            {showRowRefEditor ? "Hide" : "Show"}
          </button>
        </div>

        {showRowRefEditor ? (
          <div className="row-grid row-grid-split">
            <section className="panel">
              <h2>Reference Selection</h2>
              <div className="block">
                <details open={showRefPaths} onToggle={(e) => { setShowRefPaths(e.target.open); if (e.target.open) loadRefPaths(); }}>
                  <summary className="ghost action" style={{cursor:"pointer", fontSize:"0.85em"}}>Reference Locations</summary>
                  <div className="ref-paths-list" style={{fontSize:"0.85em", marginTop:"0.3em"}}>
                    {refPaths.length ? refPaths.map((p, i) => (
                      <div key={i} className="ref-path-item" style={{display:"flex", alignItems:"center", gap:"0.3em", marginBottom:"0.2em"}}>
                        <span className="muted" style={{wordBreak:"break-all", flex:1}}>{p}</span>
                        <button className="ghost-btn danger" style={{fontSize:"0.8em"}} onClick={() => removeRefPath(p)}>x</button>
                      </div>
                    )) : <div className="muted">No custom reference paths configured.</div>}
                    <div style={{display:"flex", gap:"0.3em", marginTop:"0.3em"}}>
                      <input
                        placeholder="/path/to/reference_root"
                        value={refPathInput}
                        onChange={(e) => setRefPathInput(e.target.value)}
                        style={{flex:1}}
                      />
                      <button
                        className="ghost action"
                        style={{fontSize:"0.85em"}}
                        onClick={() => {
                          const trimmed = refPathInput.trim();
                          if (!trimmed) return;
                          addRefPath(trimmed);
                          setRefPathInput("");
                        }}
                      >
                        Add
                      </button>
                    </div>
                    {canPickPath ? (
                      <button className="ghost action" style={{fontSize:"0.85em", marginTop:"0.3em"}} onClick={() => pickPath("directory", "Add reference location", "", (dir) => addRefPath(dir))}>
                        Add Location
                      </button>
                    ) : null}
                  </div>
                </details>
                <details open={showGenomeDownload} onToggle={(e) => setShowGenomeDownload(e.target.open)}>
                  <summary className="ghost action" style={{cursor:"pointer", fontSize:"0.85em", marginTop:"0.3em"}}>Download New Reference</summary>
                  <div style={{fontSize:"0.85em", marginTop:"0.3em"}}>
                    <input
                      placeholder="GenBank accession (e.g. AF2122/97)"
                      value={genomeAccession}
                      onChange={(e) => setGenomeAccession(e.target.value)}
                      style={{width:"100%", marginBottom:"0.3em"}}
                    />
                    <div className="row" style={{gap:"0.3em", alignItems:"center"}}>
                      <input
                        placeholder="Output directory"
                        value={genomeOutputDir}
                        readOnly={canPickPath}
                        onChange={(e) => setGenomeOutputDir(e.target.value)}
                        style={{flex:1}}
                      />
                      {canPickPath ? (
                        <button className="ghost action" style={{fontSize:"0.85em"}} onClick={() => pickPath("directory", "Select output folder", "", (dir) => setGenomeOutputDir(dir))}>
                          Browse
                        </button>
                      ) : null}
                    </div>
                    <button
                      onClick={downloadGenome}
                      disabled={!genomeAccession.trim() || !genomeOutputDir.trim() || !settingsReady}
                      style={{marginTop:"0.3em"}}
                    >
                      Download
                    </button>
                    {genomeDownloadStatus ? (
                      <div className="note" style={{marginTop:"0.3em"}}>
                        {genomeDownloadStatus.includes("Downloading") ? <span className="pulse-dot" /> : null}
                        {genomeDownloadStatus}
                      </div>
                    ) : null}
                  </div>
                </details>
              </div>
              <div className="block">
                <h3>Reference Type</h3>
                <select
                  value={refEditorRef}
                  onChange={(e) => {
                    setRefEditorRef(e.target.value);
                    loadRefEditorFiles(e.target.value);
                  }}
                >
                  <option value="">Choose a reference...</option>
                  {references.map((r) => (
                    <option key={r.name} value={r.name}>{r.name}</option>
                  ))}
                </select>
                {refEditorPath ? (
                  <div className="note" style={{wordBreak:"break-all", marginTop:"0.5em"}}>
                    Directory: {refEditorPath}
                  </div>
                ) : null}
              </div>
            </section>
            <section className="panel">
              <h2>Edit Filter / Exclusion Spreadsheets</h2>
              {refEditorRef ? (
                <div className="block">
                  {(() => {
                    const hasDefine = refEditorFiles.some((f) => f.type === "define_filter");
                    const hasRemove = refEditorFiles.some((f) => f.type === "remove_from_analysis");
                    const defineFile = refEditorFiles.find((f) => f.type === "define_filter");
                    const removeFile = refEditorFiles.find((f) => f.type === "remove_from_analysis");
                    return (
                      <>
                        <div className="ref-editor-card">
                          <h3>Define Filter</h3>
                          <div className="muted" style={{fontSize:"0.85em", marginBottom:"0.4em"}}>
                            Controls which positions are included in the SNP analysis.
                          </div>
                          {hasDefine && defineFile ? (
                            <div className="ref-editor-file-row">
                              <span className="ref-editor-filename">{defineFile.name}</span>
                              <button onClick={() => openRefFile(refEditorRef, defineFile.name)}>
                                Edit in Spreadsheet App
                              </button>
                            </div>
                          ) : (
                            <div className="ref-editor-file-row">
                              <span className="muted">No define_filter file found</span>
                              <button onClick={() => createRefFile(refEditorRef, "define_filter")}>
                                Create from Template
                              </button>
                            </div>
                          )}
                        </div>

                        <div className="ref-editor-card">
                          <h3>Remove from Analysis</h3>
                          <div className="muted" style={{fontSize:"0.85em", marginBottom:"0.4em"}}>
                            Lists samples or regions to exclude from the analysis.
                          </div>
                          {hasRemove && removeFile ? (
                            <div className="ref-editor-file-row">
                              <span className="ref-editor-filename">{removeFile.name}</span>
                              <button onClick={() => openRefFile(refEditorRef, removeFile.name)}>
                                Edit in Spreadsheet App
                              </button>
                            </div>
                          ) : (
                            <div className="ref-editor-file-row">
                              <span className="muted">No remove_from_analysis file found</span>
                              <button onClick={() => createRefFile(refEditorRef, "remove_from_analysis")}>
                                Create from Template
                              </button>
                            </div>
                          )}
                        </div>

                        <div className="note" style={{marginTop:"0.8em"}}>
                          Clicking "Edit in Spreadsheet App" opens the .xlsx file in your default spreadsheet application (e.g. Excel, LibreOffice). Save and close the file before running Step 2.
                        </div>
                        <button
                          className="ghost action"
                          style={{marginTop:"0.4em"}}
                          onClick={() => loadRefEditorFiles(refEditorRef)}
                        >
                          Refresh file list
                        </button>
                      </>
                    );
                  })()}
                </div>
              ) : (
                <div className="block">
                  <div className="note">Select a reference type on the left to edit its filter and exclusion spreadsheets.</div>
                </div>
              )}
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
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={debugMode}
                  onChange={(e) => setDebugMode(e.target.checked)}
                />
                Debug (keep intermediates, skip cleanup)
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={assembleUnmap}
                  onChange={(e) => setAssembleUnmap(e.target.checked)}
                />
                Assemble unmapped reads
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={nanoporeMode}
                  onChange={(e) => setNanoporeMode(e.target.checked)}
                />
                Nanopore (ONT) reads
              </label>
              <div className="step1-actions">
                <button onClick={step1Setup} disabled={!selectedProject || !settingsReady}>Setup</button>
                <button onClick={step1Run} disabled={!selectedProject || !settingsReady || !reference}>Run</button>
              </div>
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
              <h2>Step 1 Results</h2>
              <div className="qc-actions">
                <div className="mode-toggle">
                  <button
                    className={step1ResultsTab === "results" ? "active" : ""}
                    onClick={() => setStep1ResultsTab("results")}
                  >
                    Results
                  </button>
                  <button
                    className={step1ResultsTab === "posthoc" ? "active" : ""}
                    onClick={() => setStep1ResultsTab("posthoc")}
                  >
                    Post-hoc
                  </button>
                </div>
                {step1ResultsTab === "results" ? (
                  <>
                    <button onClick={loadQC} disabled={!selectedProject || qcLoading}>
                      {qcLoading ? "Loading..." : "Refresh"}
                    </button>
                    <button onClick={downloadQC} disabled={!selectedProject}>Download CSV</button>
                    <button onClick={downloadQcXlsx} disabled={!selectedProject}>Download XLSX</button>
                    <button onClick={saveExclusions} disabled={!selectedProject}>Save Exclusions</button>
                  </>
                ) : (
                  <>
                    <button
                      className={posthocFolders.includes(`${settings.projects_root}/${selectedProject}/step1`) ? "ghost active" : "ghost"}
                      onClick={togglePosthocCurrentProject}
                      disabled={!selectedProject || !settings.projects_root}
                    >
                      Current Project
                    </button>
                    <button onClick={addPosthocFolder}>Add Step 1 Folder</button>
                    <button onClick={loadPosthoc} disabled={!posthocFolders.length || posthocLoading}>
                      {posthocLoading ? "Loading..." : "Load"}
                    </button>
                  </>
                )}
              </div>
            </div>
            {step1ResultsTab === "results" ? (
              <>
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
                        .map((row) => {
                          const key = sampleKey(row);
                          const editInfo = step1Edits[key];
                          return (
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
                              <td>
                                <div className="cell-inline">
                                  <span>{row._sample || row.sample || "-"}</span>
                                  {editInfo?.edited ? <span className="badge edited">Edited</span> : null}
                                </div>
                              </td>
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
                                      onClick={() => openStep1FolderModal(selectedProject, sampleKey(row))}
                                      disabled={!sampleKey(row)}
                                    >
                                      Open Folder
                                    </button>
                                    <button
                                      onClick={() => openSampleInIgv(selectedProject, sampleKey(row))}
                                      disabled={!sampleKey(row)}
                                    >
                                      IGV
                                    </button>
                                    <button
                                      onClick={() => openEditVcf(sampleKey(row), selectedProject)}
                                      disabled={!sampleKey(row)}
                                    >
                                      Edit VCF
                                    </button>
                                    {editInfo?.edit_log ? (
                                      <button onClick={() => openOutput(editInfo.edit_log)}>Edit Log</button>
                                    ) : null}
                                    {sampleKey(row) ? (
                                      <button onClick={() => downloadStep1Stats(selectedProject, sampleKey(row))}>Stats</button>
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
                          );
                        })}
                    </tbody>
                  </table>
                </div>
                {qcRows.length > 8 ? <div className="scroll-note">Scroll for more samples.</div> : null}
              </>
            ) : (
              <>
                <div className="note">
                  Add Step 1 folders to build a temporary merged view for IGV sanity checks.
                </div>
                <div className="posthoc-inputs">
                  <div className="inline-fields">
                    <input
                      placeholder="Sample ID (e.g., SRR10321141)"
                      value={posthocSampleInput}
                      onChange={(e) => setPosthocSampleInput(e.target.value)}
                    />
                    <button
                      className="ghost"
                      onClick={() => {
                        const sample = posthocSampleInput.trim();
                        if (sample) {
                          resolvePosthocSamples([sample]);
                          setPosthocSampleInput("");
                        }
                      }}
                    >
                      Add sample
                    </button>
                  </div>
                  <textarea
                    placeholder="Paste sample IDs (one per line)"
                    value={posthocSampleList}
                    onChange={(e) => setPosthocSampleList(e.target.value)}
                    rows={3}
                  />
                  <button
                    className="ghost"
                    onClick={() => {
                      const samples = posthocSampleList
                        .split(/[\n,]+/)
                        .map((s) => s.trim())
                        .filter(Boolean);
                      if (samples.length) {
                        resolvePosthocSamples(samples);
                      }
                    }}
                  >
                    Add list
                  </button>
                  {posthocResolveMsg ? <div className="note">{posthocResolveMsg}</div> : null}
                </div>
                {posthocFolders.length ? (
                  <ul className="posthoc-list">
                    {posthocFolders.map((p) => (
                      <li key={p}>
                        <span>{p}</span>
                        <button className="ghost" onClick={() => removePosthocFolder(p)}>Remove</button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="muted">No folders added yet.</div>
                )}
                {posthocError ? <div className="note error">{posthocError}</div> : null}
                {reference ? (
                  <div className="note">Filtering to reference: {reference}</div>
                ) : null}
                {posthocFilteredRows.length ? (
                  <div className="qc-table scrollable">
                    <table>
                      <thead>
                        <tr>
                          <th>Source</th>
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
                        {posthocFilteredRows.map((row) => {
                          const sampleDir = row._sample_dir || (row._file ? row._file.replace(/\/[^/]+$/, "") : "");
                          const sampleName = row._sample || row.sample || "-";
                          const isEdited = Boolean(row._edited);
                          const editLog = row._edit_log || "";
                          return (
                          <tr key={row._file}>
                            <td>{row._project || "-"}</td>
                            <td>
                              <div className="cell-inline">
                                <span>{sampleName}</span>
                                {isEdited ? <span className="badge edited">Edited</span> : null}
                              </div>
                            </td>
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
                                    onClick={() => openStep1FolderModal(row._project || "", row._sample || row.sample || "")}
                                    disabled={!(row._project && (row._sample || row.sample))}
                                  >
                                    Open Folder
                                  </button>
                                  <button
                                    onClick={() => openSampleInIgv(row._project || "", row._sample || row.sample || "")}
                                    disabled={!(row._project && (row._sample || row.sample))}
                                  >
                                    IGV
                                  </button>
                                  <button
                                    onClick={() =>
                                      openEditVcf(row._sample || row.sample || "", row._project || "")
                                    }
                                  >
                                    Edit VCF
                                  </button>
                                  {editLog ? (
                                    <button onClick={() => openPosthocOutput(editLog)}>Edit Log</button>
                                  ) : null}
                                  {row._project && (row._sample || row.sample) ? <button onClick={() => downloadStep1Stats(row._project, row._sample || row.sample)}>Stats</button> : null}
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
                        )})}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="note">No post-hoc results loaded yet.</div>
                )}
              </>
            )}
          </section>
        </div>
        ) : null}

        {igvPanel.open ? (
          <div
            style={{
              position: "fixed",
              left: 0,
              right: 0,
              bottom: 0,
              top: igvPanel.fullscreen ? 0 : "auto",
              height: igvPanel.fullscreen ? "100vh" : `${igvPanel.height}vh`,
              background: "#fff",
              borderTop: igvPanel.fullscreen ? "none" : "1px solid #ccc",
              boxShadow: igvPanel.fullscreen ? "none" : "0 -4px 12px rgba(0,0,0,0.12)",
              zIndex: 9000,
              display: "flex",
              flexDirection: "column",
            }}
          >
            {!igvPanel.fullscreen ? (
              <div
                onMouseDown={startIgvResize}
                title="Drag to resize"
                style={{
                  height: "6px",
                  cursor: "ns-resize",
                  background: "#e6e6e6",
                  borderBottom: "1px solid #d0d0d0",
                }}
              />
            ) : null}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "1rem",
                padding: "0.4rem 0.8rem",
                borderBottom: "1px solid #eee",
                background: "#f7f7f7",
              }}
            >
              <strong>IGV</strong>
              <span className="muted" style={{ fontSize: "0.85em" }}>
                {igvPanel.referenceFastaPath ? igvPanel.referenceFastaPath.split("/").pop() : ""}
                {igvPanel.tracks.length ? ` · ${igvPanel.tracks.length} track${igvPanel.tracks.length === 1 ? "" : "s"}` : ""}
                {(() => {
                  const projects = Array.from(new Set(igvPanel.tracks.map((t) => t.project).filter(Boolean)));
                  return projects.length ? ` · ${projects.join(", ")}` : "";
                })()}
              </span>
              {igvPanel.status ? (
                <span style={{ color: "#b34", fontSize: "0.85em" }}>{igvPanel.status}</span>
              ) : null}
              <span style={{ flex: 1 }} />
              <button onClick={popOutIgv} title="Open this panel in a new tab">Pop out</button>
              <button onClick={toggleIgvFullscreen} title="Toggle fullscreen">
                {igvPanel.fullscreen ? "Exit fullscreen" : "Fullscreen"}
              </button>
              <button onClick={closeIgvPanel}>Close</button>
            </div>
            <div ref={igvContainerRef} style={{ flex: 1, overflow: "auto" }} />
          </div>
        ) : null}

        {folderModal.open ? (
          <div className="modal-backdrop" onClick={closeFolderModal}>
            <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
              <h3>Sample folder</h3>
              <div className="note">
                <div><strong>{folderModal.sample}</strong>{folderModal.project ? <span> (Project: {folderModal.project})</span> : null}</div>
                {folderModal.sampleDir ? <div className="muted" style={{ wordBreak: "break-all" }}>{folderModal.sampleDir}</div> : null}
              </div>
              {folderModal.loading ? (
                <div className="note">Loading…</div>
              ) : folderModal.error ? (
                <div className="error">{folderModal.error}</div>
              ) : folderModal.files.length === 0 ? (
                <div className="note">No files in this folder.</div>
              ) : (
                <div className="folder-modal-files" style={{ maxHeight: "60vh", overflow: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>File</th>
                        <th>Type</th>
                        <th style={{ textAlign: "right" }}>Size</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {folderModal.files.map((f) => (
                        <tr key={f.relpath}>
                          <td style={{ wordBreak: "break-all" }}>{f.relpath}</td>
                          <td>{f.type}</td>
                          <td style={{ textAlign: "right" }}>{formatBytes(f.size)}</td>
                          <td>
                            <button onClick={() => downloadFolderFile(folderModal.project, f.path)}>Download</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="modal-actions">
                <button onClick={closeFolderModal}>Close</button>
              </div>
            </div>
          </div>
        ) : null}

        {editVcfOpen ? (
          <div className="modal-backdrop">
            <div className="modal">
              <h3>Edit VCF</h3>
              <div className="note">
                Editing sample: <strong>{editVcfSample}</strong>
                {editVcfProject || selectedProject ? (
                  <span> (Project: {editVcfProject || selectedProject})</span>
                ) : null}
              </div>
              <label className="label">Locus (contig:pos)</label>
              <input
                placeholder="MTBC0:123456"
                value={editVcfLocus}
                onChange={(e) => setEditVcfLocus(e.target.value)}
              />
              <div className="inline-fields">
                <button className="ghost" onClick={fetchCurrentVcfCall}>Fetch current</button>
                {editVcfCurrent ? (
                  <span className="muted">
                    Current: {editVcfCurrent.ref} → {editVcfCurrent.alt}
                  </span>
                ) : null}
              </div>
              <label className="label">Correct ALT</label>
              <input
                placeholder="A/C/G/T/N"
                value={editVcfAlt}
                onChange={(e) => setEditVcfAlt(e.target.value.toUpperCase())}
              />
              <label className="label">Reason</label>
              <select value={editVcfReason} onChange={(e) => setEditVcfReason(e.target.value)}>
                <option value="">Select reason</option>
                <option value="low_support">Low support in IGV</option>
                <option value="mixed_signal">Mixed signal</option>
                <option value="mapping_issue">Mapping issue</option>
                <option value="contamination">Contamination/mixture</option>
                <option value="other">Other</option>
              </select>
              <label className="label">Note (optional)</label>
              <input
                placeholder="Reason for edit"
                value={editVcfNote}
                onChange={(e) => setEditVcfNote(e.target.value)}
              />
              <div className="modal-actions">
                <button className="ghost" onClick={() => setEditVcfOpen(false)}>Cancel</button>
                <button onClick={submitEditVcf}>Apply</button>
              </div>
            </div>
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
                  VCF Databases (Step 2)
                  <span
                    className="help-icon"
                    data-tooltip="Select the reference type, then add one or more VCF database folders. All subfolders are searched for *_zc.vcf and *_zc.vcf.gz. Folders are saved per-machine."
                  >
                    ?
                  </span>
                </h3>
                <select
                  value={importReference}
                  onChange={(e) => setImportReference(e.target.value)}
                >
                  <option value="">Select reference</option>
                  {references.map((r) => (
                    <option key={r.name} value={r.name}>{r.name}</option>
                  ))}
                </select>
                <div style={{marginTop:"8px"}}>
                  {vcfDbFolders.length ? (
                    <div style={{display:"flex", flexDirection:"column", gap:"4px", marginBottom:"8px"}}>
                      {vcfDbFolders.map((folder, i) => (
                        <div key={i} style={{display:"flex", alignItems:"center", gap:"6px", padding:"4px 8px", background:"var(--panel-2)", border:"1px solid var(--border)", borderRadius:"8px", fontSize:"12px"}}>
                          <input
                            type="checkbox"
                            checked={folder.enabled !== false}
                            onChange={() => toggleVcfDbFolder(i)}
                            style={{width:"auto"}}
                          />
                          <span
                            title={folder.path}
                            style={{flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", opacity: folder.enabled !== false ? 1 : 0.5}}
                          >
                            {folder.path.split("/").pop() || folder.path}
                          </span>
                          <button
                            className="chip-remove"
                            onClick={() => removeVcfDbFolder(i)}
                            title="Remove folder"
                          >
                            x
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="muted" style={{fontSize:"12px", marginBottom:"8px"}}>No VCF database folders configured.</div>
                  )}
                  <div style={{display:"flex", flexDirection:"column", gap:"4px"}}>
                    <div style={{display:"flex", gap:"4px", alignItems:"center"}}>
                      {canPickPath ? (
                        <button
                          className="ghost action"
                          style={{fontSize:"12px"}}
                          onClick={async () => {
                            const picked = await window.vsnp.selectPath({
                              kind: "folder",
                              title: "Select VCF database folder"
                            });
                            if (picked) {
                              await addVcfDbFolder(picked);
                            }
                          }}
                        >
                          Browse
                        </button>
                      ) : null}
                      <input
                        type="text"
                        value={manualVcfFolderPath}
                        onChange={(e) => setManualVcfFolderPath(e.target.value)}
                        placeholder="/path/to/VCF_REFS/folder"
                        title="To find a path: In Finder, right-click a folder → Get Info → copy 'Where' path, then add the folder name"
                        style={{flex:1, fontSize:"12px"}}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && manualVcfFolderPath.trim()) {
                            addVcfDbFolder(manualVcfFolderPath.trim());
                            setManualVcfFolderPath("");
                          }
                        }}
                      />
                      <button
                        className="ghost action"
                        onClick={() => {
                          if (manualVcfFolderPath.trim()) {
                            addVcfDbFolder(manualVcfFolderPath.trim());
                            setManualVcfFolderPath("");
                          }
                        }}
                        disabled={!manualVcfFolderPath.trim()}
                      >
                        Add
                      </button>
                    </div>
                    <div className="muted" style={{fontSize:"11px"}}>
                      {canPickPath
                        ? "Use Browse to select a folder, or type a path and click Add"
                        : "Tip: In Finder, right-click folder \u2192 Get Info \u2192 copy the path from \"Where\""}
                    </div>
                  </div>
                </div>
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

            <details className="step2-options-panel">
              <summary style={{cursor:"pointer", fontWeight:500, fontSize:"0.9em"}}>Step 2 Options</summary>
              <div className="step2-options-grid">
                <div className="step2-options-col">
                  <label className="checkbox">
                    <input type="checkbox" checked={s2AllVcf} onChange={(e) => setS2AllVcf(e.target.checked)} />
                    All VCF table (-a)
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={s2NoFilters} onChange={(e) => setS2NoFilters(e.target.checked)} />
                    No filters (-n)
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={s2FindNewFilters} onChange={(e) => setS2FindNewFilters(e.target.checked)} />
                    Find new filters (-i)
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={s2HashGroups} onChange={(e) => setS2HashGroups(e.target.checked)} />
                    Hash groups (-hash)
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={s2ShowGroups} onChange={(e) => setS2ShowGroups(e.target.checked)} />
                    Show groups in table (--show_groups)
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={s2HtmlTree} onChange={(e) => setS2HtmlTree(e.target.checked)} />
                    HTML tree (-html_tree)
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={s2LabelStyle === "rich"}
                      onChange={(e) => setS2LabelStyle(e.target.checked ? "rich" : "short")}
                    />
                    Use rich labels for VCF refs (labeled trees)
                  </label>
                  <label className="checkbox">
                    <input type="checkbox" checked={s2Dp} onChange={(e) => setS2Dp(e.target.checked)} />
                    Avg depth in tables (-dp)
                  </label>
                </div>
                <div className="step2-options-col">
                  <label className="option-field">
                    <span>QUAL threshold (-w)</span>
                    <input type="number" value={s2QualThreshold} onChange={(e) => setS2QualThreshold(parseInt(e.target.value, 10) || 0)} style={{width:"5em"}} />
                    <span className="muted" style={{fontSize:"0.8em"}}>default: 150</span>
                  </label>
                  <label className="option-field">
                    <span>N threshold (-x)</span>
                    <input type="number" value={s2NThreshold} onChange={(e) => setS2NThreshold(parseInt(e.target.value, 10) || 0)} style={{width:"5em"}} />
                    <span className="muted" style={{fontSize:"0.8em"}}>default: 50</span>
                  </label>
                  <label className="option-field">
                    <span>MQ threshold (-y)</span>
                    <input type="number" value={s2MqThreshold} onChange={(e) => setS2MqThreshold(parseInt(e.target.value, 10) || 0)} style={{width:"5em"}} />
                    <span className="muted" style={{fontSize:"0.8em"}}>default: 56</span>
                  </label>
                  <label className="option-field">
                    <span>Density threshold</span>
                    <input type="number" value={s2DensityThreshold} placeholder="3" onChange={(e) => setS2DensityThreshold(e.target.value)} style={{width:"5em"}} />
                    <span className="muted" style={{fontSize:"0.8em"}}>default: 3</span>
                  </label>
                  <label className="option-field">
                    <span>Density window (bp)</span>
                    <input type="number" value={s2DensityWindow} placeholder="20" onChange={(e) => setS2DensityWindow(e.target.value)} style={{width:"5em"}} />
                    <span className="muted" style={{fontSize:"0.8em"}}>default: 20</span>
                  </label>
                  <label className="option-field">
                    <span>Bootstrap (replicates)</span>
                    <input type="number" min="0" value={s2Bootstrap} placeholder="0" onChange={(e) => setS2Bootstrap(e.target.value)} style={{width:"5em"}} />
                    <span className="muted" style={{fontSize:"0.8em"}}>0 = off · 100 typical · RAxML -f a</span>
                  </label>
                </div>
              </div>
            </details>

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
              {step2EditedCount > 0 ? (
                <div className="note warning">
                  Edited samples detected: {step2EditedCount}. Step 2 will use patched VCFs when available.
                </div>
              ) : step1EditedCount > 0 ? (
                <div className="note warning">
                  Edited samples available: {step1EditedCount}. Rebuild the Step 2 VCF set to include them.
                </div>
              ) : null}
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
            {step2EditedCount > 0 && settings.projects_root && selectedProject ? (
              <div className="note">
                Edited samples included in this run.{" "}
                <button
                  className="link-button"
                  onClick={() =>
                    openOutput(`${settings.projects_root}/${selectedProject}/step2/edited_samples.json`)
                  }
                >
                  View edited sample list
                </button>
              </div>
            ) : null}
            {step2RunId ? <div className="note">Run ID: {step2RunId}</div> : null}
            {step2OutputsError ? <div className="note error">{step2OutputsError}</div> : null}
            {(() => {
              const shouldHideOutput = (item) => {
                const path = item?.path || "";
                return path.includes("_labeled_labeled");
              };
              const filteredStep2Outputs = step2Outputs.filter((item) => !shouldHideOutput(item));
              const filteredStep2Groups = step2Groups
                .map((group) => ({
                  ...group,
                  files: (group.files || []).filter((item) => !shouldHideOutput(item)),
                }))
                .filter((group) => group.files.length);
              const groupCount = filteredStep2Groups.reduce((sum, g) => sum + (g.files?.length || 0), 0);
              const totalCount = filteredStep2Outputs.length + groupCount;
              const sortedStep2Outputs = filteredStep2Outputs.slice().sort((a, b) => {
                if (s2AllVcf) {
                  const aIsAll = /-all$/i.test(a.label || "");
                  const bIsAll = /-all$/i.test(b.label || "");
                  if (aIsAll !== bIsAll) return aIsAll ? -1 : 1;
                }
                return (a.label || "").localeCompare(b.label || "");
              });
              const sortedStep2Groups = filteredStep2Groups.slice().sort((a, b) => {
                if (s2AllVcf) {
                  const aIsAll = /-all$/i.test(a.name || "");
                  const bIsAll = /-all$/i.test(b.name || "");
                  if (aIsAll !== bIsAll) return aIsAll ? -1 : 1;
                }
                return (a.name || "").localeCompare(b.name || "");
              });
              return (
                <>
                  <div className="results-list">
                  {sortedStep2Outputs.length ? (
                    sortedStep2Outputs.map((item) => (
                      <div key={item.path} className="results-item">
                        <div className="results-main">
                      <div className="results-name">{item.label}</div>
                      <div className="results-path">{item.path}</div>
                    </div>
                    <div className="results-actions">
                      <button onClick={() => openOutput(item.path)}>Open</button>
                      <button onClick={() => downloadOutput(item.path)} title="Download file">DL</button>
                    </div>
                  </div>
                ))
              ) : null}
              {sortedStep2Groups.length ? (
                <div className="results-groups">
                  {sortedStep2Groups.map((group) => (
                    <details key={group.name} className="results-group">
                      <summary>{group.name}</summary>
                      {group.files.map((item) => {
                        const isTre = (item.label || "").toLowerCase().endsWith(".tre");
                        const treeBase = window.location.pathname.replace(/[^/]*$/, "");
                        const ptUrl = isTre ? `${treeBase}?view=tree-pt&project=${encodeURIComponent(selectedProject)}&path=${encodeURIComponent(item.path)}` : "";
                        const pcUrl = isTre ? `${treeBase}?view=tree-pc&project=${encodeURIComponent(selectedProject)}&path=${encodeURIComponent(item.path)}` : "";
                        return (
                          <div key={item.path} className="results-item">
                            <div className="results-main">
                              <div className="results-name">{item.label}</div>
                              <div className="results-path">{item.path}</div>
                            </div>
                            <div className="results-actions">
                              {isTre ? (
                                <>
                                  <button onClick={() => window.open(ptUrl, "_blank", "noopener")} title="Spike: phylotree.js">phylotree</button>
                                  <button onClick={() => window.open(pcUrl, "_blank", "noopener")} title="Spike: phylocanvas.gl">phylocanvas</button>
                                </>
                              ) : null}
                              <button onClick={() => openOutput(item.path)}>Open</button>
                              <button onClick={() => downloadOutput(item.path)} title="Download file">DL</button>
                            </div>
                          </div>
                        );
                      })}
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
                logs.length ? logs.map((l, i) => {
                  let cls = "";
                  if (l.includes("[MISSING]") || l.includes("[DEPENDENCY_ERROR]") || l.includes("[FAILED]")) cls = "log-error";
                  else if (l.includes("[OK]")) cls = "log-success";
                  return <div key={i} className={cls}>{l}</div>;
                }) : <div>Waiting for output...</div>
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
