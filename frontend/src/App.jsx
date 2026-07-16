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
    bcftools_path: "",
    step1_max_parallel: 3,
    sra_allow_insecure_https: false
  });
  // Server-side folder browser for picking the projects root.
  const [folderBrowser, setFolderBrowser] = useState({ open: false, path: "", parent: null, entries: [], loading: false, error: "" });
  const [sraText, setSraText] = useState("");
  const [sraFolder, setSraFolder] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [reference, setReference] = useState("");
  const [debugMode, setDebugMode] = useState(false);
  const [assembleUnmap, setAssembleUnmap] = useState(false);
  const [nanoporeMode, setNanoporeMode] = useState(false);
  const [forceRerun, setForceRerun] = useState(false);   // re-align samples already Complete
  const [jobId, setJobId] = useState("");
  const [jobStatus, setJobStatus] = useState("idle");
  const [logs, setLogs] = useState([]);
  const [uploadStatus, setUploadStatus] = useState("");
  const [inputs, setInputs] = useState({ files: [], total_bytes: 0, count: 0 });
  const [inputsLoading, setInputsLoading] = useState(false);
  // Project list expands inline to show its samples (Kraken-GUI-style layout).
  const [projExpanded, setProjExpanded] = useState({});   // project -> bool
  const [projData, setProjData] = useState({});           // project -> {loading, samples:[grouped], krakenDirs:[]}
  const [sampleKrakenOpen, setSampleKrakenOpen] = useState({});   // "proj::sample" -> bool (row expanded)
  const [sampleKrakenFiles, setSampleKrakenFiles] = useState({}); // "proj::sample" -> {loading,present,files}
  const [sampleStep1Files, setSampleStep1Files] = useState({});   // "proj::sample" -> {loading,files} (vSNP Step 1/2 outputs)
  const [copiedPath, setCopiedPath] = useState("");               // last path copied (for transient "Copied" hint)
  const [folderPickerMode, setFolderPickerMode] = useState("dropdown"); // "dropdown" | "custom"
  const [qcRows, setQcRows] = useState([]);
  // project name -> [kraken output dir names]. Lets the Step 1 results table
  // tell which samples already have a Kraken run (and so a Krona graph to open)
  // without a per-row request. Populated by loadProjectKrakenDirs().
  const [krakenDirsByProject, setKrakenDirsByProject] = useState({});
  const [qcLoading, setQcLoading] = useState(false);
  const [qcError, setQcError] = useState("");
  const [showFlaggedOnly, setShowFlaggedOnly] = useState(false);
  const [qcFilter, setQcFilter] = useState("");                   // as-you-type sample filter for Step 1 Results
  const [showAllInputs, setShowAllInputs] = useState(false);      // download list: false = last 7 days only, true = all
  const [qcDateStart, setQcDateStart] = useState("");             // YYYY-MM-DD; filter Step 1 Results by run date (inclusive)
  const [qcDateEnd, setQcDateEnd] = useState("");                 // YYYY-MM-DD; empty end = open-ended
  const [projSampleFilter, setProjSampleFilter] = useState("");   // as-you-type sample filter for the Projects list
  const [excluded, setExcluded] = useState({});
  const [step1Status, setStep1Status] = useState([]);
  // Batch-level status from /step1/status (returned alongside per-sample
  // statuses). "running" disables the Run button so clicks can't spawn a
  // second concurrent batch. Empty string when no batch has ever been
  // run for this project.
  const [step1JobStatus, setStep1JobStatus] = useState("");
  const [step1StatusError, setStep1StatusError] = useState("");
  // True while a stop request is in flight, to disable the Stop button and
  // show feedback (the batch can take a few seconds to tear down).
  const [step1Stopping, setStep1Stopping] = useState(false);
  const [step1LogSample, setStep1LogSample] = useState("");
  const [step1LogText, setStep1LogText] = useState("");
  const [step1LogLoading, setStep1LogLoading] = useState(false);
  const [step1FilesCache, setStep1FilesCache] = useState({});
  const [openStep1FilesRow, setOpenStep1FilesRow] = useState("");
  const [folderModal, setFolderModal] = useState({ open: false, project: "", sample: "", files: [], sampleDir: "", loading: false, error: "", krakenPresent: false, krakenFiles: [], krakenDir: "" });
  // Run Kraken ID Parse on a single sample, launched from Step 1. mode is
  // "full" (classify + parse reads + identify) or "kraken_only" (Kraken2 +
  // Krona graph only). Streams the live pipeline log via /api/jobs/{id}/events.
  const [krakenModal, setKrakenModal] = useState({
    open: false, project: "", sample: "", mode: "full", taxon: "",
    running: false, jobId: null, status: "idle", log: [],
  });
  // Taxon presets are loaded from the shared kraken config/taxa.yaml via
  // /api/kraken/taxa; this list is only a fallback if that fetch fails.
  const [krakenTaxonPresets, setKrakenTaxonPresets] = useState([
    "Mycobacterium tuberculosis complex",
    "Mycobacterium bovis",
    "Mycobacterium avium subsp. paratuberculosis",
    "Brucella",
  ]);
  const [krakenNewTaxon, setKrakenNewTaxon] = useState("");
  const [krakenAddingTaxon, setKrakenAddingTaxon] = useState(false);
  const [igvPanel, setIgvPanel] = useState({ open: false, project: "", referenceFastaPath: "", referenceFaiPath: "", tracks: [], status: "", height: 45, fullscreen: false });
  const [igvPopoutOpen, setIgvPopoutOpen] = useState(false);
  const igvBrowserRef = useRef(null);
  const igvContainerRef = useRef(null);
  const igvPopoutRef = useRef(null);
  const uploadInputRef = useRef(null);
  // Synchronous re-entry guard for Step 1 dispatch. step1JobStatus only flips
  // to "running" after the request resolves, so a same-tick double-click would
  // otherwise fire two dispatches; this ref blocks the second instantly.
  const step1DispatchingRef = useRef(false);
  const excludeSaveTimerRef = useRef(null);
  const step2BuildExcludeTimerRef = useRef(null);
  const uploadXhrRef = useRef(null);
  const qcRowsRef = useRef([]);
  const excludedRef = useRef({});
  const step1JobStatusRef = useRef("");
  const krakenEsRef = useRef(null);
  const krakenLogRef = useRef(null);
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
  const [projectReference, setProjectReference] = useState("");
  const [newProjectReference, setNewProjectReference] = useState("");
  const [step2Runs, setStep2Runs] = useState([]);
  const [step2SelectedRun, setStep2SelectedRun] = useState(null);
  const [step2Outputs, setStep2Outputs] = useState([]);
  const [step2Groups, setStep2Groups] = useState([]);
  const [step2OutputsError, setStep2OutputsError] = useState("");
  const [step2EditedCount, setStep2EditedCount] = useState(0);
  const [posthocTools, setPosthocTools] = useState([]);
  const [posthocStatus, setPosthocStatus] = useState({});
  const [posthocRunError, setPosthocRunError] = useState("");
  const [posthocScopeByGroup, setPosthocScopeByGroup] = useState({});
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
  const [vcfSourceSamples, setVcfSourceSamples] = useState([]);
  const [vcfSourceFilter, setVcfSourceFilter] = useState("");
  const [vcfSourceOpen, setVcfSourceOpen] = useState(false);
  // Step 2 build-list exclusions (separate from Step 1 QC exclusions): a map
  // of {sampleName: true} for samples checked "Exclude" in the Build VCF set
  // list, plus a sample->display-label map for showing reference metadata.
  const [step2BuildExcluded, setStep2BuildExcluded] = useState({});
  // Samples already excluded in Step 1 Results (remove_from_analysis.xlsx).
  // The Step 2 run unions these with the build-list exclusions anyway, so we
  // surface them in the build list as pre-checked + locked to make the build
  // honest about what will actually be compared. Change them in Step 1 Results.
  const [step2QcExcluded, setStep2QcExcluded] = useState({});
  const [step2BuildMeta, setStep2BuildMeta] = useState({});
  // Step 2 Results group search: {groupName: [sample names]} parsed from the
  // run summary HTML, and the live (case-insensitive) search text.
  const [step2Groupings, setStep2Groupings] = useState({});
  const [step2GroupSearch, setStep2GroupSearch] = useState("");
  const [vcfDbFolders, setVcfDbFolders] = useState([]);
  const [vcfDbDropdownOpen, setVcfDbDropdownOpen] = useState(false);
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
  const [genomeDisplayName, setGenomeDisplayName] = useState("");
  const [genomeOutputDir, setGenomeOutputDir] = useState("");
  const [genomeDownloadStatus, setGenomeDownloadStatus] = useState("");
  const [genomeJobId, setGenomeJobId] = useState("");
  const [step1JobId, setStep1JobId] = useState("");
  const [step2JobId, setStep2JobId] = useState("");
  // Item 5: SRA download feedback
  const [sraJobId, setSraJobId] = useState("");
  const [sraStatus, setSraStatus] = useState("");
  // Item 6: Reference Editor
  const [refEditorRef, setRefEditorRef] = useState("");
  const [refEditorFiles, setRefEditorFiles] = useState([]);
  const [refEditorPath, setRefEditorPath] = useState("");
  const [metaRows, setMetaRows] = useState([]);
  const [metaFilename, setMetaFilename] = useState(null);
  const [metaExists, setMetaExists] = useState(false);
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaSingleOrig, setMetaSingleOrig] = useState("");
  const [metaSingleDisplay, setMetaSingleDisplay] = useState("");
  const [metaBulkText, setMetaBulkText] = useState("");
  const [metaBulkOpen, setMetaBulkOpen] = useState(false);
  const [metaStatus, setMetaStatus] = useState("");
  // Reference editor: add a defining-SNP group / add a remove-from-analysis sample
  const [dfGroupName, setDfGroupName] = useState("");
  const [dfPositions, setDfPositions] = useState("");
  const [dfStatus, setDfStatus] = useState("");
  const [rmSampleText, setRmSampleText] = useState("");
  const [rmStatus, setRmStatus] = useState("");
  // _VCFs accumulation folder
  const [vcfsFolderCount, setVcfsFolderCount] = useState(0);
  const [vcfsFolderPath, setVcfsFolderPath] = useState("");
  const [vcfsFolderName, setVcfsFolderName] = useState("");
  const [vcfsFolderSamples, setVcfsFolderSamples] = useState([]);
  const [vcfsCollectResult, setVcfsCollectResult] = useState(null);
  const [vcfsForceSet, setVcfsForceSet] = useState(new Set());
  const [vcfsIncludeFolder, setVcfsIncludeFolder] = useState(true);

  const canPickPath = typeof window !== "undefined" && window.vsnp?.selectPath;

  // Mirror qcRows / excluded into refs so the debounced auto-save reads
  // current state at fire time, not the stale closure capture from when the
  // timer was scheduled.
  useEffect(() => { qcRowsRef.current = qcRows; }, [qcRows]);
  // Keep the Kraken run modal's log scrolled to the latest line.
  useEffect(() => {
    const el = krakenLogRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [krakenModal.log]);
  useEffect(() => { excludedRef.current = excluded; }, [excluded]);

  const settingsReady = Boolean(
    settings.vsnp3_path && settings.projects_root
  );

  const sampleKey = (row) => row?._sample || row?.sample || (row?._file ? row._file.split("/").pop() : "");
  const excludeKey = (row) => row?._file || sampleKey(row);
  // Run date as YYYY-MM-DD. _run_date is the backend's authoritative run
  // timestamp (run_metadata.json started_at, falling back to the stats date /
  // filename / mtime). Both ISO (2026-05-16T..) and filename (2026-05-16_09..)
  // forms start with the date, so slicing 10 chars normalizes either one.
  const qcRunDate = (row) => String(row?._run_date || "").slice(0, 10);

  // Apply the Step 1 Results filters (flagged-only + name + run-date range) in
  // one place so the table body and the "exclude all" header act on the exact
  // same set of rows. Cheap to recompute; called per render.
  function computeVisibleQcRows() {
    const q = qcFilter.trim().toLowerCase();
    const ds = qcDateStart;
    const de = qcDateEnd;
    return qcRows
      .filter((r) => !showFlaggedOnly || isFlagged(r))
      .filter((r) => !q || String(r._sample || r.sample || "").toLowerCase().includes(q))
      .filter((r) => {
        if (!ds && !de) return true;
        const rd = qcRunDate(r);
        if (!rd) return false;
        if (ds && rd < ds) return false;
        if (de && rd > de) return false;
        return true;
      });
  }

  // Aggregate state for the header "exclude all in current view" checkbox.
  function excludeAllState() {
    const vis = computeVisibleQcRows();
    const total = vis.length;
    const on = vis.reduce((n, r) => n + (excluded[excludeKey(r)] ? 1 : 0), 0);
    return { total, on, checked: total > 0 && on === total, indeterminate: on > 0 && on < total };
  }

  // Compact "last activity" label for the projects list (today / 3d ago / date).
  function _formatActivity(iso) {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return "";
    const days = Math.floor((Date.now() - t) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 30) return `${days}d ago`;
    return iso.slice(0, 10);
  }

  // Local-date YYYY-MM-DD (not UTC) so "Today" matches the user's calendar day.
  function _isoDay(d) {
    const tzOffset = d.getTimezoneOffset() * 60000;
    return new Date(d.getTime() - tzOffset).toISOString().slice(0, 10);
  }
  // Quick range: last N days through today (inclusive). N=1 → just today.
  function setQcRangeDays(n) {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - (n - 1));
    setQcDateStart(_isoDay(start));
    setQcDateEnd(_isoDay(end));
  }
  function clearQcDates() {
    setQcDateStart("");
    setQcDateEnd("");
  }

  // Select / deselect Exclude for every row currently visible (honors the
  // active flagged/name/date filters). Reuses the same debounced save as the
  // per-row toggle so a bulk change still POSTs once.
  function toggleExcludeAllVisible(checked) {
    const vis = computeVisibleQcRows();
    setExcluded((prev) => {
      const next = { ...prev };
      vis.forEach((row) => { next[excludeKey(row)] = checked; });
      return next;
    });
    if (excludeSaveTimerRef.current) clearTimeout(excludeSaveTimerRef.current);
    excludeSaveTimerRef.current = setTimeout(() => {
      excludeSaveTimerRef.current = null;
      _persistExclusions();
    }, 400);
  }

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

  // The post-hoc table can mix samples from several projects; make sure each
  // referenced project's Kraken dir list is cached so its rows can show a
  // Krona button. Only fetches projects we haven't loaded yet.
  useEffect(() => {
    const projs = new Set(posthocFilteredRows.map((r) => r._project).filter(Boolean));
    projs.forEach((p) => {
      if (!(p in krakenDirsByProject)) loadProjectKrakenDirs(p);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [posthocFilteredRows]);

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

  async function addVcfDbFolder(path, reference) {
    if (!reference) {
      alert("Select a reference before adding a custom VCF folder — VCFs are reference-dependent.");
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/vcf-db-folders`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add", path, reference })
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

  async function removeVcfDbFolder(path) {
    const res = await fetch(`${API_BASE}/api/vcf-db-folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "remove", path })
    });
    if (res.ok) {
      const data = await res.json();
      setVcfDbFolders(data || []);
    }
  }

  async function toggleVcfDbFolder(path) {
    const res = await fetch(`${API_BASE}/api/vcf-db-folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "toggle", path })
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
    const finalDisplayName = genomeDisplayName.trim();
    setGenomeDownloadStatus("Starting download...");
    const res = await fetch(`${API_BASE}/api/references/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accession: genomeAccession.trim(),
        output_dir: finalDir,
        display_name: finalDisplayName || null,
      })
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

  async function loadMetadata(refName) {
    if (!refName) { setMetaRows([]); setMetaFilename(null); setMetaExists(false); return; }
    setMetaLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(refName)}/metadata`);
      if (res.ok) {
        const data = await res.json();
        setMetaRows(data.rows || []);
        setMetaFilename(data.filename || null);
        setMetaExists(data.exists || false);
      }
    } finally {
      setMetaLoading(false);
    }
  }

  async function addMetadataRows(rows) {
    if (!refEditorRef || !rows.length) return;
    setMetaStatus("Saving…");
    const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(refEditorRef)}/metadata/add-rows`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows })
    });
    if (res.ok) {
      const data = await res.json();
      setMetaStatus(`Saved — ${data.added} added, ${data.updated} updated (${data.rows_total} total)`);
      await loadMetadata(refEditorRef);
    } else {
      const err = await res.json().catch(() => ({}));
      setMetaStatus(`Error: ${err.detail || res.status}`);
    }
  }

  // Add a defining-SNP group (group name + one or more chrom:pos positions)
  // permanently to the reference's *_define_filter.xlsx. Prompts for a
  // rationale (audited + the prior file archived, like Replace).
  async function addDefineFilterGroup() {
    if (!refEditorRef) return;
    const group = dfGroupName.trim();
    const positions = dfPositions
      .split(/[\s,;]+/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (!group) { setDfStatus("Enter a group name."); return; }
    if (!positions.length) { setDfStatus("Enter at least one position (chrom:position)."); return; }
    const rationale = window.prompt(
      `Rationale for adding group "${group}" (${positions.length} position(s)) to the define_filter file? Required.`
    );
    if (!rationale || !rationale.trim()) { setDfStatus("Cancelled — rationale is required."); return; }
    setDfStatus("Saving…");
    const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(refEditorRef)}/define-filter/add-group`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group, positions, rationale: rationale.trim() }),
    });
    if (res.ok) {
      const data = await res.json();
      setDfStatus(`Added group "${data.group}" with ${data.added} position(s): ${(data.positions || []).join(", ")}`);
      setDfGroupName("");
      setDfPositions("");
      await loadRefEditorFiles(refEditorRef);
    } else {
      const err = await res.json().catch(() => ({}));
      setDfStatus(`Error: ${err.detail || res.status}`);
    }
  }

  // Add sample name(s) to the reference's *_remove_from_analysis.xlsx
  // (one per line / comma-separated). Audited + archived like Replace.
  async function addRemoveSample() {
    if (!refEditorRef) return;
    const samples = rmSampleText
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!samples.length) { setRmStatus("Enter at least one sample name."); return; }
    const rationale = window.prompt(
      `Rationale for adding ${samples.length} sample name(s) to the remove_from_analysis file? Required.`
    );
    if (!rationale || !rationale.trim()) { setRmStatus("Cancelled — rationale is required."); return; }
    setRmStatus("Saving…");
    const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(refEditorRef)}/remove-from-analysis/add-sample`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples, rationale: rationale.trim() }),
    });
    if (res.ok) {
      const data = await res.json();
      const addedTxt = (data.added || []).length ? `added ${(data.added || []).join(", ")}` : "nothing added";
      const skipTxt = (data.skipped || []).length ? `; skipped (already present): ${(data.skipped || []).join(", ")}` : "";
      setRmStatus(`${addedTxt}${skipTxt}`);
      setRmSampleText("");
      await loadRefEditorFiles(refEditorRef);
    } else {
      const err = await res.json().catch(() => ({}));
      setRmStatus(`Error: ${err.detail || res.status}`);
    }
  }

  // Open the xlsx in a formatted in-browser preview tab. Replaces the old
  // "Edit in Spreadsheet App" POST -> _open_path(xdg-open) flow, which was
  // a desktop-app launcher and silently no-ops in an OOD session (no display
  // on the container side; user is on a remote browser).
  function viewRefFile(refName, filename) {
    if (!refName || !filename) return;
    const url = `${API_BASE}/api/references/${encodeURIComponent(refName)}/preview-xlsx?filename=${encodeURIComponent(filename)}`;
    window.open(url, "_blank", "noopener");
  }

  // Download the raw xlsx so the user can edit offline (Excel / LibreOffice
  // / Numbers). Pair with Replace to close the offline-edit loop.
  function downloadRefFile(refName, filename) {
    if (!refName || !filename) return;
    const url = `${API_BASE}/api/references/${encodeURIComponent(refName)}/download-file?filename=${encodeURIComponent(filename)}`;
    window.open(url, "_blank", "noopener");
  }

  // T-39: re-upload an edited reference xlsx to replace in place. Opens a
  // hidden file picker, prompts for a rationale, posts multipart to the
  // upload endpoint. The backend enforces filename whitelist, size cap,
  // atomic write, and archives the old file under <ref>/.history/.
  function replaceRefFile(refName, expectedFilename, onReplaced) {
    if (!refName || !expectedFilename) return;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".xlsx";
    input.style.display = "none";
    document.body.appendChild(input);
    input.addEventListener("change", async () => {
      try {
        const picked = input.files && input.files[0];
        if (!picked) return;
        if (picked.name !== expectedFilename) {
          const ok = window.confirm(
            `The file you picked is named "${picked.name}" but the slot expects "${expectedFilename}". ` +
            `Continue anyway? The server will reject it if the filename doesn't match the allowed pattern.`
          );
          if (!ok) return;
        }
        const rationale = window.prompt(
          `Rationale for replacing ${expectedFilename}? ` +
          `(e.g. "added 12 newly characterised positions from Lingling 2026 panel"). Required.`
        );
        if (!rationale || !rationale.trim()) {
          window.alert("Replace cancelled — rationale is required.");
          return;
        }
        const fd = new FormData();
        fd.append("file", picked, picked.name);
        const url = `${API_BASE}/api/references/${encodeURIComponent(refName)}/upload-file?rationale=${encodeURIComponent(rationale.trim())}`;
        const res = await fetch(url, { method: "POST", body: fd });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          window.alert(`Replace failed: ${detail.detail || res.status}`);
          return;
        }
        const data = await res.json();
        window.alert(
          `Replaced ${data.filename}.\n` +
          `new sha256: ${data.new_sha256.slice(0, 12)}…\n` +
          `old archived: ${data.archived_old || "(no previous file)"}\n` +
          `audit log: ${data.audit_log}`
        );
        // Reload the reference editor's file list so the row reflects the new file.
        await loadRefEditorFiles(refName);
        // Slots with a separate preview loader (e.g. metadata) pass a refresh hook.
        if (typeof onReplaced === "function") await onReplaced();
      } finally {
        input.remove();
      }
    });
    input.click();
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

  async function loadVcfsFolder(project) {
    if (!project) {
      setVcfsFolderCount(0); setVcfsFolderPath(""); setVcfsFolderName(""); setVcfsFolderSamples([]);
      return;
    }
    const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/vcfs`);
    if (res.ok) {
      const data = await res.json();
      setVcfsFolderCount(data.count || 0);
      setVcfsFolderPath(data.path || "");
      setVcfsFolderName(data.folder_name || "");
      setVcfsFolderSamples(data.samples || []);
    }
  }

  async function collectVcfs(forceSamples) {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/vcfs/collect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force_samples: forceSamples || [] })
    });
    if (res.ok) {
      const data = await res.json();
      setVcfsCollectResult(data);
      await loadVcfsFolder(selectedProject);
      await refreshProjects(selectedProject);
    }
  }

  async function loadAll() {
    const [cfg, proj, refs, dbFolders, posthocToolsResp, paths] = await Promise.all([
      fetch(`${API_BASE}/api/config`).then((r) => r.json()),
      fetch(`${API_BASE}/api/projects`).then((r) => r.json()),
      fetch(`${API_BASE}/api/references`).then((r) => r.json()),
      fetch(`${API_BASE}/api/vcf-db-folders`).then((r) => r.json()).catch(() => []),
      fetch(`${API_BASE}/api/posthoc/tools`).then((r) => (r.ok ? r.json() : [])).catch(() => []),
      fetch(`${API_BASE}/api/references/paths`).then((r) => (r.ok ? r.json() : { paths: [] })).catch(() => ({ paths: [] }))
    ]);
    setRefPaths(paths.paths || []);
    setConfig(cfg);
    setProjects(proj);
    setReferences(refs);
    setVcfDbFolders(dbFolders || []);
    setPosthocTools(posthocToolsResp || []);
    setSettings({
      vsnp3_path: cfg.vsnp3_path || "",
      projects_root: cfg.projects_root || "",
      bcftools_path: cfg.bcftools_path || "",
      step1_max_parallel: cfg.step1_max_parallel ?? 3,
      sra_allow_insecure_https: Boolean(cfg.sra?.allow_insecure_https),
      saved_project_roots: Array.isArray(cfg.saved_project_roots) ? cfg.saved_project_roots : []
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

  // Load the shared taxon search names (kraken repo's config/taxa.yaml).
  useEffect(() => {
    fetch(`${API_BASE}/api/kraken/taxa`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Array.isArray(d.taxa) && d.taxa.length) setKrakenTaxonPresets(d.taxa);
      })
      .catch(() => {});
  }, []);

  // Auto-fill the "Download New Reference" output directory from the first
  // configured reference root once we have one. Under OOD the Browse button
  // isn't shown (no Electron bridge), so without this default the user has
  // to type the absolute server path by hand — and the Download button
  // stays disabled until they do. The field remains editable in case they
  // want to point somewhere else.
  useEffect(() => {
    if (!genomeOutputDir && refPaths && refPaths.length > 0) {
      setGenomeOutputDir(refPaths[0]);
    }
  }, [refPaths, genomeOutputDir]);

  useEffect(() => {
    let cancelled = false;
    setReference("");
    setImportReference("");
    setRefLock({ references: [] });
    setProjectReference("");
    setVcfsCollectResult(null);
    setVcfsForceSet(new Set());
    loadVcfsFolder(selectedProject);
    if (!selectedProject) return () => { cancelled = true; };
    // Seed projectReference from the already-loaded projects list (fast, no extra fetch)
    const proj = projects.find((p) => p.name === selectedProject);
    if (proj?.reference) setProjectReference(proj.reference);
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
          // Sync projectReference when inferred from step1 stats
          if (!projectReference) setProjectReference(lock.references[0]);
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
        // Step 1 job reached a terminal state: refresh sample statuses, QC
        // table, project counts, collect VCFs. "cancelled" is included so a
        // user-stopped batch still collects the VCFs of samples that finished
        // before the stop.
        if (step1JobId && jobId === step1JobId && (status === "succeeded" || status === "failed" || status === "cancelled")) {
          loadStep1Status();
          loadQC();
          refreshProjects(selectedProject);
          collectVcfs([]);
        }
        // Step 2 job completed: refresh run list (auto-selects newest → triggers
        // loadStep2Outputs via useEffect([step2SelectedRun])), update project counts
        if (step2JobId && jobId === step2JobId && (status === "succeeded" || status === "failed")) {
          loadStep2Runs(true);
          refreshProjects(selectedProject);
        }
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
    setStep2Runs([]);
    setStep2SelectedRun(null);
    loadStep2Runs(true);
    loadStep2Outputs();
    loadVcfSourceSamples();
    loadInputs(selectedProject);
    setStep2RunId("");
    setStep2BuiltAt("");
    setStep2VcfCount(0);
    setVcfSourceSamples([]);
    setVcfSourceFilter("");
    setVcfSourceOpen(false);
    // Clear the import-sources textarea on project change. Otherwise paths
    // from a previous project's import (a different reference, possibly
    // different shared DBs) survive the switch and get re-injected into
    // the next build via parseAccessions(importSourcesText).
    setImportSourcesText("");
    setImportStatus("");
    setImportMismatchReport("");
  }, [selectedProject]);

  useEffect(() => {
    if (step2Mode !== "custom") return;
    if (importReference && reference !== importReference) {
      setReference(importReference);
    }
  }, [step2Mode, importReference, reference]);

  // Auto-populate importReference from the project-level reference so VCF
  // database filtering and import tagging always use the project's reference
  // without the user having to re-select it in the VCF Databases panel.
  useEffect(() => {
    const projectRef = projectReference || (refLock.references.length === 1 ? refLock.references[0] : "");
    if (projectRef) setImportReference(projectRef);
  }, [projectReference, refLock.references]);

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

  // Poll /step1/status every 5 s while the batch is running. Reliable
  // regardless of whether the SSE connection survives the OOD proxy timeout.
  useEffect(() => {
    if (step1JobStatus !== "running") return;
    const t = setInterval(loadStep1Status, 5000);
    return () => clearInterval(t);
  }, [step1JobStatus, selectedProject]);

  // When step1JobStatus transitions from "running" → terminal, trigger the
  // same refreshes that the SSE handler would have fired. This fires via the
  // polling interval above, so it works even when SSE drops under the proxy.
  useEffect(() => {
    const prev = step1JobStatusRef.current;
    step1JobStatusRef.current = step1JobStatus;
    if (prev === "running" && step1JobStatus !== "running" && step1JobStatus !== "") {
      loadQC();
      refreshProjects(selectedProject);
    }
  }, [step1JobStatus]);

  // Poll the job endpoint directly for step2 completion. SSE connections
  // drop under the OOD proxy for jobs that run longer than the proxy
  // read-timeout (~60 s); polling is the reliable fallback.
  useEffect(() => {
    if (!step2JobId || !selectedProject) return;
    let cancelled = false;
    const poll = async () => {
      if (cancelled) return;
      try {
        const res = await fetch(`${API_BASE}/api/jobs/${step2JobId}`);
        if (!res.ok) return;
        const job = await res.json();
        if (job.status === "succeeded" || job.status === "failed") {
          cancelled = true;
          clearInterval(t);
          loadStep2Runs(true);
          refreshProjects(selectedProject);
        }
      } catch {}
    };
    const t = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(t); };
  }, [step2JobId, selectedProject]);

  useEffect(() => {
    if (!selectedProject) return;
    loadStep2Outputs();
  }, [step2SelectedRun]);

  useEffect(() => {
    if (!selectedProject || !settingsReady) return;
    const hasRunning = Object.values(posthocStatus).some((status) => status?.running);
    if (!hasRunning) return;
    const id = setInterval(() => {
      loadStep2Outputs();
    }, 3000);
    return () => clearInterval(id);
  }, [selectedProject, settingsReady, posthocStatus]);

  async function createProject() {
    if (!newProjectName.trim()) return;
    const body = { name: newProjectName.trim() };
    if (newProjectReference) body.reference = newProjectReference;
    const res = await fetch(`${API_BASE}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      window.alert(`Could not create project: ${detail.detail || res.status}`);
      return;
    }
    setNewProjectName("");
    setNewProjectReference("");
    await loadAll();
  }

  async function setProjectRef(ref) {
    if (!selectedProject) return;
    await fetch(`${API_BASE}/api/projects/${selectedProject}/set_reference`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference: ref })
    });
    setProjectReference(ref);
    setReference(ref);
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

  // --- Project-root folder browser ---------------------------------------
  function browseDirs(path) {
    setFolderBrowser((s) => ({ ...s, loading: true, error: "" }));
    fetch(`${API_BASE}/api/browse-dirs?path=${encodeURIComponent(path || "")}`)
      .then((r) => (r.ok ? r.json() : r.json().then((e) => { throw new Error(e.detail || "Cannot open folder"); })))
      .then((d) => setFolderBrowser((s) => ({ ...s, path: d.path, parent: d.parent, entries: d.entries, loading: false })))
      .catch((err) => setFolderBrowser((s) => ({ ...s, loading: false, error: err.message })));
  }
  function openFolderBrowser() {
    setFolderBrowser({ open: true, path: "", parent: null, entries: [], loading: true, error: "" });
    browseDirs(settings.projects_root || "");
  }
  function chooseFolder() {
    setSettings((s) => ({ ...s, projects_root: folderBrowser.path }));
    setFolderBrowser((s) => ({ ...s, open: false }));
  }

  async function saveSettings() {
    await fetch(`${API_BASE}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        vsnp3_path: settings.vsnp3_path,
        projects_root: settings.projects_root,
        saved_project_roots: settings.saved_project_roots,
        bcftools_path: settings.bcftools_path,
        step1_max_parallel: settings.step1_max_parallel,
        sra: { allow_insecure_https: settings.sra_allow_insecure_https }
      })
    });
    await loadAll();
  }

  // Curated Projects-root bookmarks. Each action persists immediately (POST +
  // reload) so the list sticks without relying on the separate Save button.
  async function persistProjectRoots(next) {
    const merged = { ...settings, ...next };
    setSettings(merged);
    await fetch(`${API_BASE}/api/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        projects_root: merged.projects_root,
        saved_project_roots: merged.saved_project_roots
      })
    });
    await loadAll();
  }
  function saveCurrentLocation() {
    const cur = (settings.projects_root || "").trim();
    const list = settings.saved_project_roots || [];
    if (!cur || list.includes(cur)) return;
    persistProjectRoots({ saved_project_roots: [...list, cur] });
  }
  function removeSavedLocation(path) {
    persistProjectRoots({ saved_project_roots: (settings.saved_project_roots || []).filter((r) => r !== path) });
  }
  function jumpToLocation(path) {
    if (path) persistProjectRoots({ projects_root: path });
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

  // Backend (qc_verdict.py) ships a `_qc_verdict` field on every qc_summary
  // / posthoc row: { level: "pass" | "review" | "fail", reasons: [...] }.
  // "Flagged" — used by the "show only flagged" toggle and the row-tint —
  // means anything that isn't a clean pass.
  function qcLevel(row) {
    return (row && row._qc_verdict && row._qc_verdict.level) || "pass";
  }

  function qcReasons(row) {
    return (row && row._qc_verdict && row._qc_verdict.reasons) || [];
  }

  function isFlagged(row) {
    return qcLevel(row) !== "pass";
  }

  function qcMappingRate(row) {
    // Backend already computed this in _qc_verdict.signals.mapping_rate (=
    // 100 - parsePercent(Unmapped Percent)). Falling back to a local parse
    // keeps the column populated for legacy rows that pre-date T-09 wiring.
    const sig = row?._qc_verdict?.signals?.mapping_rate;
    if (typeof sig === "number") return sig;
    const unmapped = parsePercent(row?.["Unmapped Percent"]);
    return unmapped == null ? null : 100 - unmapped;
  }

  function fmtPercent1(value) {
    return typeof value === "number" ? `${value.toFixed(1)}%` : "-";
  }

  // Strip the trailing " < N% fail threshold" / " < N× pass threshold" suffix
  // so the inline reason under a chip stays terse — the criteria widget above
  // the table already explains the thresholds; here the reader just needs to
  // see which signal tripped (e.g. "mapping rate 58.5%").
  function summarizeQcReason(reason) {
    return reason.replace(/\s*<\s*[\d.]+\s*[%×xX]\s*(pass|review|fail)\s+threshold\s*$/i, "").trim();
  }

  function QcChip({ row }) {
    const level = qcLevel(row);
    const reasons = qcReasons(row);
    return (
      <div className="qc-cell">
        <span
          className={`qc-badge qc-badge-${level}`}
          title={reasons.length ? reasons.join("\n") : "All thresholds met"}
        >
          {level}
        </span>
        {reasons.length > 0 ? (
          <span className="qc-reason" title={reasons.join("\n")}>
            {summarizeQcReason(reasons[0])}
            {reasons.length > 1 ? <span className="qc-reason-more"> +{reasons.length - 1}</span> : null}
          </span>
        ) : null}
      </div>
    );
  }

  // Renders the live QC thresholds (from /api/config) inside a <details>
  // disclosure so users can see exactly what determines pass/review/fail
  // without digging into config.json. Per-project overrides via project.json
  // are not reflected here yet — surfacing the resolved-per-project view
  // would need a /api/projects/<p>/qc_thresholds endpoint we haven't built.
  function QcCriteriaWidget() {
    const t = config?.qc_thresholds;
    if (!t) return null;
    const cov = t.coverage || {};
    const mr = t.mapping_rate || {};
    return (
      <details className="qc-criteria">
        <summary>QC criteria</summary>
        <ul>
          <li>
            <strong>Coverage</strong> (Avg Depth):
            {" "}≥{cov.pass_min}× pass · ≥{cov.review_min}× review · &lt;{cov.review_min}× fail
          </li>
          <li>
            <strong>Mapping rate</strong> (1 − Unmapped %):
            {" "}≥{mr.pass_min}% pass · ≥{mr.review_min}% review · &lt;{mr.review_min}% fail
          </li>
          <li>
            <strong>Contamination</strong> flag (sourmash, when available):
            {" "}{t.contamination_review ? "any positive value forces review" : "ignored"}
          </li>
        </ul>
        <div className="muted">
          Defaults live in <code>~/.config/vsnp_gui/config.json</code> under{" "}
          <code>qc_thresholds</code>; per-project overrides go in the project's{" "}
          <code>project.json</code>.
        </div>
      </details>
    );
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
    // Surface which of these samples already have a Kraken run, so the table
    // can offer a "Krona" button alongside the "Kraken ID" (run) button.
    loadProjectKrakenDirs(selectedProject);
    // Hydrate the exclusion checkboxes from the persisted xlsx so re-opening
    // a project shows what's actually saved on disk (no more silent drift
    // between the GUI's checkbox state and what Step 2 will honor).
    try {
      const exRes = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_exclude`);
      if (exRes.ok) {
        const exData = await exRes.json();
        const samples = new Set(exData.samples || []);
        const map = {};
        data.forEach((row) => {
          const sample = sampleKey(row);
          if (sample && samples.has(sample)) {
            map[excludeKey(row)] = true;
          }
        });
        setExcluded(map);
      }
    } catch (_) {
      // Non-fatal — the table just shows everything unchecked.
    }
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

  async function loadStep2Runs(autoSelectLatest = false) {
    if (!selectedProject) return;
    try {
      const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/runs`);
      if (!res.ok) return;
      const runs = await res.json();
      setStep2Runs(runs);
      if (autoSelectLatest && runs.length > 0) {
        setStep2SelectedRun(runs[0].run_id);
      }
    } catch {
      // keep existing state on network error
    }
  }

  async function loadStep2Outputs() {
    if (!selectedProject) return;
    setStep2OutputsError("");
    const runParam = step2SelectedRun ? `?run_id=${encodeURIComponent(step2SelectedRun)}` : "";
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2_outputs${runParam}`);
    if (!res.ok) {
      const msg = await res.json();
      setStep2OutputsError(msg.detail || "Failed to load Step 2 outputs");
      return;
    }
    const data = await res.json();
    let groups = [];
    if (Array.isArray(data)) {
      setStep2Outputs(data);
      setStep2Groups([]);
    } else {
      setStep2Outputs(data.top || []);
      groups = data.groups || [];
      setStep2Groups(groups);
    }
    loadStep2Groupings();
    const countRes = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/vcf_count`);
    if (countRes.ok) {
      const countData = await countRes.json();
      setStep2VcfCount(countData.count || 0);
      setStep2EditedCount(countData.edited_count || 0);
    } else {
      setStep2EditedCount(0);
    }
    if (groups.length) {
      loadPosthocStatuses(groups);
    } else {
      setPosthocStatus({});
    }
  }

  async function loadVcfSourceSamples() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/vcf_database/samples`);
    if (res.ok) {
      const samples = await res.json();
      setVcfSourceSamples(samples);
      setStep2VcfCount(samples.length);
    }
    loadStep2BuildExclusions();
    loadStep2QcExclusions();
    loadStep2BuildMeta();
  }

  // Pull the Step 1 QC exclusions so the build list can pre-check (and lock)
  // samples the user already dropped in Step 1 Results.
  async function loadStep2QcExclusions() {
    if (!selectedProject) { setStep2QcExcluded({}); return; }
    try {
      const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_exclude`);
      if (res.ok) {
        const data = await res.json();
        const map = {};
        (data.samples || []).forEach((s) => { map[s] = true; });
        setStep2QcExcluded(map);
      }
    } catch (e) { /* best-effort; the build still works without it */ }
  }

  // Step 2 build-list exclusions: a separate, Step-2-only removal set. Hydrate
  // from the backend so checkboxes survive reloads.
  async function loadStep2BuildExclusions() {
    if (!selectedProject) { setStep2BuildExcluded({}); return; }
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/build-exclusions`);
    if (res.ok) {
      const data = await res.json();
      const map = {};
      (data.samples || []).forEach((s) => { map[s] = true; });
      setStep2BuildExcluded(map);
    }
  }

  async function _persistStep2BuildExclusions(map) {
    if (!selectedProject) return;
    const samples = Object.keys(map).filter((k) => map[k]);
    try {
      await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/build-exclusions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ samples }),
      });
    } catch (e) { /* best-effort; debounced retry on next toggle */ }
  }

  function toggleStep2BuildExcluded(sample, checked) {
    setStep2BuildExcluded((prev) => {
      const next = { ...prev, [sample]: checked };
      if (step2BuildExcludeTimerRef.current) clearTimeout(step2BuildExcludeTimerRef.current);
      step2BuildExcludeTimerRef.current = setTimeout(() => {
        step2BuildExcludeTimerRef.current = null;
        _persistStep2BuildExclusions(next);
      }, 400);
      return next;
    });
  }

  // Load the project reference's metadata as a {VCF-stem: display-label} map so
  // the build list can show metadata next to each sample.
  async function loadStep2BuildMeta() {
    const ref = projectReference || reference;
    if (!ref) { setStep2BuildMeta({}); return; }
    const res = await fetch(`${API_BASE}/api/references/${encodeURIComponent(ref)}/metadata`);
    if (res.ok) {
      const data = await res.json();
      const map = {};
      (data.rows || []).forEach((r) => { if (r.original) map[r.original] = r.display_name; });
      setStep2BuildMeta(map);
    }
  }

  // Parse the run's vSNP3 summary into {groupName: [sample names]} so the
  // Step 2 Results search can filter groups by sample (incl. metadata in the
  // name). Loaded alongside Step 2 outputs.
  async function loadStep2Groupings() {
    if (!selectedProject) { setStep2Groupings({}); return; }
    const runParam = step2SelectedRun ? `?run_id=${encodeURIComponent(step2SelectedRun)}` : "";
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/groupings${runParam}`);
    if (res.ok) {
      const data = await res.json();
      setStep2Groupings(data.groups || {});
    } else {
      setStep2Groupings({});
    }
  }

  async function loadPosthocStatuses(groups) {
    if (!selectedProject) return;
    const statusMap = {};
    await Promise.all(
      groups.map(async (group) => {
        try {
          const res = await fetch(
            `${API_BASE}/api/projects/${selectedProject}/posthoc/status?group=${encodeURIComponent(group.name)}&tool=snp_analysis`
          );
          if (!res.ok) return;
          const data = await res.json();
          statusMap[group.name] = data;
        } catch {
          // ignore
        }
      })
    );
    setPosthocStatus(statusMap);
  }

  async function runPosthoc(groupName) {
    if (!selectedProject) return;
    setPosthocRunError("");
    const scope = posthocScopeByGroup[groupName] || "all";
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/posthoc/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group: groupName, tool: "snp_analysis", scope })
    });
    if (!res.ok) {
      const msg = await res.json().catch(() => ({}));
      setPosthocRunError(msg.detail || "Failed to start SNP analysis");
      window.alert(msg.detail || "Failed to start SNP analysis");
      return;
    }
    loadPosthocStatuses([{ name: groupName }]);
  }

  function downloadOutput(path, downloadName) {
    if (!selectedProject) return;
    const nameParam = downloadName ? `&download_name=${encodeURIComponent(downloadName)}` : "";
    const url = `${API_BASE}/api/projects/${selectedProject}/download-file?path=${encodeURIComponent(path)}${nameParam}`;
    window.open(url, "_blank");
  }

  function previewXlsx(path) {
    if (!selectedProject || !path) return;
    const url = `${API_BASE}/api/projects/${selectedProject}/preview-xlsx?path=${encodeURIComponent(path)}`;
    window.open(url, "_blank", "noopener");
  }

  // Open a project file in a new tab via the existing inline-download endpoint.
  // Optional `project` arg lets post-hoc cross-project rows view files for a
  // project other than the currently-selected one.
  function viewInline(path, project) {
    const proj = project || selectedProject;
    if (!proj || !path) return;
    const url = `${API_BASE}/api/projects/${encodeURIComponent(proj)}/download-file?path=${encodeURIComponent(path)}&inline=1`;
    window.open(url, "_blank", "noopener");
  }

  // Copy an absolute path to clipboard with a brief confirmation. Used in
  // place of the legacy "open folder in OS file manager" buttons, which were
  // a no-op under OOD (xdg-open on the server has no display, the user is
  // on a remote browser). The path is still actionable — paste into the OOD
  // Files app or scp.
  async function copyPathToClipboard(path, label) {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      window.alert(`Copied path to clipboard:\n${path}\n\nPaste into the OOD Files app or scp from a terminal.`);
    } catch (err) {
      window.prompt(`Copy this path (${label || "path"}):`, path);
    }
  }

  // Decide which preview button (if any) to render for a given output path.
  // The legacy "Open" button calls /api/projects/<p>/open which shells out
  // via xdg-open — useless in the web flow. So we replace it with browser-
  // native previews when we can:
  //   - xlsx/xlsm → formatted preview via /preview-xlsx (color-preserving)
  //   - tre/nwk   → tree viewer (existing button)
  //   - any text-ish / image / pdf / html → /download-file?inline=1 (new tab)
  //   - zip / bam / binary archives → no View button (DL only)
  function fileViewMode(path) {
    const p = (path || "").toLowerCase();
    if (/\.(xlsx|xlsm)$/.test(p)) return "xlsx";
    if (/\.(tre|nwk)$/.test(p)) return "tree";
    if (/\.(html?|fasta|fa|fna|nexus|nex|vcf|txt|tsv|csv|log|json|jsonl|ndjson|yaml|yml|md|pdf|png|jpe?g|gif|svg|webp)$/.test(p)) return "inline";
    return "none";
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

  // Query string carrying the active Step 1 Results filters so a download
  // matches what's on screen (date range + name filter). Empty filters are
  // omitted; the backend treats absent params as "no filter".
  function qcExportQuery() {
    const params = new URLSearchParams();
    if (qcDateStart) params.set("start", qcDateStart);
    if (qcDateEnd) params.set("end", qcDateEnd);
    const q = qcFilter.trim();
    if (q) params.set("q", q);
    const s = params.toString();
    return s ? `?${s}` : "";
  }

  // Suffix for downloaded filenames so a date-scoped export is self-describing
  // in an email attachment (e.g. _2026-05-01_to_2026-05-31).
  function qcExportSuffix() {
    if (qcDateStart && qcDateEnd) return `_${qcDateStart}_to_${qcDateEnd}`;
    if (qcDateStart) return `_from_${qcDateStart}`;
    if (qcDateEnd) return `_through_${qcDateEnd}`;
    return "";
  }

  async function downloadQC() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_summary.csv${qcExportQuery()}`);
    if (!res.ok) return;
    const csv = await res.text();
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedProject}_qc_summary${qcExportSuffix()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function downloadQcXlsx() {
    if (!selectedProject) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_summary.xlsx${qcExportQuery()}`);
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedProject}_combined_excelworksheets${qcExportSuffix()}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Build the set of sample names currently flagged for exclusion. Reads from
  // refs so a debounced auto-save doesn't see stale closure state.
  function _collectExcludedSamples() {
    const samples = new Set();
    (qcRowsRef.current || []).forEach((row) => {
      const key = excludeKey(row);
      if (excludedRef.current[key]) {
        const sample = sampleKey(row);
        if (sample) samples.add(sample);
      }
    });
    return samples;
  }

  async function _persistExclusions(opts = {}) {
    if (!selectedProject) return { ok: false };
    const samples = _collectExcludedSamples();
    try {
      const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/qc_exclude`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ samples: Array.from(samples) })
      });
      if (!res.ok) {
        if (opts.alertOnError) {
          const msg = await res.json().catch(() => ({}));
          window.alert(msg.detail || "Failed to save exclusions");
        }
        return { ok: false };
      }
      return { ok: true, count: samples.size };
    } catch (e) {
      if (opts.alertOnError) window.alert(`Save failed: ${e.message}`);
      return { ok: false };
    }
  }

  // Toggle handler: applies the change locally, then debounces a save so a
  // user click-storming through the QC table only POSTs once after they stop.
  function toggleExcluded(row, checked) {
    setExcluded((prev) => ({ ...prev, [excludeKey(row)]: checked }));
    if (excludeSaveTimerRef.current) {
      clearTimeout(excludeSaveTimerRef.current);
    }
    excludeSaveTimerRef.current = setTimeout(() => {
      excludeSaveTimerRef.current = null;
      _persistExclusions();
    }, 400);
  }

  // Manual save button — flushes any pending debounce immediately and surfaces
  // errors via alert. Kept as a belt-and-suspenders affordance.
  async function saveExclusions() {
    if (excludeSaveTimerRef.current) {
      clearTimeout(excludeSaveTimerRef.current);
      excludeSaveTimerRef.current = null;
    }
    const result = await _persistExclusions({ alertOnError: true });
    if (result.ok) window.alert(`Exclusions saved (${result.count})`);
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

  async function loadInputs(project) {
    if (!project) {
      setInputs({ files: [], total_bytes: 0, count: 0 });
      return;
    }
    setInputsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/projects/${project}/inputs`);
      if (res.ok) {
        const data = await res.json();
        setInputs(data);
      } else {
        setInputs({ files: [], total_bytes: 0, count: 0 });
      }
    } catch (_) {
      setInputs({ files: [], total_bytes: 0, count: 0 });
    } finally {
      setInputsLoading(false);
    }
  }

  // --- Project list inline sample browser (Kraken-GUI-style layout) -------
  // Clicking a project expands it to list its samples beneath the name; each
  // sample expands to its Kraken results (Krona/report HTML open inline).
  async function loadProjData(project) {
    setProjData((m) => ({ ...m, [project]: { loading: true, samples: (m[project]?.samples || []), krakenDirs: (m[project]?.krakenDirs || []) } }));
    try {
      const [inRes, kRes] = await Promise.all([
        fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/inputs`),
        fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/kraken/samples`).catch(() => null),
      ]);
      const inData = inRes.ok ? await inRes.json() : { files: [] };
      const samples = groupPairedFiles(inData.files || []);
      let krakenDirs = [];
      if (kRes && kRes.ok) krakenDirs = (await kRes.json()).samples || [];
      setProjData((m) => ({ ...m, [project]: { loading: false, samples, krakenDirs } }));
    } catch (_) {
      setProjData((m) => ({ ...m, [project]: { loading: false, samples: [], krakenDirs: [] } }));
    }
  }

  async function toggleProjectExpand(project) {
    const willOpen = !projExpanded[project];
    setProjExpanded((m) => ({ ...m, [project]: willOpen }));
    setSelectedProject(project);
    if (willOpen && !projData[project]) loadProjData(project);
  }

  // Match a sample name to a Kraken output dir in either direction (Kraken may
  // have stripped more/less of the read-tag suffix than the download name).
  function krakenDirForSample(krakenDirs, sample) {
    if (!sample || !krakenDirs?.length) return null;
    if (krakenDirs.includes(sample)) return sample;
    const longer = krakenDirs.filter((d) => d.startsWith(`${sample}_`)).sort();
    if (longer.length) return longer[0];
    const shorter = krakenDirs.filter((d) => sample.startsWith(`${d}_`));
    if (shorter.length) return shorter.sort((a, b) => b.length - a.length)[0];
    return null;
  }

  // Fetch the list of Kraken output dirs for a project and cache it by name.
  // Used by the Step 1 results table to show a "Krona" button only on samples
  // that already have a Kraken run. Cheap (one request per project) and safe
  // to call repeatedly — it just refreshes the cached list.
  async function loadProjectKrakenDirs(project) {
    if (!project) return;
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/kraken/samples`);
      if (!res.ok) return;
      const data = await res.json();
      setKrakenDirsByProject((m) => ({ ...m, [project]: data.samples || [] }));
    } catch (_) {
      // Non-fatal — without the list, rows simply won't show a Krona button.
    }
  }

  // True when a Step 1 sample has a Kraken run (and therefore a Krona graph).
  function hasKrakenRun(project, sample) {
    return !!krakenDirForSample(krakenDirsByProject[project], sample);
  }

  // Inline-open URL for a sample's Krona chart. The kraken dir name (not the
  // step1 sample name) is what the backend resolves against, so pass the
  // matched dir when we have it; the backend matches either direction anyway.
  function kronaHref(project, sample) {
    const dir = krakenDirForSample(krakenDirsByProject[project], sample) || sample;
    return `${API_BASE}/api/projects/${encodeURIComponent(project)}/kraken/samples/${encodeURIComponent(dir)}/krona`;
  }

  // Copy a server path to the clipboard (parsed-read fastqs → paste into the
  // Inputs "server path" field, or just keep a record of where they are).
  async function copyPath(path) {
    try {
      await navigator.clipboard.writeText(path);
      setCopiedPath(path);
      setTimeout(() => setCopiedPath((c) => (c === path ? "" : c)), 1500);
    } catch (_) {
      window.prompt("Copy this path:", path);
    }
  }

  // Import a single parsed-read fastq into this project's download/ (symlink),
  // so it shows up as a sample and can be re-run through Step 1.
  async function importFastqToDownload(project, path) {
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/link-local`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { window.alert(`Import failed: ${data.detail || res.status}`); return; }
      if (project === selectedProject) await loadInputs(selectedProject);
      await loadProjData(project);
      window.alert(data.linked ? `Imported ${data.linked} file into download/. It's now available in the Inputs panel and for Step 1.` : "Already in download/.");
    } catch (e) {
      window.alert(`Import failed: ${e.message}`);
    }
  }

  // Expand a sample row to show ITS files: the vSNP Step 1/2 outputs (always)
  // plus any Kraken ID Parse outputs (when a kraken run exists). `sample` is the
  // step1 sample name; `krakenDir` is the (possibly differently-named) kraken
  // output dir for it, or null.
  function toggleSample(project, sample, krakenDir) {
    const key = `${project}::${sample}`;
    const willOpen = !sampleKrakenOpen[key];
    setSampleKrakenOpen((m) => ({ ...m, [key]: willOpen }));
    if (willOpen) {
      if (!sampleStep1Files[key]) loadSampleStep1(project, sample);
      if (krakenDir && !sampleKrakenFiles[key]) loadSampleKraken(project, krakenDir);
    }
  }

  async function loadSampleStep1(project, sample) {
    const key = `${project}::${sample}`;
    setSampleStep1Files((m) => ({ ...m, [key]: { loading: true, files: [] } }));
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/samples/${encodeURIComponent(sample)}/files`);
      if (!res.ok) { setSampleStep1Files((m) => ({ ...m, [key]: { loading: false, files: [] } })); return; }
      const data = await res.json();
      setSampleStep1Files((m) => ({ ...m, [key]: { loading: false, ...data } }));
    } catch (_) {
      setSampleStep1Files((m) => ({ ...m, [key]: { loading: false, files: [] } }));
    }
  }

  // One file row in a sample's expanded output list (shared by the vSNP and
  // Kraken sections). Inline-openable files link; fastq.gz get import helpers;
  // everything has a download arrow.
  function sampleFileRow(project, f) {
    const base = `${API_BASE}/api/projects/${encodeURIComponent(project)}/download-file?path=${encodeURIComponent(f.path)}`;
    const isFastq = f.name.endsWith(".fastq.gz");
    return (
      <div key={f.path} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.path}>
          {f.openable ? (
            <a href={`${base}&inline=1`} target="_blank" rel="noopener noreferrer" title={`Open ${f.relpath}`}>{f.relpath}</a>
          ) : f.relpath}
          {isFastq ? <span title="Parsed reads — import to re-run through Step 1" style={{ marginLeft: 6, fontSize: "10px", padding: "0 5px", borderRadius: 8, background: "#fef3c7", color: "#92400e", fontWeight: 600 }}>parsed reads</span> : null}
        </span>
        <span className="muted" style={{ fontSize: "10.5px" }}>{_formatBytes(f.size)}</span>
        {isFastq ? (
          <>
            <button className="ghost-btn" style={{ fontSize: "10.5px", padding: "1px 6px" }} title="Copy this file's server path (paste into Inputs → server path)" onClick={() => copyPath(f.path)}>
              {copiedPath === f.path ? "Copied!" : "Copy path"}
            </button>
            <button className="ghost-btn" style={{ fontSize: "10.5px", padding: "1px 6px" }} title="Symlink into this project's download/ so it can be re-run through Step 1" onClick={() => importFastqToDownload(project, f.path)}>
              Import → Step 1
            </button>
          </>
        ) : null}
        <a href={`${base}&inline=0`} title={`Download ${f.name}`} style={{ textDecoration: "none" }}>⬇</a>
      </div>
    );
  }

  async function loadSampleKraken(project, sample) {
    const key = `${project}::${sample}`;
    setSampleKrakenFiles((m) => ({ ...m, [key]: { loading: true, files: [] } }));
    try {
      const res = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/kraken/samples/${encodeURIComponent(sample)}/files`);
      const data = await res.json();
      setSampleKrakenFiles((m) => ({ ...m, [key]: { loading: false, ...data } }));
    } catch (_) {
      setSampleKrakenFiles((m) => ({ ...m, [key]: { loading: false, present: false, files: [] } }));
    }
  }

  // Group paired-end fastq files into a single sample row in the
  // "Files in download/" list. Matches SRA convention (SRR<n>_1.fastq.gz +
  // _2.fastq.gz) and Illumina convention (Sample_R1[_001].fastq.gz +
  // _R2[_001].fastq.gz). Anything that doesn't match either stays solo.
  function groupPairedFiles(files) {
    const PAIR_RE = /^(.+?)_R?([12])(?:_\d+)?\.(?:fastq|fq)(?:\.gz)?$/i;
    const FASTQ_RE = /\.(?:fastq|fq)(?:\.gz)?$/i;
    const groups = [];
    const sampleIdx = new Map();
    for (const f of files) {
      // Only FASTQ reads are samples. Skip helper/clutter files that also live
      // in download/ (download_sra.sh, sra_crosswalk.tsv, logs, etc.).
      if (!FASTQ_RE.test(f.name)) continue;
      const m = f.name.match(PAIR_RE);
      const fmtime = Number(f.mtime) || 0;
      if (m) {
        const sample = m[1];
        if (sampleIdx.has(sample)) {
          const g = groups[sampleIdx.get(sample)];
          g.files.push(f);
          g.totalSize += f.size;
          g.isPair = g.files.length === 2;
          if (fmtime > g.mtime) g.mtime = fmtime;
        } else {
          sampleIdx.set(sample, groups.length);
          groups.push({ sample, files: [f], totalSize: f.size, isPair: false, mtime: fmtime });
        }
      } else {
        groups.push({ sample: f.name, files: [f], totalSize: f.size, isPair: false, mtime: fmtime });
      }
    }
    return groups;
  }

  // True if this download-list sample already has a completed Step 1 run.
  // step1Status entries are keyed by the step1/ directory name; FASTQ prefixes
  // usually equal that name (SRA run accession or the sample id), so we match
  // exact first, then allow the step1 name to start with the FASTQ prefix to
  // tolerate suffixing. Running / errored / not-started do not count as "run".
  function isSampleRunInStep1(sampleName) {
    if (!sampleName) return false;
    return step1Status.some((s) => {
      const done = s.has_outputs || s.has_zc_vcf || s.status === "complete";
      if (!done) return false;
      return s.sample === sampleName || String(s.sample || "").startsWith(sampleName);
    });
  }

  async function deletePair(groupFiles) {
    if (!selectedProject || !groupFiles.length) return;
    const label = groupFiles.length > 1
      ? `${groupFiles.length} paired files (${groupFiles.map((f) => f.name).join(", ")})`
      : groupFiles[0].name;
    if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return;
    for (const f of groupFiles) {
      try {
        const res = await fetch(
          `${API_BASE}/api/projects/${selectedProject}/inputs/${encodeURIComponent(f.name)}`,
          { method: "DELETE" },
        );
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          window.alert(`Delete of ${f.name} failed: ${err.detail || res.statusText}`);
          return;
        }
      } catch (e) {
        window.alert(`Delete of ${f.name} failed: ${e.message}`);
        return;
      }
    }
    loadInputs(selectedProject);
  }

  async function deleteInput(filename) {
    if (!selectedProject) return;
    if (!window.confirm(`Delete ${filename}? This cannot be undone.`)) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${selectedProject}/inputs/${encodeURIComponent(filename)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        window.alert(`Delete failed: ${err.detail || res.statusText}`);
        return;
      }
    } catch (e) {
      window.alert(`Delete failed: ${e.message}`);
      return;
    }
    await loadInputs(selectedProject);
  }

  function cancelUpload() {
    const xhr = uploadXhrRef.current;
    if (xhr) {
      xhr.abort();
      uploadXhrRef.current = null;
    }
  }

  function _formatBytes(bytes) {
    if (!bytes) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  async function uploadFiles(fileList) {
    if (!selectedProject || !settingsReady) {
      setUploadStatus("Select a project and complete Settings before uploading.");
      return;
    }
    const files = Array.from(fileList || []);
    if (!files.length) {
      setUploadStatus("No files selected.");
      return;
    }
    const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
    const totalMB = (totalBytes / 1024 / 1024).toFixed(1);
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    const startTime = Date.now();
    const plural = files.length > 1 ? "s" : "";
    setUploadStatus(`Uploading ${files.length} file${plural} (0 / ${totalMB} MB)...`);
    await new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      uploadXhrRef.current = xhr;
      xhr.upload.addEventListener("progress", (e) => {
        if (!e.lengthComputable) return;
        const elapsed = (Date.now() - startTime) / 1000;
        const loadedMB = (e.loaded / 1024 / 1024).toFixed(1);
        const pct = ((e.loaded / e.total) * 100).toFixed(1);
        const mbps = elapsed > 0 ? (e.loaded / 1024 / 1024 / elapsed).toFixed(1) : "—";
        setUploadStatus(`Uploading ${files.length} file${plural}: ${loadedMB} / ${totalMB} MB (${pct}%, ${elapsed.toFixed(0)}s, ${mbps} MB/s)`);
      });
      xhr.addEventListener("load", () => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const data = JSON.parse(xhr.responseText);
            setUploadStatus(`Uploaded ${data.uploaded} file${data.uploaded > 1 ? "s" : ""} in ${elapsed}s (${totalMB} MB)`);
          } catch {
            setUploadStatus(`Upload completed in ${elapsed}s`);
          }
        } else {
          let detail = `Upload failed (HTTP ${xhr.status})`;
          try {
            const msg = JSON.parse(xhr.responseText);
            if (msg.detail) detail = msg.detail;
          } catch {}
          setUploadStatus(detail);
        }
        resolve();
      });
      xhr.addEventListener("error", () => {
        setUploadStatus("Upload failed (network error)");
        resolve();
      });
      xhr.addEventListener("abort", () => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
        setUploadStatus(`Upload cancelled after ${elapsed}s`);
        resolve();
      });
      xhr.open("POST", `${API_BASE}/api/projects/${selectedProject}/upload`);
      xhr.send(formData);
    });
    uploadXhrRef.current = null;
    // Refresh the inputs panel so any successfully landed files appear
    // immediately. Cancelled uploads may have left a partial file behind;
    // it surfaces in the list and can be deleted via the [×] button.
    await loadInputs(selectedProject);
    await loadAll();
  }

  async function sraDownload() {
    if (!selectedProject || !settingsReady) return;
    const accessions = parseAccessions(sraText);
    if (!accessions.length) return;
    setSraStatus(`Downloading ${accessions.length} accession${accessions.length > 1 ? "s" : ""}...`);
    setShowRowLogs(true);
    let res;
    try {
      res = await fetch(`${API_BASE}/api/projects/${selectedProject}/sra/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accessions, folder: sraFolder || null })
      });
    } catch (e) {
      setSraStatus(`Download failed: ${e.message || "network error"}`);
      return;
    }
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON body */ }
    if (!res.ok) {
      // Backend surfaces NCBI eutils failures (rate-limit, etc.) as 502 with a
      // useful `detail`. Surface it so users don't see a generic spinner that
      // never clears.
      const detail = data.detail || `HTTP ${res.status}`;
      setSraStatus(`Download failed: ${detail}`);
      return;
    }
    setJobId(data.job_id);
    setSraJobId(data.job_id);
  }

  async function importVcfs() {
    if (!selectedProject || !settingsReady) return;
    // Auto-populate from enabled vcfDbFolders matching the selected reference.
    // The picker UI already filters to importReference for display, but the
    // underlying state holds every shared+user DB. Without the reference
    // filter here, e.g. selecting NC_045512_wuhan-hu-1 still pulls in the
    // mtbc0_v1.1 DBs because they're "enabled by default."
    const enabledPaths = vcfDbFolders
      .filter((f) => f.enabled && (f.reference || "") === importReference)
      .map((f) => f.path);
    const manualPaths = parseAccessions(importSourcesText);
    const vcfsFolderPaths = vcfsIncludeFolder && vcfsFolderPath ? [vcfsFolderPath] : [];
    const allPaths = [...new Set([...vcfsFolderPaths, ...enabledPaths, ...manualPaths])];
    // Don't write allPaths back into the textarea. The textarea is
    // reserved for *user-typed* extra paths only; auto-discovered DB
    // selections bleed in here would create a feedback loop where a
    // previous reference's DB paths survive a reference/project switch
    // and get re-included in the next build (manifesting as a
    // mysterious "Mismatched 57" the user didn't ask for).
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
    const parts = [
      `Imported ${data.imported}`,
      data.already_present ? `Already in set: ${data.already_present}` : null,
      data.renamed ? `Renamed ${data.renamed}` : null,
      data.dedup_skipped ? `Deduped (older copy): ${data.dedup_skipped}` : null,
      data.ref_skipped ? `Ref mismatch: ${data.ref_skipped}` : null,
      data.detected_reference ? `Ref: ${data.detected_reference}` : null
    ].filter(Boolean);
    setImportStatus(parts.join(" | "));
    setImportProjectLock(selectedProject);
    setStep2BuiltAt(new Date().toISOString());
    await refreshProjects(selectedProject);
    await loadVcfSourceSamples();
  }

  async function step1Setup() {
    if (!selectedProject || !settingsReady) return;
    await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/setup`, { method: "POST" });
    await loadAll();
    await loadStep1Status();
  }

  async function step1Run() {
    const effectiveRef = reference || projectReference;
    if (!selectedProject || !settingsReady || !effectiveRef) return;
    if (step1JobStatus === "running") {
      setStep1StatusError("Step 1 is already running for this project. Wait for it to finish before starting a new run.");
      return;
    }
    if (step1DispatchingRef.current) return;  // block a same-tick double-click
    step1DispatchingRef.current = true;
    const refValue = effectiveRef === "__auto__" ? null : effectiveRef;
    setStep1StatusError("");
    setStep2SetupMsg("Step 1 rerun started. Rebuild Step 2 VCF set before running Step 2.");
    setStep2BuiltAt("");
    setStep2VcfCount(0);
    setStep2Outputs([]);
    setStep2Groups([]);
    setStep2OutputsError("");
    setStep2RunId("");
    let res;
    try {
      res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step1/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference: refValue, debug: debugMode, assemble_unmap: assembleUnmap, nanopore: nanoporeMode, force_rerun: forceRerun })
      });
    } catch (e) {
      step1DispatchingRef.current = false;
      window.alert(`Step 1 run failed: ${e.message || "network error"}`);
      return;
    }
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON body */ }
    if (!res.ok) {
      // Backend raises HTTPException with a `detail` for things like provenance
      // dispatch failures (and the 409 when a run is already active). Surface
      // that instead of silently swallowing.
      step1DispatchingRef.current = false;
      window.alert(`Step 1 run failed: ${data.detail || `HTTP ${res.status}`}`);
      return;
    }
    setJobId(data.job_id);
    setStep1JobId(data.job_id);
    setStep1AutoRefreshPending(true);
    // T-46: surface samples auto-skipped from the dispatch (single-end,
    // junk-sized fastqs) so the user knows what didn't run and why. Without
    // this banner the GUI just silently runs N-of-M samples.
    if (Array.isArray(data.skipped_samples) && data.skipped_samples.length > 0) {
      const lines = data.skipped_samples
        .map((s) => `  • ${s.sample}: ${s.reason}`)
        .join("\n");
      window.alert(
        `Step 1 dispatched — ${data.skipped_samples.length} sample(s) auto-skipped:\n\n` +
        lines +
        `\n\nThese sample directories remain on disk (under step1/) but are excluded from this run. Single-end Illumina support is a separate ticket (T-46 Phase 2).`
      );
    }
    // Clear the re-entry guard now that the job is recorded; step1JobStatus is
    // about to read "running" (loadStep1Status), which keeps the button disabled.
    step1DispatchingRef.current = false;
    await loadStep1Status();
  }

  async function stopStep1() {
    if (!step1JobId || step1JobStatus !== "running") return;
    const ok = window.confirm(
      "Stop the Step 1 run?\n\n" +
      "Samples that have already finished keep their results. Any samples still " +
      "running will be terminated and left incomplete — you can re-run them later."
    );
    if (!ok) return;
    setStep1Stopping(true);
    try {
      const res = await fetch(`${API_BASE}/api/jobs/${step1JobId}/stop`, { method: "POST" });
      if (!res.ok && res.status !== 409) {
        let data = {};
        try { data = await res.json(); } catch (_) { /* non-JSON body */ }
        window.alert(`Could not stop Step 1: ${data.detail || `HTTP ${res.status}`}`);
      }
    } catch (e) {
      window.alert(`Could not stop Step 1: ${e.message || "network error"}`);
    } finally {
      setStep1Stopping(false);
      await loadStep1Status();
    }
  }

  async function step2Setup() {
    if (!selectedProject || !settingsReady) return;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/setup`, { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      const total = data.total ?? data.linked;
      const excluded = data.skipped_excluded || 0;
      const suffix = excluded > 0 ? ` · ${excluded} excluded per QC` : "";
      setStep2SetupMsg(`VCFs ready for Step 2: ${total} (linked ${data.linked})${suffix}`);
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
      setVcfSourceSamples([]);
      setVcfSourceFilter("");
      setVcfSourceOpen(false);
      setStep2Outputs([]);
      setStep2Groups([]);
      setStep2OutputsError("");
      await loadAll();
    }
  }

  async function step2Run() {
    if (!selectedProject || !settingsReady) return;
    const effectiveRef = reference || projectReference || null;
    const res = await fetch(`${API_BASE}/api/projects/${selectedProject}/step2/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reference: effectiveRef,
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
    setStep2JobId(data.job_id);
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
      setStep1JobStatus(data.job_status || "");
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

  // Open the latest *_stats.xlsx for a sample as a formatted HTML preview
  // in a new tab — same xlsx_html renderer used by the Reference Editor
  // View buttons. The preview page has an embedded "Download xlsx" link
  // (via ?download=1) so users can still pull the file for offline edits
  // without leaving the preview.
  function viewStep1Stats(project, sample) {
    if (!project || !sample) return;
    const url = `${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/samples/${encodeURIComponent(sample)}/stats/preview`;
    window.open(url, "_blank", "noopener");
  }

  // Kept for callers that explicitly want the raw xlsx (none today after the
  // Stats button migration, but the underlying download endpoint stays in
  // place for scripting / future use).
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
    setFolderModal({ open: true, project, sample, files: [], sampleDir: "", loading: true, error: "", krakenPresent: false, krakenFiles: [], krakenDir: "" });
    try {
      // Fetch vSNP step1 files and Kraken ID Parse files for the same sample
      // in parallel. Kraken outputs live in <project>/kraken/<sample>/ — the
      // same shared project dir — so we can surface them here for cross-tool
      // visibility. Kraken absence is not an error (present:false).
      const [res, krakenRes] = await Promise.all([
        fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/step1/samples/${encodeURIComponent(sample)}/files`),
        fetch(`${API_BASE}/api/projects/${encodeURIComponent(project)}/kraken/samples/${encodeURIComponent(sample)}/files`).catch(() => null),
      ]);
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setFolderModal((prev) => ({ ...prev, loading: false, error: detail.detail || `Failed to load files (${res.status})` }));
        return;
      }
      const data = await res.json();
      let kraken = { present: false, files: [], sample_dir: "" };
      if (krakenRes && krakenRes.ok) {
        kraken = await krakenRes.json().catch(() => kraken);
      }
      setFolderModal({
        open: true, project, sample,
        files: data.files || [], sampleDir: data.sample_dir || "",
        loading: false, error: "",
        krakenPresent: !!kraken.present,
        krakenFiles: kraken.files || [],
        krakenDir: kraken.sample_dir || "",
      });
    } catch (err) {
      setFolderModal((prev) => ({ ...prev, loading: false, error: String(err) }));
    }
  }

  function closeFolderModal() {
    setFolderModal({ open: false, project: "", sample: "", files: [], sampleDir: "", loading: false, error: "", krakenPresent: false, krakenFiles: [], krakenDir: "" });
  }

  // --- Kraken ID Parse on a single Step 1 sample -------------------------

  // Persist a new taxon search name to the shared kraken config/taxa.yaml and
  // select it. Same list the Kraken ID Parse GUI reads/writes.
  async function addKrakenTaxon() {
    const name = krakenNewTaxon.trim();
    if (!name || krakenAddingTaxon) return;
    setKrakenAddingTaxon(true);
    try {
      const res = await fetch(`${API_BASE}/api/kraken/taxa`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (res.ok) {
        const d = await res.json();
        if (Array.isArray(d.taxa)) setKrakenTaxonPresets(d.taxa);
        setKrakenModal((m) => ({ ...m, taxon: name }));
        setKrakenNewTaxon("");
      }
    } catch (_) {
      /* leave input as-is for retry */
    } finally {
      setKrakenAddingTaxon(false);
    }
  }

  function openKrakenModal(project, sample) {
    if (!project || !sample) return;
    setKrakenModal((m) => {
      // Re-attach to an in-flight run for the same sample instead of resetting,
      // so reopening from the background chip shows live progress.
      if (m.running && m.project === project && m.sample === sample) {
        return { ...m, open: true };
      }
      if (krakenEsRef.current && !m.running) { krakenEsRef.current.close(); krakenEsRef.current = null; }
      return {
        open: true, project, sample, mode: "full", taxon: "",
        running: false, jobId: null, status: "idle", log: [],
      };
    });
  }

  // Closing while a run is in progress just hides the modal — the job keeps
  // running in the background (server-side) and the EventSource keeps updating
  // state, so reopening via the background chip resumes the live view.
  function closeKrakenModal() {
    setKrakenModal((m) => {
      if (!m.running && krakenEsRef.current) { krakenEsRef.current.close(); krakenEsRef.current = null; }
      return { ...m, open: false };
    });
  }

  async function runKrakenForSample() {
    const { project, sample, mode, taxon, running } = krakenModal;
    if (running || !project || !sample) return;
    if ((mode === "full" || mode === "parse_only") && !taxon.trim()) return;
    setKrakenModal((m) => ({ ...m, running: true, status: "running", log: [] }));
    try {
      const res = await fetch(
        `${API_BASE}/api/projects/${encodeURIComponent(project)}/kraken/run`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sample, mode, taxon: taxon.trim() || null }),
        }
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        setKrakenModal((m) => ({
          ...m, running: false, status: "failed",
          log: [...m.log, `ERROR: ${detail.detail || res.status}`],
        }));
        return;
      }
      const { job_id } = await res.json();
      setKrakenModal((m) => ({ ...m, jobId: job_id }));
      streamKrakenLog(job_id);
    } catch (err) {
      setKrakenModal((m) => ({
        ...m, running: false, status: "failed",
        log: [...m.log, `ERROR: ${err.message}`],
      }));
    }
  }

  function streamKrakenLog(jobId) {
    const es = new EventSource(`${API_BASE}/api/jobs/${jobId}/events`);
    krakenEsRef.current = es;
    es.onmessage = (evt) => {
      const data = evt.data;
      const m = data.match(/^\[job:(succeeded|failed)\]$/);
      if (m) {
        es.close();
        krakenEsRef.current = null;
        setKrakenModal((mm) => {
          // Refresh the project's sample browser so the new Kraken outputs
          // (incl. parsed-read fastqs) appear under the sample, and drop any
          // stale cached file list for it.
          if (mm.project) {
            if (projExpanded[mm.project]) loadProjData(mm.project);
            // A successful run produced a new Kraken dir → refresh the cached
            // list so the Step 1 row's "Krona" button appears right away.
            if (m[1] === "succeeded") loadProjectKrakenDirs(mm.project);
            // The backend auto-imports the parsed-read fastqs into the project's
            // inputs (download/) on success; refresh the Inputs pane so they
            // show up immediately, ready to re-run through vSNP.
            if (m[1] === "succeeded" && mm.project === selectedProject) {
              loadInputs(selectedProject);
            }
            setSampleKrakenFiles((prev) => {
              const next = { ...prev };
              Object.keys(next).forEach((k) => { if (k.startsWith(`${mm.project}::`)) delete next[k]; });
              return next;
            });
          }
          return { ...mm, running: false, status: m[1] };
        });
        // Refresh the sample's cross-tool Kraken files if its folder is open.
        if (folderModal.open && folderModal.sample) {
          openStep1FolderModal(folderModal.project, folderModal.sample);
        }
        return;
      }
      setKrakenModal((mm) => ({ ...mm, log: [...mm.log, data] }));
    };
    es.onerror = () => {
      es.close();
      krakenEsRef.current = null;
      setKrakenModal((mm) => ({ ...mm, running: false, status: mm.status === "running" ? "failed" : mm.status }));
    };
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
          <img className="app-logo" src="vSNP_icon_align_256.png" alt="vSNP alignment icon" />
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
                    <button className="ghost action" onClick={openFolderBrowser}>Browse…</button>
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
                <div className="settings-row">
                  <label className="label">Saved locations</label>
                  <span style={{display:"inline-flex", alignItems:"center", gap:"6px", flexWrap:"wrap"}}>
                    <select
                      value=""
                      onChange={(e) => jumpToLocation(e.target.value)}
                      disabled={!(settings.saved_project_roots && settings.saved_project_roots.length)}
                      title="Jump the Projects root to a saved location"
                    >
                      <option value="">
                        {settings.saved_project_roots && settings.saved_project_roots.length
                          ? "↦ Jump to a saved location…"
                          : "No saved locations yet"}
                      </option>
                      {(settings.saved_project_roots || []).map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                    <button
                      className="ghost action"
                      onClick={saveCurrentLocation}
                      disabled={!settings.projects_root || (settings.saved_project_roots || []).includes(settings.projects_root)}
                      title="Save the current Projects root as a bookmark"
                    >★ Save current</button>
                    <button
                      className="ghost action"
                      onClick={() => removeSavedLocation(settings.projects_root)}
                      disabled={!(settings.saved_project_roots || []).includes(settings.projects_root)}
                      title="Remove the current Projects root from saved locations"
                    >Remove</button>
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
                placeholder="New project name (e.g. LSDV_India)"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value.replace(/\s+/g, "_"))}
                title="Spaces are auto-converted to underscores. Letters, digits, _ - . are allowed; other characters will be rejected."
              />
              <select
                value={newProjectReference}
                onChange={(e) => setNewProjectReference(e.target.value)}
                title="Optional: set the reference at project creation"
                style={{ minWidth: "10rem" }}
              >
                <option value="">-- reference (optional) --</option>
                {references.map((r) => (
                  <option key={r.name} value={r.name}>{r.name}</option>
                ))}
              </select>
              <button onClick={createProject}>Create</button>
            </div>
            <div className="row" style={{ marginTop: "4px" }}>
              <input
                type="search"
                placeholder="Filter samples in expanded projects…"
                value={projSampleFilter}
                onChange={(e) => setProjSampleFilter(e.target.value)}
                title="Case-insensitive. Expand a project, then type to show only matching samples."
                style={{ flex: 1 }}
              />
              {projSampleFilter ? (
                <button className="ghost-btn" onClick={() => setProjSampleFilter("")} title="Clear filter">Clear</button>
              ) : null}
            </div>
            <div className="list">
              {projects.map((p) => (
                <div key={p.name} style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                <div
                  className={`list-item ${p.name === selectedProject ? "active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => toggleProjectExpand(p.name)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") toggleProjectExpand(p.name);
                  }}
                >
                  <div className="list-details">
                    <div className="list-title" style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                      <span className="expand-icon" style={{ fontSize: "0.7em", color: "var(--muted)" }}>{projExpanded[p.name] ? "▾" : "▸"}</span>
                      <span>{p.display_name || p.name}</span>
                      {p.scope === "shared" ? (
                        <span
                          title="Shared project (visible to all members of the project group)"
                          style={{
                            fontSize: "0.7em",
                            padding: "1px 6px",
                            borderRadius: "10px",
                            background: "#dbe9ff",
                            color: "#13294b",
                            fontWeight: 600,
                            letterSpacing: "0.02em",
                          }}
                        >
                          shared
                        </span>
                      ) : null}
                      {p.reference ? (
                        <span
                          title={`Reference: ${p.reference}`}
                          style={{
                            fontSize: "0.7em",
                            padding: "1px 6px",
                            borderRadius: "10px",
                            background: "#e8f5e9",
                            color: "#1b5e20",
                            fontWeight: 600,
                            letterSpacing: "0.02em",
                          }}
                        >
                          {p.reference}
                        </span>
                      ) : null}
                    </div>
                    <div className="list-meta">
                      FASTQ: {p.fastq_count} | Step1: {p.step1_samples} | _VCFs: {p.vcfs_count ?? p.step1_vcfs}
                      {p.last_activity ? (
                        <span title={`Last activity: ${p.last_activity}`}> | {_formatActivity(p.last_activity)}</span>
                      ) : null}
                    </div>
                    {p._root ? (
                      <div
                        className="list-meta"
                        style={{ fontSize: "0.72em", opacity: 0.8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", direction: "rtl", textAlign: "left" }}
                        title={`${p._root}/${p.name}`}
                      >
                        {p._root}/{p.name}
                      </div>
                    ) : null}
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
                {/* Samples listed beneath the project name (Kraken-GUI layout).
                    Each sample expands to its Kraken results. */}
                {projExpanded[p.name] ? (
                  <div style={{ marginLeft: "1.1rem", display: "flex", flexDirection: "column", gap: "4px" }}>
                    {projData[p.name]?.loading ? (
                      <div className="muted" style={{ fontSize: "12px" }}>Loading samples…</div>
                    ) : !projData[p.name] || projData[p.name].samples.length === 0 ? (
                      <div className="muted" style={{ fontSize: "12px" }}>
                        No samples in download/ yet. Add FASTQs in the Inputs panel.
                      </div>
                    ) : (() => {
                      const q = projSampleFilter.trim().toLowerCase();
                      const shown = q
                        ? projData[p.name].samples.filter((g) => g.sample.toLowerCase().includes(q))
                        : projData[p.name].samples;
                      if (q && shown.length === 0) {
                        return <div className="muted" style={{ fontSize: "12px" }}>No samples match “{projSampleFilter}”.</div>;
                      }
                      return shown.map((g) => {
                        const krakenDir = krakenDirForSample(projData[p.name].krakenDirs, g.sample);
                        const key = `${p.name}::${g.sample}`;
                        const open = !!sampleKrakenOpen[key];
                        const kRes = sampleKrakenFiles[key];
                        return (
                          <div key={g.sample} className="sample-row" style={{ border: "1px solid var(--border)", borderRadius: "8px", background: "var(--panel-2)" }}>
                            <div
                              style={{ display: "flex", alignItems: "center", gap: "8px", padding: "6px 10px", cursor: "pointer", fontSize: "12px" }}
                              onClick={() => toggleSample(p.name, g.sample, krakenDir)}
                              title="Show this sample's output files"
                            >
                              <span className="expand-icon" style={{ fontSize: "0.7em", color: "var(--muted)" }}>{open ? "▾" : "▸"}</span>
                              <span style={{ flex: 1, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.sample}</span>
                              {g.isPair ? <span className="muted" style={{ fontSize: "10.5px" }}>R1+R2</span> : null}
                              {krakenDir ? (
                                <span title="Kraken ID Parse results available" style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "10px", background: "#ede9fe", color: "#5b21b6", fontWeight: 600 }}>🧬 Kraken</span>
                              ) : null}
                            </div>
                            {open ? (
                              <div style={{ borderTop: "1px solid var(--border)", padding: "4px 10px 6px 24px", display: "flex", flexDirection: "column", gap: "6px" }}>
                                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                                  <span className="muted" style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase" }}>vSNP outputs</span>
                                  {(() => {
                                    const s1 = sampleStep1Files[key];
                                    if (!s1 || s1.loading) return <span className="muted" style={{ fontSize: "11px" }}>Loading…</span>;
                                    if (!(s1.files || []).length) return <span className="muted" style={{ fontSize: "11px" }}>No Step 1 outputs yet — run Step 1 for this sample.</span>;
                                    return s1.files.map((f) => sampleFileRow(p.name, f));
                                  })()}
                                </div>
                                {krakenDir ? (
                                  <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                                    <span className="muted" style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase" }}>🧬 Kraken outputs</span>
                                    {kRes?.loading ? (
                                      <span className="muted" style={{ fontSize: "11px" }}>Loading Kraken results…</span>
                                    ) : !kRes || !(kRes.files || []).length ? (
                                      <span className="muted" style={{ fontSize: "11px" }}>No Kraken output files.</span>
                                    ) : (
                                      kRes.files.map((f) => sampleFileRow(p.name, f))
                                    )}
                                  </div>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        );
                      });
                    })()}
                  </div>
                ) : null}
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Inputs</h2>
            <div className="input-columns">
              <div className="input-column">
                <h3>Bring Your Own FASTQ</h3>
                {window?.vsnp?.selectPath ? (
                  <button
                    onClick={async () => {
                      const picked = await window.vsnp.selectPath({
                        kind: "folder",
                        title: "Select FASTQ folder",
                        defaultPath: settings.projects_root || undefined
                      });
                      if (picked) {
                        setLocalPath(picked);
                        await linkLocal(picked);
                      }
                    }}
                    disabled={!selectedProject || !settingsReady}
                  >
                    Choose Folder
                  </button>
                ) : (
                  <div style={{display:"flex", flexDirection:"column", gap:"4px"}}>
                    <select
                      value={folderPickerMode === "custom" ? "__custom__" : ""}
                      disabled={!selectedProject || !settingsReady}
                      onChange={async (e) => {
                        const value = e.target.value;
                        if (value === "__custom__") {
                          setFolderPickerMode("custom");
                          return;
                        }
                        if (value === "") return;
                        setLocalPath(value);
                        await linkLocal(value);
                      }}
                      style={{fontSize:"13px"}}
                    >
                      <option value="">Pick a folder on the server…</option>
                      {projects
                        .filter((p) => p.name !== selectedProject && p._root)
                        .map((p) => {
                          const path = `${p._root}/${p.name}/download`;
                          return (
                            <option key={path} value={path}>
                              {p.name}/download {p.scope === "shared" ? "(shared)" : ""}
                            </option>
                          );
                        })}
                      <option value="__custom__">Custom path…</option>
                    </select>
                    {folderPickerMode === "custom" ? (
                      <div style={{display:"flex", gap:"4px", alignItems:"center"}}>
                        <input
                          type="text"
                          value={localPath}
                          onChange={(e) => setLocalPath(e.target.value)}
                          placeholder="/srv/kapurlab/projects/<name>/download"
                          style={{flex:1, fontSize:"12px"}}
                          onKeyDown={async (e) => {
                            if (e.key === "Enter" && localPath.trim()) {
                              await linkLocal(localPath.trim());
                            }
                          }}
                        />
                        <button
                          className="ghost"
                          onClick={async () => {
                            if (localPath.trim()) await linkLocal(localPath.trim());
                          }}
                          disabled={!localPath.trim() || !selectedProject || !settingsReady}
                        >
                          Link
                        </button>
                        <button
                          className="ghost"
                          onClick={() => { setFolderPickerMode("dropdown"); setLocalPath(""); }}
                          title="Back to dropdown"
                        >
                          ↶
                        </button>
                      </div>
                    ) : null}
                  </div>
                )}
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
                      ref={uploadInputRef}
                      type="file"
                      multiple
                      style={{ display: "none" }}
                      onChange={(e) => {
                        const files = Array.from(e.target.files);
                        e.target.value = "";
                        uploadFiles(files);
                      }}
                    />
                    <button
                      type="button"
                      disabled={!selectedProject || !settingsReady}
                      onClick={() => uploadInputRef.current?.click()}
                    >
                      Choose Files
                    </button>
                    <span>Or drop FASTQ.GZ files here</span>
                  </div>
                  {!selectedProject || !settingsReady ? (
                    <div className="note warning">Select a project and complete Settings to enable uploads.</div>
                  ) : null}
                  {uploadStatus ? (
                    <div className="note" style={{display:"flex", alignItems:"center", gap:"8px"}}>
                      <span style={{flex:1}}>{uploadStatus}</span>
                      {uploadStatus.startsWith("Uploading") ? (
                        <button
                          type="button"
                          className="ghost"
                          onClick={cancelUpload}
                          title="Abort the in-progress upload (partial file will appear in the list below)"
                          style={{fontSize:"11px", padding:"2px 8px"}}
                        >
                          Cancel
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                {/* T-07-adjacent: file list in <project>/download/ so users can see what
                    they've uploaded (without going through OOD Files) and clean up
                    partial files left behind by a cancelled upload. */}
                {selectedProject ? (
                  <div className="block">
                    <h3 style={{display:"flex", alignItems:"center", gap:"8px"}}>
                      <span style={{flex:1}}>
                        Files in download/
                        {inputs.count > 0 ? (
                          <span className="muted" style={{marginLeft:"6px", fontWeight:"normal", fontSize:"12px"}}>
                            ({inputs.count} file{inputs.count > 1 ? "s" : ""}, {_formatBytes(inputs.total_bytes)})
                          </span>
                        ) : null}
                      </span>
                      {inputs.files.some((f) => f.name === "sra_crosswalk.tsv") ? (
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => window.open(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/sra-crosswalk`, "_blank", "noopener")}
                          title="View the SRS/DRS/SRX → SRR/DRR resolution for this project. Generated by the SRA download flow."
                          style={{fontSize:"11px", padding:"2px 8px"}}
                        >
                          View crosswalk
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => loadInputs(selectedProject)}
                        disabled={inputsLoading}
                        title="Refresh the file list"
                        style={{fontSize:"11px", padding:"2px 8px"}}
                      >
                        {inputsLoading ? "…" : "Refresh"}
                      </button>
                    </h3>
                    {!inputsLoading && inputs.files.length === 0 ? (
                      <div className="muted" style={{fontSize:"12px"}}>No files yet. Upload above or use Choose Folder.</div>
                    ) : null}
                    {inputs.files.length > 0 ? (() => {
                      const allGroups = groupPairedFiles(inputs.files);
                      const weekAgo = (Date.now() / 1000) - 7 * 86400;
                      const olderCount = allGroups.filter((g) => g.mtime && g.mtime < weekAgo).length;
                      const groups = showAllInputs
                        ? allGroups
                        : allGroups.filter((g) => !g.mtime || g.mtime >= weekAgo);
                      return (
                      <>
                      <div style={{display:"flex", flexDirection:"column", gap:"3px", maxHeight:"260px", overflowY:"auto"}}>
                        {groups.map((g) => {
                          const tip = g.files.map((f) => `${f.name} (${_formatBytes(f.size)})`).join("\n");
                          const notRun = !isSampleRunInStep1(g.sample);
                          return (
                            <div
                              key={g.files.map((f) => f.name).join("|")}
                              style={{
                                display:"flex", alignItems:"center", gap:"8px",
                                padding:"4px 8px",
                                background:"var(--panel-2)",
                                border:"1px solid var(--border)",
                                borderRadius:"6px",
                                fontSize:"12px",
                              }}
                            >
                              <span
                                style={{
                                  flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap",
                                  color: notRun ? "var(--accent, #2f6f9f)" : "var(--text)",
                                  fontWeight: notRun ? 600 : "normal",
                                }}
                                title={notRun ? `${tip}\n\nNot yet run through Step 1` : tip}
                              >
                                {g.sample}
                                {notRun ? (
                                  <span style={{marginLeft:"6px", fontSize:"10.5px", color:"var(--accent, #2f6f9f)"}}>
                                    • not run
                                  </span>
                                ) : null}
                                {g.isPair ? (
                                  <span className="muted" style={{marginLeft:"6px", fontSize:"10.5px"}}>
                                    paired · R1+R2
                                  </span>
                                ) : null}
                              </span>
                              <span className="muted" style={{minWidth:"70px", textAlign:"right"}}>{_formatBytes(g.totalSize)}</span>
                              <button
                                type="button"
                                className="chip-remove"
                                onClick={() => deletePair(g.files)}
                                title={g.isPair ? `Delete both ${g.files.map((f) => f.name).join(" + ")}` : `Delete ${g.files[0].name}`}
                              >
                                ×
                              </button>
                            </div>
                          );
                        })}
                      </div>
                      {olderCount > 0 ? (
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => setShowAllInputs((v) => !v)}
                          style={{fontSize:"11px", padding:"2px 8px", marginTop:"4px", alignSelf:"flex-start"}}
                          title="Files older than 7 days are hidden by default to keep this list readable as it grows."
                        >
                          {showAllInputs ? `Show recent only (hide ${olderCount} older)` : `Show all (${olderCount} older)`}
                        </button>
                      ) : null}
                      </>
                      );
                    })() : null}
                  </div>
                ) : null}
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
                {inputs.files.some((f) => f.name === "sra_crosswalk.tsv") ? (
                  <div className="note" style={{marginTop:"6px", fontSize:"12px"}}>
                    <a
                      href="#"
                      onClick={(e) => { e.preventDefault(); window.open(`${API_BASE}/api/projects/${encodeURIComponent(selectedProject)}/sra-crosswalk`, "_blank", "noopener"); }}
                    >
                      View crosswalk
                    </a>
                    <span className="muted"> — sample/study accessions you submitted → run accessions actually downloaded.</span>
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
                    <input
                      placeholder="Display name — optional, e.g. LSDV_NW-LW_AF325528.1"
                      value={genomeDisplayName}
                      onChange={(e) => setGenomeDisplayName(e.target.value)}
                      style={{width:"100%", marginBottom:"0.3em"}}
                      title="Becomes the directory name and the entry in the Reference dropdown. Letters, digits, _ - . only. Blank → uses the accession."
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
                    setMetaStatus("");
                    setMetaSingleOrig("");
                    setMetaSingleDisplay("");
                    setMetaBulkText("");
                    loadRefEditorFiles(e.target.value);
                    loadMetadata(e.target.value);
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
                              <button onClick={() => viewRefFile(refEditorRef, defineFile.name)}>
                                View
                              </button>
                              <button className="ghost" onClick={() => downloadRefFile(refEditorRef, defineFile.name)}>
                                Download
                              </button>
                              <button className="ghost" onClick={() => replaceRefFile(refEditorRef, defineFile.name)}>
                                Replace
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
                          {hasDefine && defineFile ? (
                            <div style={{marginTop:"0.5em"}}>
                              <div style={{fontWeight:600, fontSize:"0.85em", marginBottom:"0.3em"}}>Add defining SNP group</div>
                              <div className="muted" style={{fontSize:"0.82em", marginBottom:"0.3em"}}>
                                A group name and its absolute position(s) as <code>chrom:position</code> (e.g. <code>NC_000962:12345</code>). Separate multiple positions with spaces, commas, or new lines. A bare number is auto-prefixed with the reference's contig. This permanently adds to <code>{defineFile.name}</code>.
                              </div>
                              <input
                                placeholder="Group name (e.g. Lineage-04X)"
                                value={dfGroupName}
                                onChange={(e) => setDfGroupName(e.target.value)}
                                style={{width:"100%", boxSizing:"border-box", marginBottom:"0.3em"}}
                              />
                              <textarea
                                rows={2}
                                placeholder={"chrom:position\nor: 12345, 67890"}
                                value={dfPositions}
                                onChange={(e) => setDfPositions(e.target.value)}
                                style={{width:"100%", boxSizing:"border-box", fontFamily:"monospace", fontSize:"0.85em", resize:"vertical"}}
                              />
                              <button
                                style={{marginTop:"0.3em"}}
                                disabled={!dfGroupName.trim() || !dfPositions.trim()}
                                onClick={addDefineFilterGroup}
                              >
                                Add group
                              </button>
                              {dfStatus && <div className="note" style={{marginTop:"0.4em", fontSize:"0.85em"}}>{dfStatus}</div>}
                            </div>
                          ) : null}
                        </div>

                        <div className="ref-editor-card">
                          <h3>Remove from Analysis</h3>
                          <div className="muted" style={{fontSize:"0.85em", marginBottom:"0.4em"}}>
                            Lists samples or regions to exclude from the analysis.
                          </div>
                          {hasRemove && removeFile ? (
                            <div className="ref-editor-file-row">
                              <span className="ref-editor-filename">{removeFile.name}</span>
                              <button onClick={() => viewRefFile(refEditorRef, removeFile.name)}>
                                View
                              </button>
                              <button className="ghost" onClick={() => downloadRefFile(refEditorRef, removeFile.name)}>
                                Download
                              </button>
                              <button className="ghost" onClick={() => replaceRefFile(refEditorRef, removeFile.name)}>
                                Replace
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
                          {hasRemove && removeFile ? (
                            <div style={{marginTop:"0.5em"}}>
                              <div style={{fontWeight:600, fontSize:"0.85em", marginBottom:"0.3em"}}>Add sample to remove</div>
                              <div className="muted" style={{fontSize:"0.82em", marginBottom:"0.3em"}}>
                                Sample name(s) to permanently exclude from analysis for this reference (matches the VCF stem, no extension). One per line or comma-separated. Adds to <code>{removeFile.name}</code>.
                              </div>
                              <textarea
                                rows={2}
                                placeholder={"sample_name\nanother_sample"}
                                value={rmSampleText}
                                onChange={(e) => setRmSampleText(e.target.value)}
                                style={{width:"100%", boxSizing:"border-box", fontFamily:"monospace", fontSize:"0.85em", resize:"vertical"}}
                              />
                              <button
                                style={{marginTop:"0.3em"}}
                                disabled={!rmSampleText.trim()}
                                onClick={addRemoveSample}
                              >
                                Add sample
                              </button>
                              {rmStatus && <div className="note" style={{marginTop:"0.4em", fontSize:"0.85em"}}>{rmStatus}</div>}
                            </div>
                          ) : null}
                        </div>

                        <div className="ref-editor-card">
                          <h3>Sample Metadata</h3>
                          <div className="muted" style={{fontSize:"0.85em", marginBottom:"0.4em"}}>
                            Maps VCF file-stem names to human-readable labels in vSNP3 trees and tables. Column 1 = original name (VCF stem, e.g. <code>99-0100</code>), Column 2 = display label.
                          </div>
                          {metaLoading ? (
                            <div className="note"><span className="pulse-dot" /> Loading…</div>
                          ) : metaExists && metaFilename ? (
                            <>
                              <div className="ref-editor-file-row" style={{marginBottom:"0.4em"}}>
                                <span className="ref-editor-filename">{metaFilename}</span>
                                <button onClick={() => viewRefFile(refEditorRef, metaFilename)}>View</button>
                                <button className="ghost" onClick={() => downloadRefFile(refEditorRef, metaFilename)}>Download</button>
                                <button className="ghost" onClick={() => replaceRefFile(refEditorRef, metaFilename, () => loadMetadata(refEditorRef))}>Replace</button>
                              </div>
                              {metaRows.length > 0 ? (
                                <div style={{overflowX:"auto", maxHeight:"14em", overflowY:"auto", marginBottom:"0.5em", fontSize:"0.82em"}}>
                                  <table style={{width:"100%", borderCollapse:"collapse"}}>
                                    <thead>
                                      <tr>
                                        <th style={{textAlign:"left", padding:"0.15em 0.4em", borderBottom:"1px solid var(--border,#ccc)", whiteSpace:"nowrap"}}>Original name</th>
                                        <th style={{textAlign:"left", padding:"0.15em 0.4em", borderBottom:"1px solid var(--border,#ccc)"}}>Display label</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {metaRows.map((r, i) => (
                                        <tr key={i} style={{background: i % 2 === 0 ? "transparent" : "var(--row-alt,rgba(0,0,0,0.03))"}}>
                                          <td style={{padding:"0.1em 0.4em", fontFamily:"monospace"}}>{r.original}</td>
                                          <td style={{padding:"0.1em 0.4em"}}>{r.display_name}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              ) : (
                                <div className="muted" style={{fontSize:"0.85em", marginBottom:"0.5em"}}>File exists but contains no rows yet.</div>
                              )}
                            </>
                          ) : (
                            <div className="muted" style={{fontSize:"0.85em", marginBottom:"0.5em"}}>No metadata file found — one will be created as <code>{refEditorRef}_metadata.xlsx</code> when you add the first entry.</div>
                          )}

                          {selectedProject && vcfsFolderSamples.length > 0 ? (() => {
                            const metaOrigSet = new Set(metaRows.map(r => r.original));
                            const missing = vcfsFolderSamples.filter(s => !metaOrigSet.has(s.sample));
                            const covered = vcfsFolderSamples.length - missing.length;
                            return (
                              <div style={{marginTop:"0.4em", padding:"0.4em 0.5em", background:"var(--panel-2,rgba(0,0,0,0.03))", borderRadius:"6px", fontSize:"0.82em"}}>
                                <div style={{fontWeight:600, marginBottom:"0.2em"}}>
                                  {vcfsFolderName} coverage: {covered} / {vcfsFolderSamples.length} samples have metadata
                                </div>
                                {missing.length > 0 ? (
                                  <details>
                                    <summary style={{cursor:"pointer", color:"var(--muted,#666)"}}>
                                      {missing.length} missing — click to pre-fill
                                    </summary>
                                    <ul style={{listStyle:"none", padding:0, margin:"0.3em 0"}}>
                                      {missing.map(s => (
                                        <li key={s.sample} style={{display:"flex", gap:"0.4em", alignItems:"center", marginBottom:"0.15em"}}>
                                          <code style={{flex:"0 0 auto"}}>{s.sample}</code>
                                          <button
                                            className="ghost action"
                                            style={{fontSize:"0.8em", padding:"0 0.3em"}}
                                            onClick={() => { setMetaSingleOrig(s.sample); setMetaSingleDisplay(s.sample); }}
                                          >
                                            Fill
                                          </button>
                                        </li>
                                      ))}
                                    </ul>
                                  </details>
                                ) : <div style={{color:"var(--accent,green)"}}>All samples covered</div>}
                              </div>
                            );
                          })() : null}

                          <div style={{marginTop:"0.5em"}}>
                            <div style={{fontWeight:600, fontSize:"0.85em", marginBottom:"0.3em"}}>Add single entry</div>
                            <div style={{display:"flex", gap:"0.3em", alignItems:"center", flexWrap:"wrap"}}>
                              <input
                                placeholder="Original name (VCF stem)"
                                value={metaSingleOrig}
                                onChange={(e) => setMetaSingleOrig(e.target.value)}
                                style={{flex:"1 1 10em", minWidth:"8em"}}
                              />
                              <span style={{color:"var(--muted,#888)"}}>→</span>
                              <input
                                placeholder="Display label"
                                value={metaSingleDisplay}
                                onChange={(e) => setMetaSingleDisplay(e.target.value)}
                                style={{flex:"1 1 10em", minWidth:"8em"}}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" && metaSingleOrig.trim() && metaSingleDisplay.trim()) {
                                    addMetadataRows([{original: metaSingleOrig.trim(), display_name: metaSingleDisplay.trim()}]);
                                    setMetaSingleOrig("");
                                    setMetaSingleDisplay("");
                                  }
                                }}
                              />
                              <button
                                disabled={!metaSingleOrig.trim() || !metaSingleDisplay.trim()}
                                onClick={() => {
                                  addMetadataRows([{original: metaSingleOrig.trim(), display_name: metaSingleDisplay.trim()}]);
                                  setMetaSingleOrig("");
                                  setMetaSingleDisplay("");
                                }}
                              >Add</button>
                            </div>
                          </div>

                          <div style={{marginTop:"0.5em"}}>
                            <button
                              className="ghost action"
                              style={{fontSize:"0.85em"}}
                              onClick={() => setMetaBulkOpen(!metaBulkOpen)}
                            >
                              {metaBulkOpen ? "Hide bulk paste" : "Bulk paste (tab-delimited)"}
                            </button>
                            {metaBulkOpen && (
                              <div style={{marginTop:"0.3em"}}>
                                <div className="muted" style={{fontSize:"0.82em", marginBottom:"0.2em"}}>
                                  Paste two tab-separated columns, one row per line: <code>original_name{"\\t"}display_label</code>. Existing originals will be overwritten.
                                </div>
                                <textarea
                                  rows={6}
                                  style={{width:"100%", fontFamily:"monospace", fontSize:"0.85em", resize:"vertical"}}
                                  placeholder={"99-0100\tBovine TB isolate A\n2023-0055\tBrucella field strain"}
                                  value={metaBulkText}
                                  onChange={(e) => setMetaBulkText(e.target.value)}
                                />
                                <button
                                  style={{marginTop:"0.3em"}}
                                  disabled={!metaBulkText.trim()}
                                  onClick={() => {
                                    const rows = metaBulkText
                                      .split("\n")
                                      .map((line) => line.split("\t").map((s) => s.trim()))
                                      .filter((parts) => parts.length >= 2 && parts[0] && parts[1])
                                      .map(([original, display_name]) => ({ original, display_name }));
                                    if (!rows.length) {
                                      setMetaStatus("No valid rows found — ensure two tab-separated columns.");
                                      return;
                                    }
                                    addMetadataRows(rows);
                                    setMetaBulkText("");
                                    setMetaBulkOpen(false);
                                  }}
                                >
                                  Add {metaBulkText.split("\n").filter((l) => l.includes("\t") && l.trim()).length} rows
                                </button>
                              </div>
                            )}
                          </div>

                          {metaStatus && (
                            <div className="note" style={{marginTop:"0.4em", fontSize:"0.85em"}}>{metaStatus}</div>
                          )}
                        </div>

                        <div className="note" style={{marginTop:"0.8em"}}>
                          <strong>View</strong> opens a formatted, read-only preview in a new tab (cell colors and conditional formatting preserved).
                          <br />
                          <strong>Download</strong> saves the .xlsx so you can edit it locally in Excel, Numbers, or LibreOffice.
                          <br />
                          <strong>Replace</strong> uploads your edited copy back to the reference dir. You'll be asked for a rationale (recorded in <code>/srv/kapurlab/audit/reference-changes.jsonl</code>); the previous version is archived under <code>.history/</code> and is recoverable. Filename must match exactly — only <code>*_define_filter.xlsx</code> and <code>*_remove_from_analysis.xlsx</code> can be replaced via this path.
                        </div>
                        <button
                          className="ghost action"
                          style={{marginTop:"0.4em"}}
                          onClick={() => { loadRefEditorFiles(refEditorRef); loadMetadata(refEditorRef); }}
                        >
                          Refresh file list
                        </button>
                      </>
                    );
                  })()}
                </div>
              ) : (
                <div className="block">
                  <div className="note">Select a reference type on the left to edit its filter, exclusion, and metadata spreadsheets.</div>
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
              {projectReference ? (
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span
                    style={{
                      padding: "3px 10px",
                      borderRadius: "10px",
                      background: "#e8f5e9",
                      color: "#1b5e20",
                      fontWeight: 600,
                      fontSize: "0.9em",
                    }}
                  >
                    {projectReference}
                  </span>
                  <span className="muted" style={{ fontSize: "0.8em" }}>
                    (set at project level — change in Projects panel)
                  </span>
                </div>
              ) : (
                <>
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
                </>
              )}
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
              <label className="checkbox" title="By default, samples already marked Complete are skipped so re-running a batch (e.g. after adding newly-parsed samples) only aligns the new ones. Tick this to re-align everything.">
                <input
                  type="checkbox"
                  checked={forceRerun}
                  onChange={(e) => setForceRerun(e.target.checked)}
                />
                Force re-run (re-align samples already Complete)
              </label>
              <div className="step1-actions">
                <button onClick={step1Setup} disabled={!selectedProject || !settingsReady}>Setup</button>
                <button
                  onClick={step1Run}
                  disabled={!selectedProject || !settingsReady || (!reference && !projectReference) || step1JobStatus === "running"}
                  title={step1JobStatus === "running" ? "Step 1 batch is in progress — wait for it to finish" : ""}
                >
                  {step1JobStatus === "running" ? "Running…" : "Run"}
                </button>
                {step1JobStatus === "running" ? (
                  <button
                    className="danger"
                    onClick={stopStep1}
                    disabled={step1Stopping}
                    title="Terminate the running Step 1 batch. Finished samples keep their results."
                  >
                    {step1Stopping ? "Stopping…" : "Stop"}
                  </button>
                ) : null}
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
              {step1Status.length > 0 ? (
                <div style={{borderTop:"1px solid var(--border)", marginTop:"0.5em", paddingTop:"0.5em"}}>
                  <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", fontSize:"0.85em"}}>
                    <span><strong>{vcfsFolderName || `${selectedProject}_VCFs`}</strong>: {vcfsFolderCount} VCF{vcfsFolderCount !== 1 ? "s" : ""}</span>
                    <button className="ghost" style={{fontSize:"0.8em"}} onClick={() => collectVcfs([])}>Collect</button>
                  </div>
                  {vcfsCollectResult ? (
                    <div className="note" style={{fontSize:"0.82em", marginTop:"0.2em"}}>
                      {[
                        vcfsCollectResult.auto_added.length ? `+${vcfsCollectResult.auto_added.length} added` : null,
                        vcfsCollectResult.force_added.length ? `+${vcfsCollectResult.force_added.length} force-added` : null,
                        vcfsCollectResult.already_present.length ? `${vcfsCollectResult.already_present.length} already present` : null,
                      ].filter(Boolean).join(" • ") || "Up to date"}
                    </div>
                  ) : null}
                  {(() => {
                    const notCollected = step1Status.filter(s =>
                      !s.in_vcfs_folder &&
                      s.status !== "not_started" &&
                      s.status !== "running"
                    );
                    if (!notCollected.length) return null;
                    const hasAnyVcf = notCollected.some(s => s.has_zc_vcf);
                    return (
                      <details style={{marginTop:"0.3em"}}>
                        <summary style={{cursor:"pointer", fontSize:"0.82em", color:"var(--muted,#666)"}}>
                          {notCollected.length} sample{notCollected.length !== 1 ? "s" : ""} not in _VCFs
                          {hasAnyVcf ? " — check to add" : ""}
                        </summary>
                        <ul style={{listStyle:"none", padding:0, margin:"0.3em 0 0.2em 0"}}>
                          {notCollected.map(s => (
                            <li key={s.sample} style={{display:"flex", gap:"0.3em", alignItems:"center", fontSize:"0.82em", marginBottom:"0.15em"}}>
                              <input
                                type="checkbox"
                                checked={vcfsForceSet.has(s.sample)}
                                disabled={!s.has_zc_vcf}
                                onChange={(e) => {
                                  setVcfsForceSet(prev => {
                                    const next = new Set(prev);
                                    if (e.target.checked) next.add(s.sample);
                                    else next.delete(s.sample);
                                    return next;
                                  });
                                }}
                                style={{width:"auto"}}
                              />
                              <span className={`badge ${s.status}`} style={{fontSize:"0.75em"}}>{s.status.replace("_"," ")}</span>
                              <span style={{opacity: s.has_zc_vcf ? 1 : 0.5}}>{s.sample}</span>
                              {!s.has_zc_vcf ? <span className="muted" style={{fontSize:"0.78em"}}>(no VCF)</span> : null}
                            </li>
                          ))}
                        </ul>
                        {hasAnyVcf ? (
                          <button
                            style={{fontSize:"0.82em"}}
                            disabled={vcfsForceSet.size === 0}
                            onClick={() => {
                              collectVcfs(Array.from(vcfsForceSet));
                              setVcfsForceSet(new Set());
                            }}
                          >
                            Add {vcfsForceSet.size > 0 ? `${vcfsForceSet.size} ` : ""}checked to _VCFs
                          </button>
                        ) : null}
                      </details>
                    );
                  })()}
                </div>
              ) : null}
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
                    <button
                      onClick={saveExclusions}
                      disabled={!selectedProject}
                      className="ghost"
                      title="Exclusions auto-save when you toggle a checkbox; this button forces an immediate save and confirms with an alert."
                    >
                      Force-save Exclusions
                    </button>
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
                  {qcRows.length
                    ? (qcDateStart || qcDateEnd || qcFilter.trim() || showFlaggedOnly
                        ? `Showing ${computeVisibleQcRows().length} of ${qcRows.length} sample(s) for ${selectedProject}.`
                        : `Loaded ${qcRows.length} sample(s) for ${selectedProject}.`)
                    : "No stats loaded yet."}
                </div>
                <div className="row" style={{ alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={showFlaggedOnly}
                      onChange={(e) => setShowFlaggedOnly(e.target.checked)}
                    />
                    Show only flagged samples
                  </label>
                  <input
                    type="search"
                    placeholder="Filter samples…"
                    value={qcFilter}
                    onChange={(e) => setQcFilter(e.target.value)}
                    title="Case-insensitive: show only samples whose name matches as you type."
                    style={{ flex: 1, minWidth: "10rem" }}
                  />
                </div>
                <div className="row" style={{ alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                  <span className="muted" style={{ fontSize: "0.85em" }} title="Filter by the date each sample was run through Step 1.">Run date:</span>
                  <input
                    type="date"
                    value={qcDateStart}
                    max={qcDateEnd || undefined}
                    onChange={(e) => setQcDateStart(e.target.value)}
                    title="Start date (inclusive). Leave blank for open-ended."
                  />
                  <span className="muted">–</span>
                  <input
                    type="date"
                    value={qcDateEnd}
                    min={qcDateStart || undefined}
                    onChange={(e) => setQcDateEnd(e.target.value)}
                    title="End date (inclusive). Set start = end for a single day."
                  />
                  <button className="ghost" style={{ fontSize: "0.8em" }} onClick={() => setQcRangeDays(1)} title="Samples run today">Today</button>
                  <button className="ghost" style={{ fontSize: "0.8em" }} onClick={() => setQcRangeDays(7)} title="Samples run in the last 7 days">Last 7d</button>
                  <button className="ghost" style={{ fontSize: "0.8em" }} onClick={() => setQcRangeDays(30)} title="Samples run in the last 30 days">Last 30d</button>
                  {(qcDateStart || qcDateEnd) ? (
                    <button className="ghost" style={{ fontSize: "0.8em" }} onClick={clearQcDates} title="Clear the date filter">Clear dates</button>
                  ) : null}
                </div>
                {qcError ? <div className="note error">{qcError}</div> : null}
                <QcCriteriaWidget />
                <div className="qc-table scrollable">
                  <table>
                    <thead>
                      <tr>
                        <th title="Toggle Exclude for every sample currently shown (honors the date / name / flagged filters).">
                          <div className="cell-inline" style={{ gap: "4px", justifyContent: "center" }}>
                            <input
                              type="checkbox"
                              ref={(el) => { if (el) el.indeterminate = excludeAllState().indeterminate; }}
                              checked={excludeAllState().checked}
                              disabled={!computeVisibleQcRows().length}
                              onChange={(e) => toggleExcludeAllVisible(e.target.checked)}
                              title="Select / deselect Exclude for all samples in the current view"
                            />
                            <span>Exclude</span>
                          </div>
                        </th>
                        <th>QC</th>
                        <th>Sample</th>
                        <th>Run date</th>
                        <th>Files</th>
                        <th>Reference</th>
                        <th>Avg Depth</th>
                        <th>Mapping %</th>
                        <th>Zero Cov %</th>
                        <th>Dup %</th>
                        <th>R1 Q20</th>
                        <th>R2 Q20</th>
                        <th>Genome Cov</th>
                        <th>Quality SNPs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {computeVisibleQcRows()
                        .map((row) => {
                          const key = sampleKey(row);
                          const editInfo = step1Edits[key];
                          const level = qcLevel(row);
                          return (
                            <tr key={row._file} className={`qc-row qc-${level}`}>
                              <td>
                                <input
                                  type="checkbox"
                                  checked={Boolean(excluded[excludeKey(row)])}
                                  onChange={(e) => toggleExcluded(row, e.target.checked)}
                                  title="Toggling auto-saves to remove_from_analysis.xlsx; the next Step 2 run honors it via -remove_by_name"
                                />
                              </td>
                              <td><QcChip row={row} /></td>
                              <td>
                                <div className="cell-inline">
                                  <span>{row._sample || row.sample || "-"}</span>
                                  {editInfo?.edited ? <span className="badge edited">Edited</span> : null}
                                </div>
                              </td>
                              <td title={row._run_date || ""}>{qcRunDate(row) || "-"}</td>
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
                                      <button onClick={() => viewInline(editInfo.edit_log)}>Edit Log</button>
                                    ) : null}
                                    {sampleKey(row) ? (
                                      <button onClick={() => viewStep1Stats(selectedProject, sampleKey(row))}>Stats</button>
                                    ) : null}
                                    {sampleKey(row) && hasKrakenRun(selectedProject, sampleKey(row)) ? (
                                      <a
                                        className="ghost action"
                                        href={kronaHref(selectedProject, sampleKey(row))}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        title="Open the interactive Krona chart from this sample's Kraken run"
                                      >
                                        📊 Krona
                                      </a>
                                    ) : null}
                                    {sampleKey(row) ? (
                                      <button
                                        className="ghost action"
                                        title="Run Kraken ID Parse (species ID / contamination screen) on this sample"
                                        onClick={() => openKrakenModal(selectedProject, sampleKey(row))}
                                      >
                                        🧬 Kraken ID
                                      </button>
                                    ) : null}
                                  </div>
                                </details>
                              </td>
                              <td>{row.Reference || "-"}</td>
                              <td>{row["Average Depth"] || "-"}</td>
                              <td>{fmtPercent1(qcMappingRate(row))}</td>
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
                  <>
                  <QcCriteriaWidget />
                  <div className="qc-table scrollable">
                    <table>
                      <thead>
                        <tr>
                          <th>Source</th>
                          <th>QC</th>
                          <th>Sample</th>
                          <th>Files</th>
                          <th>Reference</th>
                          <th>Avg Depth</th>
                          <th>Mapping %</th>
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
                          const level = qcLevel(row);
                          return (
                          <tr key={row._file} className={`qc-row qc-${level}`}>
                            <td>{row._project || "-"}</td>
                            <td><QcChip row={row} /></td>
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
                                    <button onClick={() => viewInline(editLog, row._project)}>Edit Log</button>
                                  ) : null}
                                  {row._project && (row._sample || row.sample) ? <button onClick={() => viewStep1Stats(row._project, row._sample || row.sample)}>Stats</button> : null}
                                  {row._project && (row._sample || row.sample) && hasKrakenRun(row._project, row._sample || row.sample) ? (
                                    <a
                                      className="ghost action"
                                      href={kronaHref(row._project, row._sample || row.sample)}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title="Open the interactive Krona chart from this sample's Kraken run"
                                    >
                                      📊 Krona
                                    </a>
                                  ) : null}
                                  {row._project && (row._sample || row.sample) ? (
                                    <button
                                      className="ghost action"
                                      title="Run Kraken ID Parse (species ID / contamination screen) on this sample"
                                      onClick={() => openKrakenModal(row._project, row._sample || row.sample)}
                                    >
                                      🧬 Kraken ID
                                    </button>
                                  ) : null}
                                </div>
                              </details>
                            </td>
                            <td>{row.Reference || "-"}</td>
                            <td>{row["Average Depth"] || "-"}</td>
                            <td>{fmtPercent1(qcMappingRate(row))}</td>
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
                  </>
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
              {!folderModal.loading && !folderModal.error ? (
                <div className="kraken-cross-tool" style={{ marginTop: 16 }}>
                  <h4 style={{ marginBottom: 4 }}>Kraken ID results</h4>
                  {!folderModal.krakenPresent ? (
                    <div className="muted">No Kraken ID Parse run for this sample yet.</div>
                  ) : folderModal.krakenFiles.length === 0 ? (
                    <div className="muted">Kraken folder exists but contains no files.</div>
                  ) : (
                    <>
                      {folderModal.krakenDir ? (
                        <div className="muted" style={{ wordBreak: "break-all", marginBottom: 4 }}>{folderModal.krakenDir}</div>
                      ) : null}
                      <div className="folder-modal-files" style={{ maxHeight: "30vh", overflow: "auto" }}>
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
                            {folderModal.krakenFiles.map((f) => {
                              const isFastq = f.relpath.endsWith(".fastq.gz") || f.name?.endsWith(".fastq.gz");
                              return (
                              <tr key={f.relpath}>
                                <td style={{ wordBreak: "break-all" }}>
                                  {f.relpath}
                                  {isFastq ? <span title="Parsed reads — add to the project to re-run through Step 1" style={{ marginLeft: 6, fontSize: "10px", padding: "0 5px", borderRadius: 8, background: "#fef3c7", color: "#92400e", fontWeight: 600 }}>parsed reads</span> : null}
                                </td>
                                <td>{f.type}</td>
                                <td style={{ textAlign: "right" }}>{formatBytes(f.size)}</td>
                                <td style={{ whiteSpace: "nowrap" }}>
                                  {isFastq ? (
                                    <button
                                      onClick={() => importFastqToDownload(folderModal.project, f.path)}
                                      title="Symlink this parsed-read file into the project's download/ so it can be run through Step 1"
                                      style={{ marginRight: 6 }}
                                    >
                                      Add to project
                                    </button>
                                  ) : null}
                                  <button onClick={() => downloadFolderFile(folderModal.project, f.path)}>Download</button>
                                </td>
                              </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>
              ) : null}
              <div className="modal-actions">
                <button onClick={closeFolderModal}>Close</button>
              </div>
            </div>
          </div>
        ) : null}

        {/* Floating chip to reopen a backgrounded Kraken run. */}
        {krakenModal.running && !krakenModal.open ? (
          <button
            className="ghost action"
            onClick={() => setKrakenModal((m) => ({ ...m, open: true }))}
            title="A Kraken run is in progress — click to view its log"
            style={{ position: "fixed", right: 18, bottom: 18, zIndex: 50, boxShadow: "0 6px 20px rgba(0,0,0,0.25)" }}
          >
            <span className="pulse-dot" /> 🧬 Kraken: {krakenModal.sample} — running… (view)
          </button>
        ) : null}

        {krakenModal.open ? (
          <div className="modal-backdrop" onClick={closeKrakenModal}>
            <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
              <h3>Run Kraken ID Parse</h3>
              <div className="note">
                Sample: <strong>{krakenModal.sample}</strong>
                {krakenModal.project ? <span> (Project: {krakenModal.project})</span> : null}
                <div className="muted" style={{ marginTop: 4 }}>
                  Classifies reads against Kraken2 to identify species / screen for contamination.
                  Output is written next to this sample under <code>kraken/{krakenModal.sample}/</code> and
                  also appears in the Kraken ID Parse app.
                </div>
              </div>

              {/* Mode choice — full identification vs Kraken/Krona only */}
              <div className="kraken-mode-choice" style={{ display: "flex", flexDirection: "column", gap: 8, margin: "12px 0" }}>
                <label className={`kraken-mode-card ${krakenModal.mode === "full" ? "selected" : ""}`}
                       style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 10, cursor: krakenModal.running ? "not-allowed" : "pointer", background: krakenModal.mode === "full" ? "var(--panel-2)" : "transparent" }}>
                  <input type="radio" name="krakenMode" checked={krakenModal.mode === "full"} disabled={krakenModal.running}
                         onChange={() => setKrakenModal((m) => ({ ...m, mode: "full" }))} style={{ marginTop: 3 }} />
                  <div>
                    <div style={{ fontWeight: 700 }}>Full identification</div>
                    <div className="muted" style={{ fontSize: 12 }}>Kraken2 classify → parse target reads → assemble → BLAST. Best for confirming species. Needs a target taxon.</div>
                  </div>
                </label>
                <label className={`kraken-mode-card ${krakenModal.mode === "parse_only" ? "selected" : ""}`}
                       style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 10, cursor: krakenModal.running ? "not-allowed" : "pointer", background: krakenModal.mode === "parse_only" ? "var(--panel-2)" : "transparent" }}>
                  <input type="radio" name="krakenMode" checked={krakenModal.mode === "parse_only"} disabled={krakenModal.running}
                         onChange={() => setKrakenModal((m) => ({ ...m, mode: "parse_only" }))} style={{ marginTop: 3 }} />
                  <div>
                    <div style={{ fontWeight: 700 }}>Parse reads only (skip BLAST)</div>
                    <div className="muted" style={{ fontSize: 12 }}>Kraken2 classify → extract the target taxon's reads, then stop. Skips assembly and BLAST. The parsed reads are auto-imported into this project's inputs for re-running through vSNP. Needs a target taxon.</div>
                  </div>
                </label>
                <label className={`kraken-mode-card ${krakenModal.mode === "kraken_only" ? "selected" : ""}`}
                       style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 10, cursor: krakenModal.running ? "not-allowed" : "pointer", background: krakenModal.mode === "kraken_only" ? "var(--panel-2)" : "transparent" }}>
                  <input type="radio" name="krakenMode" checked={krakenModal.mode === "kraken_only"} disabled={krakenModal.running}
                         onChange={() => setKrakenModal((m) => ({ ...m, mode: "kraken_only" }))} style={{ marginTop: 3 }} />
                  <div>
                    <div style={{ fontWeight: 700 }}>Kraken + Krona only</div>
                    <div className="muted" style={{ fontSize: 12 }}>Quick classification + interactive Krona chart. Skips read parsing, assembly and BLAST. No target taxon needed.</div>
                  </div>
                </label>
              </div>

              {krakenModal.mode === "full" || krakenModal.mode === "parse_only" ? (
                <div style={{ marginBottom: 8 }}>
                  <label className="label">Target taxon</label>
                  <select
                    value={krakenModal.taxon}
                    disabled={krakenModal.running}
                    onChange={(e) => setKrakenModal((m) => ({ ...m, taxon: e.target.value }))}
                  >
                    <option value="">Select a target taxon…</option>
                    {krakenTaxonPresets.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                  <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                    <input
                      placeholder="Add a new taxon to the list…"
                      value={krakenNewTaxon}
                      disabled={krakenModal.running || krakenAddingTaxon}
                      onChange={(e) => setKrakenNewTaxon(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addKrakenTaxon(); } }}
                      style={{ flex: 1 }}
                    />
                    <button type="button" className="ghost"
                            onClick={addKrakenTaxon}
                            disabled={krakenModal.running || krakenAddingTaxon || !krakenNewTaxon.trim()}>
                      {krakenAddingTaxon ? "Adding…" : "+ Add"}
                    </button>
                  </div>
                  <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                    New taxa are saved to the shared list (/srv/kapurlab/tools/kraken_id_parse_gui/config/taxa.yaml) and appear in this dropdown and the Kraken ID Parse GUI.
                  </div>
                </div>
              ) : null}

              {krakenModal.log.length > 0 || krakenModal.running ? (
                <div className="log" ref={krakenLogRef} style={{ maxHeight: "32vh", overflow: "auto", marginTop: 8 }}>
                  {krakenModal.log.length === 0 ? (
                    <span className="log-placeholder">Starting…</span>
                  ) : (
                    krakenModal.log.map((line, i) => (
                      <div key={i} className={line.startsWith("ERROR") ? "log-line error" : "log-line"}>{line}</div>
                    ))
                  )}
                </div>
              ) : null}

              {krakenModal.running ? (
                <div className="note" style={{ marginTop: 8 }}>
                  This runs in the background — you can close this window and keep working.
                  Results (incl. parsed-read FASTQs) appear under the sample in the Projects list when done.
                </div>
              ) : null}
              {krakenModal.status === "succeeded" ? (
                <div className="note" style={{ marginTop: 8 }}>
                  {krakenModal.mode === "kraken_only"
                    ? <>Expand this sample in the Projects list to open the Krona graph.</>
                    : <>Parsed reads were <strong>auto-imported</strong> into this project's inputs (download/)
                       under a <code>-</code>-delimited sample name, ready to re-run through vSNP.
                       Expand this sample in the Projects list to open the Krona graph and other outputs.</>}
                </div>
              ) : null}

              <div className="modal-actions" style={{ marginTop: 12 }}>
                {krakenModal.status === "succeeded" ? <span className="muted" style={{ marginRight: "auto", color: "var(--success)" }}>✓ Finished</span> : null}
                {krakenModal.status === "failed" ? <span className="muted" style={{ marginRight: "auto", color: "var(--danger)" }}>Run failed — see log above.</span> : null}
                <button
                  onClick={runKrakenForSample}
                  disabled={krakenModal.running || ((krakenModal.mode === "full" || krakenModal.mode === "parse_only") && !krakenModal.taxon.trim())}
                >
                  {krakenModal.running ? "Running…" : krakenModal.status === "succeeded" || krakenModal.status === "failed" ? "Run again" : "▶ Run"}
                </button>
                <button className="ghost" onClick={closeKrakenModal}>
                  {krakenModal.running ? "Run in background" : "Close"}
                </button>
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
                    data-tooltip="VCF database folders for the project's reference. All subfolders are searched for *_zc.vcf and *_zc.vcf.gz. Folders are saved per-machine."
                  >
                    ?
                  </span>
                </h3>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "6px" }}>
                  <span className="muted" style={{ fontSize: "0.85em" }}>Reference:</span>
                  {importReference ? (
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: "10px",
                        background: "#e8f5e9",
                        color: "#1b5e20",
                        fontWeight: 600,
                        fontSize: "0.85em",
                      }}
                    >
                      {importReference}
                    </span>
                  ) : (
                    <span className="muted" style={{ fontSize: "0.85em" }}>
                      not set — select a reference in the Projects panel
                    </span>
                  )}
                </div>
                {selectedProject && vcfsFolderPath ? (
                  <div style={{display:"flex", alignItems:"center", gap:"6px", padding:"4px 8px", background:"var(--panel-2)", border:"1px solid var(--border)", borderRadius:"6px", fontSize:"12px", marginBottom:"6px"}}>
                    <input
                      type="checkbox"
                      checked={vcfsIncludeFolder}
                      onChange={(e) => setVcfsIncludeFolder(e.target.checked)}
                      style={{width:"auto"}}
                    />
                    <span style={{flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", fontWeight:600}} title={vcfsFolderPath}>
                      {vcfsFolderName}
                    </span>
                    <span className="muted" style={{fontSize:"11px", whiteSpace:"nowrap"}}>(n={vcfsFolderCount})</span>
                    <span style={{fontSize:"10px", padding:"1px 6px", borderRadius:"6px", background:"var(--accent)", color:"#f7faf9", textTransform:"uppercase", letterSpacing:"0.04em"}}>
                      this project
                    </span>
                  </div>
                ) : null}
                <div style={{marginTop:"8px"}}>
                  {(() => {
                    const visible = importReference
                      ? vcfDbFolders.filter((f) => (f.reference || "") === importReference)
                      : [];
                    if (!importReference) {
                      return <div className="muted" style={{fontSize:"12px", marginBottom:"8px"}}>Set the project reference in the Projects panel to see matching VCF databases.</div>;
                    }
                    if (!visible.length) {
                      return <div className="muted" style={{fontSize:"12px", marginBottom:"8px"}}>No VCF databases configured for {importReference} yet.</div>;
                    }
                    const enabledList = visible.filter((f) => f.enabled !== false);
                    const summary = (() => {
                      if (!enabledList.length) return "None selected";
                      if (enabledList.length === visible.length) return `All ${visible.length} databases selected`;
                      const names = enabledList.map((f) => f.name || (f.path || "").split("/").pop());
                      const joined = names.join(", ");
                      const truncated = joined.length > 60 ? `${joined.slice(0, 57)}…` : joined;
                      return `${enabledList.length} of ${visible.length} selected: ${truncated}`;
                    })();
                    return (
                      <div style={{position:"relative", marginBottom:"8px"}}>
                        <button
                          type="button"
                          onClick={() => setVcfDbDropdownOpen(!vcfDbDropdownOpen)}
                          className="ghost"
                          style={{width:"100%", display:"flex", alignItems:"center", justifyContent:"space-between", gap:"8px", padding:"6px 10px", fontSize:"12px", textAlign:"left"}}
                          aria-expanded={vcfDbDropdownOpen}
                        >
                          <span style={{flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>
                            {summary}
                          </span>
                          <span style={{fontSize:"10px"}}>{vcfDbDropdownOpen ? "▲" : "▼"}</span>
                        </button>
                        {vcfDbDropdownOpen ? (
                          <div style={{display:"flex", flexDirection:"column", gap:"4px", marginTop:"6px", padding:"6px", border:"1px solid var(--border)", borderRadius:"8px", background:"var(--panel)"}}>
                            {visible.map((folder, i) => {
                              const isShared = folder.scope === "shared";
                              const lname = (folder.name || "").toLowerCase();
                              const isSynthetic = lname.includes("synthetic") || lname.includes("from_assembly");
                              return (
                                <div key={folder.path || i} style={{display:"flex", alignItems:"center", gap:"6px", padding:"4px 8px", background:"var(--panel-2)", border:"1px solid var(--border)", borderRadius:"6px", fontSize:"12px"}}>
                                  <input
                                    type="checkbox"
                                    checked={folder.enabled !== false}
                                    onChange={() => toggleVcfDbFolder(folder.path)}
                                    title={isShared ? "Shared lab DB — toggle to include/exclude for your runs" : "Toggle"}
                                    style={{width:"auto"}}
                                  />
                                  <span
                                    title={folder.path}
                                    style={{flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", opacity: folder.enabled !== false ? 1 : 0.5}}
                                  >
                                    {folder.name || folder.path.split("/").pop() || folder.path}
                                    {typeof folder.sample_count === "number" ? <span className="muted" style={{marginLeft:"6px"}}>(n={folder.sample_count})</span> : null}
                                    {isSynthetic ? <span className="muted" style={{marginLeft:"6px", fontStyle:"italic"}}>[from-assembly]</span> : null}
                                  </span>
                                  {isShared ? (
                                    <span
                                      title="Shared lab DB — managed via the filesystem; toggle is per-user"
                                      style={{fontSize:"10px", padding:"1px 6px", borderRadius:"6px", background:"var(--accent)", color:"#f7faf9", textTransform:"uppercase", letterSpacing:"0.04em"}}
                                    >
                                      shared
                                    </span>
                                  ) : (
                                    <button
                                      className="chip-remove"
                                      onClick={() => removeVcfDbFolder(folder.path)}
                                      title="Remove folder"
                                    >
                                      x
                                    </button>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        ) : null}
                      </div>
                    );
                  })()}
                  <div style={{display:"flex", flexDirection:"column", gap:"4px"}}>
                    <div style={{display:"flex", gap:"4px", alignItems:"center"}}>
                      {canPickPath ? (
                        <button
                          className="ghost action"
                          style={{fontSize:"12px"}}
                          disabled={!importReference}
                          title={importReference ? "Browse for a VCF folder" : "Set the project reference first"}
                          onClick={async () => {
                            const picked = await window.vsnp.selectPath({
                              kind: "folder",
                              title: "Select VCF database folder"
                            });
                            if (picked) {
                              await addVcfDbFolder(picked, importReference);
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
                        disabled={!importReference}
                        placeholder={importReference ? `/path/to/VCFs (will tag as ${importReference})` : "Set the project reference first"}
                        title="To find a path: In Finder, right-click a folder → Get Info → copy 'Where' path, then add the folder name"
                        style={{flex:1, fontSize:"12px"}}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && manualVcfFolderPath.trim() && importReference) {
                            addVcfDbFolder(manualVcfFolderPath.trim(), importReference);
                            setManualVcfFolderPath("");
                          }
                        }}
                      />
                      <button
                        className="ghost action"
                        onClick={() => {
                          if (manualVcfFolderPath.trim() && importReference) {
                            addVcfDbFolder(manualVcfFolderPath.trim(), importReference);
                            setManualVcfFolderPath("");
                          }
                        }}
                        disabled={!manualVcfFolderPath.trim() || !importReference}
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
                    onClick={() => copyPathToClipboard(`${settings.projects_root}/${selectedProject}/step2/vcf_database`, "vcf_database path")}
                    disabled={!selectedProject}
                    title="Copy the absolute server path to clipboard — paste into the OOD Files app or scp"
                  >
                    Copy vcf_database path
                  </button>
                </div>
                <div className="note">
                  VCFs in set: {step2VcfCount}
                  {step2BuiltAt ? ` • Built at: ${step2BuiltAt}` : ""}
                </div>
                {importMismatchReport ? (
                  <button
                    className="ghost action"
                    onClick={() => viewInline(importMismatchReport)}
                  >
                    View mismatch report
                  </button>
                ) : null}
                {importStatus ? <div className="note">{importStatus}</div> : null}
                {vcfSourceSamples.length > 0 && (
                  <div style={{marginTop:"6px"}}>
                    <button
                      className="ghost action"
                      onClick={() => { setVcfSourceOpen(o => !o); setVcfSourceFilter(""); }}
                      style={{fontSize:"0.85em"}}
                    >
                      {vcfSourceOpen ? "▲ Hide" : "▼ Browse"} {vcfSourceSamples.length} samples
                    </button>
                    {vcfSourceOpen && (
                      <div style={{marginTop:"6px", border:"1px solid var(--border)", borderRadius:"4px", overflow:"hidden"}}>
                        <div style={{padding:"6px 8px", borderBottom:"1px solid var(--border)", background:"var(--surface)"}}>
                          <input
                            type="text"
                            placeholder="Filter samples…"
                            value={vcfSourceFilter}
                            onChange={e => setVcfSourceFilter(e.target.value)}
                            style={{width:"100%", boxSizing:"border-box", fontSize:"0.85em", padding:"3px 6px"}}
                            autoFocus
                          />
                        </div>
                        <div style={{maxHeight:"320px", overflowY:"auto", fontSize:"0.8em", fontFamily:"monospace"}}>
                          {(() => {
                            const q = vcfSourceFilter.trim().toLowerCase();
                            const filtered = q
                              ? vcfSourceSamples.filter(s => s.sample.toLowerCase().includes(q) || s.filename.toLowerCase().includes(q))
                              : vcfSourceSamples;
                            const excludedCount = vcfSourceSamples.filter(s => step2BuildExcluded[s.sample] || step2QcExcluded[s.sample]).length;
                            return (
                              <>
                                <div style={{padding:"3px 8px", fontSize:"0.9em", fontFamily:"sans-serif", color:"var(--muted)", borderBottom:"1px solid var(--border)", background:"var(--surface)"}}>
                                  {filtered.length === vcfSourceSamples.length
                                    ? `${filtered.length} samples`
                                    : `${filtered.length} of ${vcfSourceSamples.length} samples`}
                                  {excludedCount > 0 && (
                                    <span style={{color:"var(--danger, #a94442)"}}> · {excludedCount} excluded from Step 2</span>
                                  )}
                                </div>
                                {filtered.map(s => {
                                  const lockedByQc = !!step2QcExcluded[s.sample];
                                  const isExcluded = !!step2BuildExcluded[s.sample] || lockedByQc;
                                  const metaLabel = step2BuildMeta[s.sample];
                                  return (
                                  <div key={s.filename} title={s.filename} style={{display:"flex", alignItems:"center", gap:"8px", padding:"2px 8px", borderBottom:"1px solid var(--border)", opacity: isExcluded ? 0.55 : 1}}>
                                    <input
                                      type="checkbox"
                                      checked={isExcluded}
                                      disabled={lockedByQc}
                                      onChange={e => toggleStep2BuildExcluded(s.sample, e.target.checked)}
                                      title={lockedByQc
                                        ? "Excluded in Step 1 Results — change it there to include in Step 2"
                                        : (isExcluded ? "Excluded from Step 2 — uncheck to include" : "Exclude this sample from Step 2")}
                                      style={{flexShrink:0, cursor: lockedByQc ? "not-allowed" : "pointer"}}
                                    />
                                    <span style={{flex:"1 1 auto", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", textDecoration: isExcluded ? "line-through" : "none"}}>
                                      {s.sample}
                                      {lockedByQc && (
                                        <span style={{color:"var(--danger, #a94442)", fontFamily:"sans-serif", fontStyle:"italic"}}> — excluded in Step 1</span>
                                      )}
                                      {metaLabel && metaLabel !== s.sample && (
                                        <span style={{color:"var(--muted)", fontFamily:"sans-serif", fontStyle:"italic"}}> — {metaLabel}</span>
                                      )}
                                    </span>
                                    {s.source_type && (
                                      <span style={{
                                        flexShrink:0,
                                        fontSize:"0.8em",
                                        padding:"0 4px",
                                        borderRadius:"3px",
                                        background: s.source_type === "step1" ? "var(--accent-subtle, #dff0d8)" : "var(--info-subtle, #d9edf7)",
                                        color: s.source_type === "step1" ? "var(--accent-dark, #3c763d)" : "var(--info-dark, #31708f)",
                                      }}>
                                        {s.source_type === "step1" ? "step1" : "ref db"}
                                      </span>
                                    )}
                                  </div>
                                  );
                                })}
                                {filtered.length === 0 && (
                                  <div style={{padding:"8px", color:"var(--muted)", fontFamily:"sans-serif"}}>No samples match</div>
                                )}
                              </>
                            );
                          })()}
                        </div>
                      </div>
                    )}
                  </div>
                )}
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
                {(reference || projectReference) ? (
                  <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.4rem" }}>
                    <span className="muted" style={{ fontSize: "0.8em" }}>Reference:</span>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: "10px",
                        background: "#e8f5e9",
                        color: "#1b5e20",
                        fontWeight: 600,
                        fontSize: "0.85em",
                      }}
                    >
                      {reference || projectReference}
                    </span>
                    {projectReference && !reference ? (
                      <span className="muted" style={{ fontSize: "0.75em" }}>(from project)</span>
                    ) : null}
                  </div>
                ) : null}
                <button
                  onClick={step2Run}
                  disabled={
                    !selectedProject ||
                    !settingsReady ||
                    (!reference && !projectReference) ||
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
            {step2Runs.length > 0 ? (
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
                <label style={{ fontWeight: 600, whiteSpace: "nowrap", fontSize: "0.9em" }}>Comparison:</label>
                <select
                  value={step2SelectedRun || ""}
                  onChange={(e) => setStep2SelectedRun(e.target.value || null)}
                  style={{ flex: 1 }}
                >
                  {step2Runs.map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {r.run_id === "legacy" ? "Legacy (flat)" : r.run_id.replace(/_/g, " ").replace("T", " ")}
                      {r.status === "ok" ? " ✓" : r.status === "failed" ? " ✗" : r.status === "running" ? " …" : ""}
                      {r.group_count > 0 ? ` (${r.group_count} groups)` : ""}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            {step2EditedCount > 0 && settings.projects_root && selectedProject ? (
              <div className="note">
                Edited samples included in this run.{" "}
                <button
                  className="link-button"
                  onClick={() =>
                    viewInline(`${settings.projects_root}/${selectedProject}/step2/edited_samples.json`)
                  }
                >
                  View edited sample list
                </button>
              </div>
            ) : null}
            {posthocRunError ? <div className="note error">{posthocRunError}</div> : null}
            {step2RunId ? <div className="note">Run ID: {step2RunId}</div> : null}
            {step2OutputsError ? <div className="note error">{step2OutputsError}</div> : null}
            {(() => {
              const snpTool = posthocTools.find((tool) => tool.id === "snp_analysis");
              const snpToolAvailable = snpTool ? snpTool.available : true;
              const snpToolMissing = snpTool && !snpTool.available;
              const shouldHideOutput = (item) => {
                const path = item?.path || "";
                const fileName = path.split("/").pop() || "";
                if (fileName.startsWith("~$")) return true;
                if (fileName.endsWith(".lock")) return true;
                if (fileName === "filtered_step1.fasta") return true;
                if (fileName === "snp_distances.txt") return true;
                if (fileName === "snp_matrix.tsv") return true;
                if (fileName === "stats.json") return true;
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
              // Step 2 group search: greedy, case-insensitive match against the
              // sample names listed for each group in the run summary HTML
              // (names usually carry metadata, e.g. a state code), so typing
              // "tx" surfaces every group containing a TX sample.
              const groupSearchAvailable = Object.keys(step2Groupings).length > 0;
              const groupQuery = step2GroupSearch.trim().toLowerCase();
              const searchedStep2Groups = (groupQuery && groupSearchAvailable)
                ? sortedStep2Groups.filter((g) =>
                    (step2Groupings[g.name] || []).some((m) => m.toLowerCase().includes(groupQuery))
                  )
                : sortedStep2Groups;
              return (
                <>
                  {snpToolMissing ? (
                    <div className="note warning">
                      SNP Analysis unavailable: missing {snpTool.missing?.join(", ") || "dependencies"}.
                    </div>
                  ) : null}
                  <div className="results-list">
                  {sortedStep2Outputs.length ? (
                    sortedStep2Outputs.map((item) => (
                      <div key={item.path} className="results-item">
                        <div className="results-main">
                      <div className="results-name">{item.label}</div>
                      <div className="results-path">{item.path}</div>
                    </div>
                    <div className="results-actions">
                      {(() => {
                        const mode = fileViewMode(item.path);
                        if (mode === "xlsx") {
                          return <button onClick={() => previewXlsx(item.path)} title="View formatted xlsx in a new tab">View</button>;
                        }
                        if (mode === "inline") {
                          return <button onClick={() => viewInline(item.path)} title="Open in a new browser tab">View</button>;
                        }
                        return null;
                      })()}
                      <button onClick={() => downloadOutput(item.path, item.download_name)} title="Download file">DL</button>
                    </div>
                  </div>
                ))
              ) : null}
              {sortedStep2Groups.length && groupSearchAvailable ? (
                <div style={{display:"flex", alignItems:"center", gap:"0.5rem", margin:"0.4rem 0"}}>
                  <input
                    type="text"
                    placeholder="Search groups by sample (e.g. TX)…"
                    value={step2GroupSearch}
                    onChange={(e) => setStep2GroupSearch(e.target.value)}
                    style={{flex:1, fontSize:"0.9em", padding:"3px 6px", boxSizing:"border-box"}}
                  />
                  {groupQuery ? (
                    <span style={{fontSize:"0.85em", color:"var(--muted)", whiteSpace:"nowrap"}}>
                      {searchedStep2Groups.length} of {sortedStep2Groups.length}
                    </span>
                  ) : null}
                  {step2GroupSearch ? (
                    <button className="ghost action" style={{fontSize:"0.85em"}} onClick={() => setStep2GroupSearch("")}>Clear</button>
                  ) : null}
                </div>
              ) : null}
              {sortedStep2Groups.length ? (
                <div className="results-groups">
                  {groupQuery && groupSearchAvailable && searchedStep2Groups.length === 0 ? (
                    <div className="note" style={{margin:"0.3rem 0"}}>No groups contain a sample matching “{step2GroupSearch.trim()}”.</div>
                  ) : null}
                  {searchedStep2Groups.map((group) => (
                    <details key={group.name} className="results-group">
                      <summary>
                        <div className="group-summary">
                          <span>{group.name}</span>
                          <div className="group-actions">
                            {(() => {
                              const canRunPosthoc = snpToolAvailable && group.posthoc_possible;
                              const hasPosthocOutputs = posthocStatus[group.name]?.outputs?.some((o) => o.exists);
                              if (!canRunPosthoc && !hasPosthocOutputs) {
                                return null;
                              }
                              const posthocTitle = "Runs post-hoc SNP distance/KDP using the group FASTA.";
                              return (
                                <>
                                  {hasPosthocOutputs ? (
                                    <span className="group-chip">posthoc ready</span>
                                  ) : null}
                                  {canRunPosthoc ? (
                                    <>
                                      <select
                                        className="small-select"
                                        value={posthocScopeByGroup[group.name] || "all"}
                                        title="Choose isolates for SNP analysis (Step 1 only excludes reference VCFs)."
                                        onChange={(e) =>
                                          setPosthocScopeByGroup((prev) => ({
                                            ...prev,
                                            [group.name]: e.target.value
                                          }))
                                        }
                                      >
                                        <option value="all">All VCFs</option>
                                        <option value="step1_only">Step 1 only</option>
                                      </select>
                                      <button
                                        className="small"
                                        disabled={posthocStatus[group.name]?.running}
                                        title={posthocStatus[group.name]?.running ? "SNP Analysis is running." : posthocTitle}
                                        onClick={(e) => {
                                          e.preventDefault();
                                          e.stopPropagation();
                                          runPosthoc(group.name);
                                        }}
                                      >
                                        {posthocStatus[group.name]?.running ? "SNP Analysis (running)" : "SNP Analysis"}
                                      </button>
                                    </>
                                  ) : null}
                                </>
                              );
                            })()}
                          </div>
                        </div>
                      </summary>
                      {group.files.map((item) => {
                        const isTre = (item.label || "").toLowerCase().endsWith(".tre");
                        const treeBase = window.location.pathname.replace(/[^/]*$/, "");
                        const treeUrl = isTre ? `${treeBase}?view=tree&project=${encodeURIComponent(selectedProject)}&path=${encodeURIComponent(item.path)}` : "";
                        return (
                          <div key={item.path} className="results-item">
                            <div className="results-main">
                              <div className="results-name">{item.label}</div>
                              <div className="results-path">{item.path}</div>
                            </div>
                            <div className="results-actions">
                              {(() => {
                                const mode = fileViewMode(item.path);
                                if (mode === "tree") {
                                  return <button onClick={() => window.open(treeUrl, "_blank", "noopener")} title="Open tree viewer in a new tab">View tree</button>;
                                }
                                if (mode === "xlsx") {
                                  return <button onClick={() => previewXlsx(item.path)} title="View formatted xlsx in a new tab">View</button>;
                                }
                                if (mode === "inline") {
                                  return <button onClick={() => viewInline(item.path)} title="Open in a new browser tab">View</button>;
                                }
                                return null;
                              })()}
                              <button onClick={() => downloadOutput(item.path, item.download_name)} title="Download file">DL</button>
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

      {folderBrowser.open && (
        <div
          onClick={() => setFolderBrowser((s) => ({ ...s, open: false }))}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--panel, #fff)", color: "inherit", borderRadius: 10, width: "min(640px, 92vw)", maxHeight: "80vh", display: "flex", flexDirection: "column", boxShadow: "0 10px 40px rgba(0,0,0,0.3)" }}
          >
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border, #ddd)", fontWeight: 700 }}>
              Select a projects root
            </div>
            <div style={{ padding: "10px 16px", display: "flex", gap: 6, alignItems: "center" }}>
              <button type="button" className="ghost" disabled={!folderBrowser.parent || folderBrowser.loading} onClick={() => browseDirs(folderBrowser.parent)}>↑ Up</button>
              <input
                style={{ flex: 1 }}
                value={folderBrowser.path}
                onChange={(e) => setFolderBrowser((s) => ({ ...s, path: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); browseDirs(folderBrowser.path); } }}
              />
              <button type="button" className="ghost" onClick={() => browseDirs(folderBrowser.path)}>Go</button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "0 16px", minHeight: 160 }}>
              {folderBrowser.loading ? (
                <div className="muted" style={{ padding: 12 }}>Loading…</div>
              ) : folderBrowser.error ? (
                <div className="muted" style={{ padding: 12, color: "var(--danger, #c00)" }}>{folderBrowser.error}</div>
              ) : folderBrowser.entries.length === 0 ? (
                <div className="muted" style={{ padding: 12 }}>No sub-folders here.</div>
              ) : (
                folderBrowser.entries.map((e) => (
                  <div
                    key={e.path}
                    onClick={() => browseDirs(e.path)}
                    style={{ padding: "7px 8px", cursor: "pointer", borderRadius: 6, display: "flex", gap: 8, alignItems: "center" }}
                    onMouseEnter={(ev) => (ev.currentTarget.style.background = "var(--panel-2, #f0f0f0)")}
                    onMouseLeave={(ev) => (ev.currentTarget.style.background = "transparent")}
                  >
                    <span>📁</span><span>{e.name}</span>
                  </div>
                ))
              )}
            </div>
            <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border, #ddd)", display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" className="ghost" onClick={() => setFolderBrowser((s) => ({ ...s, open: false }))}>Cancel</button>
              <button type="button" onClick={chooseFolder} disabled={folderBrowser.loading || !folderBrowser.path}>Select this folder</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
