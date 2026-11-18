"""Pathogen add-ons: find them, read their manifests, run their hooks.

A pathogen add-on (FMDV first, then HPAI, ASF, ...) is a separate repository
that bdtools installs beside this one. vsnp_gui stays a tool for running vsnp3
and viewing its results; everything specific to a pathogen (its reference
library, how a sample's reference is chosen, typing, trees, reports) lives in
the add-on and is reached only through the hooks below. A project names its
pathogen in project.json ("pathogen": "<id>"); a project that names none runs
exactly as it always has.

Contract version 1 (the umbrella's docs/BUILDING_AN_ADDON.md is the prose;
app.addon_check validates an add-on against it):

  <addon>/addon.json           the manifest (see validate_manifest)
  <addon>/hooks/<hook>.py      run with the add-on env's own python
  <library>/reference_options/ vsnp3 reference folders the chooser returns
  <library>/vcf_db_folders/<ref>/<db>/  Step 2 reference databases, per reference

Every hook gets ONE argument, ``--context <file.json>``, and runs with its
output folder as the working directory. The context holds everything a hook
may use; an add-on never reads this app's config or imports its modules.
Outputs, per hook:

  choose_reference  <out>/reference.json  {"reference_dir": abs path, ...}
  prepare_reads     <out>/reads.json      {"reads": [R1, R2?], ...}
  after_sample      <out>/result.json     one Results row (metrics, flags, files)
  after_run         <pathogen>/summary.json, plus its run report
  actions           <run>/action.json     {"status", "files", "summary"}

where <pathogen> is <project>/pathogen/<id>/, <out> is <pathogen>/samples/<sample>/
and <run> is <pathogen>/runs/<stamp>_<action>/. Outputs live under the project (the tree viewer and
the xlsx preview open only files under it), never under step2/<run>/ (every
folder there is listed as a vSNP group). The library is read-only at run time.

Only stdlib and app.config are imported here, so the batch script's helper
(app.addon_run) loads in a fraction of a second without the web app.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config as _config

CONTRACT = 1
HOOKS = ("choose_reference", "prepare_reads", "after_sample", "after_run")
REQUIRED_HOOKS = ("choose_reference", "after_sample")
ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
# A reference folder name vsnp3 keeps as given: no dots (vsnp3 cuts a name at
# its first ".", so MTBC0_v1.1 becomes MTBC0_v1), no spaces, nothing a shell
# or a URL would treat specially.
REF_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
MANIFEST_NAME = "addon.json"
PATHOGEN_DIRNAME = "pathogen"

# Tests point discovery somewhere else; None means the bdtools tools root.
ROOTS_OVERRIDE: Optional[List[Path]] = None
_CACHE: Dict[str, Any] = {"at": 0.0, "key": None, "addons": None}
_CACHE_TTL = 5.0


@dataclass
class Addon:
    id: str
    name: str
    version: str
    dir: Path
    manifest: Dict[str, Any]
    tool: str = ""                       # its bdtools/tools.yml name, e.g. fmdv_addon
    env_dir: Optional[Path] = None
    python: Optional[Path] = None
    arch_prefix: List[str] = field(default_factory=list)
    library: Optional[Path] = None
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.problems

    def hook_path(self, hook: str) -> Optional[Path]:
        rel = (self.manifest.get("hooks") or {}).get(hook)
        return (self.dir / rel) if rel else None

    def public(self) -> Dict[str, Any]:
        """What the page is told about this add-on."""
        m = self.manifest
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "description": m.get("description", ""),
            "ready": self.ready, "problems": list(self.problems), "notes": list(self.notes),
            "dir": str(self.dir), "library": str(self.library) if self.library else None,
            "env": str(self.env_dir) if self.env_dir else None,
            "columns": m.get("columns") or [],
            "actions": [{k: a.get(k) for k in ("id", "label", "description", "params")}
                        for a in (m.get("actions") or [])],
            "reports": m.get("reports") or {},
            "scope": {k: v for k, v in (m.get("scopes") or {}).items() if k != "file"},
            "hooks": sorted((m.get("hooks") or {}).keys()),
            "sample_naming": m.get("sample_naming", "folder"),
        }


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def validate_manifest(m: Any, addon_dir: Optional[Path] = None) -> List[str]:
    """Problems with an addon.json, in words. Empty when it satisfies contract 1."""
    p: List[str] = []
    if not isinstance(m, dict):
        return ["addon.json is not a JSON object"]
    if m.get("contract") != CONTRACT:
        p.append(f"contract is {m.get('contract')!r}; this vsnp_gui speaks contract {CONTRACT}")
    aid = m.get("id")
    if not isinstance(aid, str) or not ID_RE.fullmatch(aid):
        p.append(f"id {aid!r} must be lower-case letters, digits and _ (start with a letter, at most 32)")
    for k in ("name", "version"):
        if not isinstance(m.get(k), str) or not m.get(k).strip():
            p.append(f"{k} is missing")
    if m.get("consumer", "vsnp_gui") != "vsnp_gui":
        p.append(f"consumer is {m.get('consumer')!r}, not vsnp_gui")
    hooks = m.get("hooks")
    if not isinstance(hooks, dict):
        p.append("hooks is missing")
        hooks = {}
    for h in REQUIRED_HOOKS:
        if h not in hooks:
            p.append(f"hook {h} is required")
    for h, rel in hooks.items():
        if h not in HOOKS:
            p.append(f"unknown hook {h!r} (known: {', '.join(HOOKS)})")
            continue
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts or not rel.endswith(".py"):
            p.append(f"hook {h}: {rel!r} must be a .py path inside the add-on")
        elif addon_dir is not None and not (addon_dir / rel).is_file():
            p.append(f"hook {h}: {rel} does not exist")
    seen = set()
    for a in m.get("actions") or []:
        aid_ = a.get("id") if isinstance(a, dict) else None
        if not isinstance(aid_, str) or not ID_RE.fullmatch(aid_):
            p.append(f"action id {aid_!r} must match {ID_RE.pattern}")
            continue
        if aid_ in seen:
            p.append(f"action {aid_} is listed twice")
        seen.add(aid_)
        rel = a.get("command", "")
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts or not rel.endswith(".py"):
            p.append(f"action {aid_}: command {rel!r} must be a .py path inside the add-on")
        elif addon_dir is not None and not (addon_dir / rel).is_file():
            p.append(f"action {aid_}: {rel} does not exist")
    sc = m.get("scopes") or {}
    if sc and not isinstance(sc, dict):
        p.append("scopes must be an object")
    elif sc.get("file") and (str(sc["file"]).startswith("/") or ".." in Path(str(sc["file"])).parts):
        p.append(f"scopes.file {sc['file']!r} must be a path inside the library")
    lib = m.get("library") or {}
    if not isinstance(lib.get("markers", []), list):
        p.append("library.markers must be a list")
    for c in m.get("columns") or []:
        if not isinstance(c, dict) or not c.get("key") or not c.get("label"):
            p.append(f"column {c!r} needs a key and a label")
    th = m.get("qc_thresholds")
    if th is not None and not isinstance(th, dict):
        p.append("qc_thresholds must be an object")
    return p


def read_manifest(addon_dir: Path) -> Dict[str, Any]:
    with open(addon_dir / MANIFEST_NAME, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# Where things are
# --------------------------------------------------------------------------

def roots() -> List[Path]:
    """Where installed add-ons are looked for: the folder holding the bdtools
    checkouts (BDTOOLS_TOOLS_ROOT, which the launcher exports as the parent of
    THIS checkout), so an add-on is found the way the Kraken sibling is."""
    if ROOTS_OVERRIDE is not None:
        return list(ROOTS_OVERRIDE)
    return [Path(_config.TOOLS_ROOT)]


def _conda_bases() -> List[Path]:
    out: List[Path] = []
    exe = os.environ.get("CONDA_EXE", "").strip()
    if exe:
        out.append(Path(exe).resolve().parent.parent)
    home = Path.home()
    for b in ("miniforge3", "miniconda3", "anaconda3", "mambaforge"):
        out.append(home / b)
    seen, uniq = set(), []
    for b in out:
        if str(b) not in seen and b.is_dir():
            seen.add(str(b))
            uniq.append(b)
    return uniq


def _env_candidates(addon: "Addon") -> List[tuple]:
    """(source, env dir) in resolution order — the generalisation of the
    Kraken sibling's resolver: the launcher's own answer, the checkout's env,
    the installed checkout's env when running from a source tree, a named env."""
    out = []
    tool = addon.tool or addon.dir.name
    var = "BDTOOLS_SIBLING_ENV_" + re.sub(r"[^A-Za-z0-9]", "_", tool).upper()
    sib = os.environ.get(var, "").strip()
    if sib:
        out.append((var, Path(sib)))
    out.append(("add-on env", addon.dir / "env"))
    home = os.environ.get("BDTOOLS_HOME", "").strip()
    if os.environ.get("BDTOOLS_TOOLSDIR", "").strip():
        base = Path(home) if home else Path.home() / ".local" / "share" / "bdtools"
        out.append(("installed checkout", base / "checkouts" / tool / "env"))
    env_name = addon.manifest.get("env") or tool
    for b in _conda_bases():
        out.append((f"conda base {b}", b / "envs" / env_name))
    return out


def _arch_pin(env_dir: Path) -> List[str]:
    """/usr/bin/arch prefix for an env's interpreter on macOS: the subdir most
    of its conda-meta records name. An add-on env is built osx-64 on Apple
    Silicon (bdtools rule 2) while this app's env may be osx-arm64, and a
    universal interpreter would otherwise pick the caller's slice."""
    if sys.platform != "darwin":
        return []
    counts: Dict[str, int] = {}
    try:
        for f in (env_dir / "conda-meta").glob("*.json"):
            try:
                sub = json.loads(f.read_text(encoding="utf-8")).get("subdir", "")
            except (OSError, ValueError):
                continue
            if sub in ("osx-64", "osx-arm64"):
                counts[sub] = counts.get(sub, 0) + 1
    except OSError:
        return []
    if not counts or not Path("/usr/bin/arch").exists():
        return []
    best = max(counts, key=counts.get)
    return ["/usr/bin/arch", "-x86_64" if best == "osx-64" else "-arm64"]


def library_dir(addon: "Addon") -> Optional[Path]:
    """Where the add-on's reference library is. In order: the path its installer
    recorded (<addon>/.library_path), <db root>/addons/<id> (where bdtools hands
    deploy/build_library.sh BDTOOLS_DB_ROOT), <addon>/library."""
    rec = addon.dir / ".library_path"
    try:
        txt = rec.read_text(encoding="utf-8").strip()
        if txt:
            return Path(txt)
    except OSError:
        pass
    db = Path(os.environ.get("BDTOOLS_DB_ROOT", "").strip() or str(_config.DB_ROOT))
    cand = db / "addons" / addon.id
    if cand.is_dir():
        return cand
    return addon.dir / "library"


def _resolve(addon: "Addon") -> None:
    for source, cand in _env_candidates(addon):
        py = cand / "bin" / "python"
        if py.is_file():
            addon.env_dir, addon.python = cand, py
            addon.arch_prefix = _arch_pin(cand)
            if source != "add-on env":
                addon.notes.append(f"env from {source}: {cand}")
            break
    else:
        addon.problems.append("no environment: none of " + ", ".join(
            str(c) for _s, c in _env_candidates(addon)) + " has a python. Install it with "
            f"bdtools install {addon.tool or addon.dir.name}.")
    lib = library_dir(addon)
    addon.library = lib
    markers = list((addon.manifest.get("library") or {}).get("markers") or [])
    missing = [mk for mk in markers if not (lib / mk).exists()]
    if missing:
        addon.problems.append(f"reference library not built at {lib} (missing {', '.join(missing)}). "
                              f"bdtools install {addon.tool or addon.dir.name} builds it.")


def discover(refresh: bool = False) -> Dict[str, Addon]:
    """{id: Addon} for every add-on under the roots. Cached for a few seconds:
    the Step 1 page asks on every project switch."""
    key = tuple(str(r) for r in roots())
    now = time.time()
    if (not refresh and _CACHE["addons"] is not None and _CACHE["key"] == key
            and now - _CACHE["at"] < _CACHE_TTL):
        return _CACHE["addons"]
    found: Dict[str, Addon] = {}
    dupes: Dict[str, List[Path]] = {}
    for root in roots():
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in children:
            if not (d / MANIFEST_NAME).is_file():
                continue
            try:
                m = read_manifest(d)
            except (OSError, ValueError) as e:
                aid = d.name
                found.setdefault(aid, Addon(id=aid, name=aid, version="?", dir=d, manifest={},
                                            problems=[f"addon.json unreadable: {e}"]))
                continue
            aid = m.get("id") if isinstance(m, dict) else None
            aid = aid if isinstance(aid, str) and ID_RE.fullmatch(aid) else d.name
            a = Addon(id=aid, name=str(m.get("name") or aid), version=str(m.get("version") or "?"),
                      dir=d, manifest=m, tool=str(m.get("tool") or d.name))
            a.problems.extend(validate_manifest(m, d))
            prev = found.get(aid)
            if prev is not None:
                # Two folders declare the same id (a source tree beside the
                # installed checkout): the one named as its tools.yml entry wins.
                dupes.setdefault(aid, [prev.dir]).append(d)
                if prev.dir.name == prev.tool and d.name != a.tool:
                    continue
                if d.name == a.tool and prev.dir.name != prev.tool:
                    found[aid] = a
                continue
            found[aid] = a
    for aid, dirs in dupes.items():
        found[aid].notes.append("also declared by " + ", ".join(
            str(x) for x in dirs if x != found[aid].dir) + " (ignored)")
    for a in found.values():
        if not a.problems:
            _resolve(a)
    _CACHE.update(at=now, key=key, addons=found)
    return found


def get(addon_id: Optional[str]) -> Optional[Addon]:
    if not addon_id:
        return None
    return discover().get(addon_id)


def scopes(addon: "Addon") -> Dict[str, Any]:
    """The scope tree for the project form's picker: the library's scopes file
    (manifest scopes.file), built with the library — never a command run per
    page load. {"label", "default", "options": [{"value", "label",
    "children": [...]}]}; {} when the add-on has no scopes."""
    sc = addon.manifest.get("scopes") or {}
    rel = sc.get("file")
    out: Dict[str, Any] = {"label": sc.get("label", "Scope"), "default": sc.get("default", ""),
                           "options": []}
    if not rel or addon.library is None:
        return out
    data = read_json(addon.library / rel)
    if data:
        out["options"] = data.get("options") or []
        out["default"] = data.get("default", out["default"])
        out["label"] = data.get("label", out["label"])
    return out


SCOPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:+-]{0,127}")


def valid_scope(value: Optional[str]) -> bool:
    """A scope is a token the add-on defined in its scopes file; here only its
    shape is checked (it travels in the context file and project.json)."""
    if value in (None, ""):
        return True
    return bool(SCOPE_RE.fullmatch(value)) and ".." not in value


def reference_locations() -> List[tuple]:
    """[(id, <library>/reference_options)] for every ready add-on."""
    out = []
    for a in discover().values():
        if a.ready and a.library is not None:
            rel = (a.manifest.get("library") or {}).get("reference_options", "reference_options")
            p = a.library / rel
            if p.is_dir():
                out.append((a.id, p))
    return out


def vcf_db_roots() -> List[tuple]:
    """[(id, <library>/vcf_db_folders)] for every ready add-on that ships Step 2
    reference databases (one folder per reference, one per database below)."""
    out = []
    for a in discover().values():
        if a.ready and a.library is not None:
            rel = (a.manifest.get("library") or {}).get("vcf_db_folders", "vcf_db_folders")
            p = a.library / rel
            if p.is_dir():
                out.append((a.id, p))
    return out


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

def _project_meta(project_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def project_pathogen(project_dir: Path) -> Optional[str]:
    """The add-on id a project names, or None for a stock project."""
    val = _project_meta(project_dir).get("pathogen")
    return val if isinstance(val, str) and ID_RE.fullmatch(val) else None


def project_scope(project_dir: Path) -> str:
    val = _project_meta(project_dir).get("pathogen_scope")
    return val if isinstance(val, str) else ""


def pathogen_dir(project_dir: Path, addon_id: str) -> Path:
    return project_dir / PATHOGEN_DIRNAME / addon_id


def sample_out_dir(project_dir: Path, addon_id: str, sample: str) -> Path:
    return pathogen_dir(project_dir, addon_id) / "samples" / sample


def runs_dir(project_dir: Path, addon_id: str) -> Path:
    return pathogen_dir(project_dir, addon_id) / "runs"


def default_pathogen() -> Optional[str]:
    """The site's default for new projects (bdtools site.conf DEFAULT_PATHOGEN,
    exported as BDTOOLS_DEFAULT_PATHOGEN), or None."""
    val = os.environ.get("BDTOOLS_DEFAULT_PATHOGEN", "").strip()
    return val if ID_RE.fullmatch(val or "") else None


def merged_thresholds(cfg: Dict[str, Any], project_dir: Optional[Path]) -> Dict[str, Any]:
    """QC thresholds, lowest layer first: module defaults < the user's config <
    the project's add-on < the project's own project.json. The add-on layer is
    what makes "genome covered" a signal for an 8 kb amplicon virus while a
    bacterial project never sees it."""
    import copy
    from app import qc_verdict
    meta = _project_meta(project_dir) if project_dir is not None else {}
    addon = get(meta.get("pathogen")) if isinstance(meta.get("pathogen"), str) else None
    layers = [cfg.get("qc_thresholds"),
              (addon.manifest.get("qc_thresholds") if addon is not None else None),
              meta.get("qc_thresholds")]
    # Copies: merge_thresholds assigns a block it has no default for (e.g.
    # genome_coverage) by reference, and a later layer would then edit the
    # add-on's manifest in place.
    return qc_verdict.merge_thresholds(*[copy.deepcopy(x) for x in layers if x])


# --------------------------------------------------------------------------
# Running a hook
# --------------------------------------------------------------------------

class HookFailed(RuntimeError):
    pass


def hook_command(addon: Addon, script: Path, context_path: Path) -> List[str]:
    if addon.python is None:
        raise HookFailed(f"{addon.id}: no environment to run {script.name} in")
    return list(addon.arch_prefix) + [str(addon.python), "-u", str(script), "--context", str(context_path)]


def hook_env(addon: Addon) -> Dict[str, str]:
    """The environment a hook runs in: this one, with the add-on env's bin first
    on PATH so the programs it calls are the ones its env pins, and nothing of
    this app's on PYTHONPATH."""
    env = dict(os.environ)
    if addon.env_dir is not None:
        env["PATH"] = str(addon.env_dir / "bin") + os.pathsep + env.get("PATH", "")
        env["CONDA_PREFIX"] = str(addon.env_dir)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["VSNP_GUI_ADDON_CONTRACT"] = str(CONTRACT)
    return env


def run_script(addon: Addon, script: Path, context: Dict[str, Any], workdir: Path,
               log_path: Optional[Path] = None, timeout: Optional[float] = None,
               context_name: Optional[str] = None) -> Dict[str, Any]:
    """Write the context, run the script in the add-on env, return
    {returncode, command, started_at, finished_at, log}. Never raises for a
    non-zero exit: what a failure means is the caller's (see addon_run)."""
    workdir.mkdir(parents=True, exist_ok=True)
    ctx_path = workdir / (context_name or f".context_{script.stem}.json")
    tmp = ctx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(context, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, ctx_path)
    cmd = hook_command(addon, script, ctx_path)
    started = time.time()
    log_fh = open(log_path, "a", encoding="utf-8") if log_path else None
    try:
        if log_fh:
            log_fh.write(f"[{addon.id} {addon.version}] {' '.join(shlex.quote(c) for c in cmd)}\n")
            log_fh.flush()
        try:
            proc = subprocess.run(cmd, cwd=str(workdir), env=hook_env(addon),
                                  stdout=log_fh or subprocess.DEVNULL,
                                  stderr=subprocess.STDOUT if log_fh else subprocess.DEVNULL,
                                  timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = 124
            if log_fh:
                log_fh.write(f"[{addon.id}] {script.name} timed out after {timeout:g}s\n")
        except OSError as e:
            rc = 127
            if log_fh:
                log_fh.write(f"[{addon.id}] could not start {script.name}: {e}\n")
    finally:
        if log_fh:
            log_fh.close()
    return {"returncode": rc, "command": cmd, "started_at": started, "finished_at": time.time(),
            "log": str(log_path) if log_path else None, "context": str(ctx_path)}


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        val = json.loads(path.read_text(encoding="utf-8"))
        return val if isinstance(val, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def addon_identity(addon: Addon) -> Dict[str, Any]:
    """id, version and the checkout's commit, for provenance."""
    commit = ""
    try:
        commit = subprocess.run(["git", "-C", str(addon.dir), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"id": addon.id, "name": addon.name, "version": addon.version, "dir": str(addon.dir),
            "commit": commit, "env": str(addon.env_dir) if addon.env_dir else None,
            "library": str(addon.library) if addon.library else None, "contract": CONTRACT}
