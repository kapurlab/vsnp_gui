# R1 — Ops/Deployment (adversarial)

## Attack vectors

### 1. `run_in_conda_env` activation is unspecified and will silently fail inside Apptainer
- Evidence: §7 ("single-file change" claim); CLAUDE.md §5 — uvicorn runs inside Apptainer `--pid` namespace; `runners.py` is never shown
- Severity: blocker
- Falsification: Show `run_in_conda_env` source that works when called from a process tree already inside Apptainer with no conda shell hook initialized.
- Detail: Activating a conda env requires either `conda run -n <env>` (needs `conda` on `$PATH`) or `source activate` (needs the shell hook). Inside Apptainer, `$CONDA_PREFIX` may be unset and `conda` may not be on `$PATH` at all — the container's env is whatever the `.sif` baked in. The doc never shows the implementation, uses the word "activate," and waves away the container transition in §7 as a "single-file change." The most likely production outcome is exit_code=127, which the badge layer will render silently as `verdict=fail`.

### 2. `ensure_assembly` has no lock: concurrent cards double-run SPAdes and corrupt the output directory
- Evidence: §3 Rule 2 ("First card that needs `<sample>.fasta` runs SPAdes"); `Project.ensure_assembly` docstring — no mention of locking; `posthoc/snp_analysis.py:147-157` shows the existing subprocess pattern with no guard
- Severity: blocker
- Falsification: Show `ensure_assembly` using `fcntl.flock` or equivalent exclusive lock on a sentinel file before spawning SPAdes.
- Detail: `ensure_assembly` is check-then-act: if no FASTA, run SPAdes. Two cards opening the same sample within 30 seconds — user A starts AMR, user B opens MLST — both see no `assembly/<sample>.fasta`, both spawn SPAdes into the same directory. SPAdes writes intermediate files during the run; the second invocation clobbers them mid-flight, producing either a corrupt FASTA or a SPAdes crash. The doc acknowledges shared writes to `assembly/` but specifies no mutual exclusion mechanism.

### 3. `samples.json` stale lockfile will permanently freeze a project with no recovery path
- Evidence: §3 describes "atomic tempfile+rename + per-project advisory lockfile"; `posthoc/snp_analysis.py:288` (`write_stats`) uses bare `path.write_text` with no locking — the existing primitive the design claims to generalize does not implement the pattern
- Severity: major
- Falsification: Show `record_finding` with stale-lock detection: a timeout plus pid-liveness check before breaking the lock.
- Detail: If a uvicorn worker is killed mid-write (OOD session timeout, OOM kill, `fuser -k` as described in CLAUDE.md §"Backend change"), the lockfile is never released. Every subsequent `record_finding` call blocks or errors indefinitely. The design does not specify lock format (PID-file? `fcntl`? `filelock`?), timeout, or stale-lock recovery. The actual existing code has no locking, making it likely the new code will inherit that gap.

### 4. Nine conda envs on `/home/vxk1/` is a quota landmine; partial installs are not cleaned up
- Evidence: §7 table (9 envs); AMRFinder env path is `~/miniforge3/envs/amrfinder/`; CLAUDE.md confirms all current envs under `/home/vxk1/miniforge3/`
- Severity: major
- Falsification: Show `quota -s vxk1` returns no limit enforced, and that env paths will be under `/srv/kapurlab/tools/` before any multi-user rollout.
- Detail: Nine bioconda envs average 3–6 GB each; CheckM's marker databases alone add ~2 GB. At the low end that is 27 GB under one user's home directory. If the institution enforces home quotas (standard on shared Linux), `mamba create` silently fails mid-install with "no space left on device," leaving a partially-constructed env. Conda does not auto-clean on failure; the next install attempt either fails on the existing prefix or produces a subtly broken env. No install script is specified that checks space or removes a partial env on failure.

### 5. `pip install -e` is an editable install that breaks under concurrent OOD sessions and has no upgrade story
- Evidence: §10 ("just `pip install -e ./kapurlab-pipelines`"); CLAUDE.md §"Development Workflow" — each new session starts a fresh uvicorn process importing from the same source tree
- Severity: major
- Falsification: Show the package is installed as a non-editable wheel into the conda env before any session starts, not as a live source-tree reference.
- Detail: Editable installs place a `.pth` file pointing at the live source tree. If `git pull` runs while two OOD sessions are active, session A (already-imported modules) runs the old code; session B (fresh uvicorn) imports the new code. Two simultaneous users call different versions of `record_finding` against the same `samples.json`. The design calls this "no versioning theatrics" — what it actually means is no version isolation across concurrent sessions.

### 6. OOD session timeout orphans long-running primitives, leaving corrupt assembly directories
- Evidence: CLAUDE.md §5 — Apptainer `--pid` namespace; processes inside are not visible to host `ps aux`; SPAdes routinely runs 2–4 hr on clinical isolates
- Severity: major
- Falsification: Show that wgs3's OOD wall-time limit is ≥8 hr, or that primitives are launched via a detached job (`nohup`, background task manager) that survives session death.
- Detail: When the OOD session times out, Apptainer sends SIGTERM then SIGKILL to the container process tree. SPAdes dies immediately with no cleanup hook. `assembly/<sample>/` exists but contains a partial FASTA. The next `ensure_assembly` call sees the directory, finds the partial file, passes it to AMRFinder, which either crashes or returns zero findings. The provenance `exit_code` sentinel is never written. `verify_provenance.py` will flag the audit failure — but no alert is triggered and no one reads that script in normal operation.

### 7. AMRFinder DB version is not pinned; monthly updates silently change gene calls
- Evidence: §5 (DB: `2026-03-24.1`); §6 provenance records `db_version` — but §7 says nothing about pinning or blocking `amrfinder --update`
- Severity: tradeoff
- Falsification: Show `amrfinder --update` is excluded from all wrapper code and install scripts, and the DB directory is write-protected after install.
- Detail: If anyone runs `amrfinder --update`, the DB silently changes. The `db_version` in provenance will differ from the smoke-test fixtures. After an update, `mecA1` identity thresholds or gene aliases may shift, causing the 8-sample regression matrix to fail — or worse, to pass because tests only check gene presence, not identity scores. The design mentions reproducibility but provides no mechanism to enforce it.

---

## Recommendations

- **Lock `ensure_assembly` and `record_finding` with exclusive `fcntl.flock` on per-sample sentinel files.** Write assembly output atomically (temp dir + `os.rename` on the final FASTA). Add a stale-lock timeout (30 min) that checks PID liveness before breaking the lock.
- **Replace `pip install -e` with a versioned wheel installed into the conda env on deploy.** The merge-to-main step builds a `.whl` and installs it; sessions pick up the new version on next launch, not mid-session.
- **Pin the AMRFinder DB by path hash in `config.json`** and make the wrapper refuse to run on a version mismatch. Never call `amrfinder --update` in any automated path.

---

## Where I'd be wrong

- If wgs3 is used by a single authenticated user at a time and OOD sessions are never concurrent, the race conditions in §§2–3 do not materialize.
- If the Apptainer `.sif` already bakes in all nine conda envs with `conda` on `$PATH`, the activation question in §1 resolves: `conda run -n <env>` works without a shell hook.
- If home-directory quotas are not enforced on wgs3 and envs are moved to `/srv/kapurlab/tools/`, attack vector 4 is moot.
