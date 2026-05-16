# Session handover — May 16 2026 (afternoon)

Continuation notes for the next Claude session. Read this first, then
[`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md) and
[`TICKETS.md`](TICKETS.md).

The morning-of-May-16 handoff (Shivasharanappa panel + AMR fixture +
PIPELINES_PACKAGE design) is preserved at commit `3abbb1a` in git history.
Open in browser via GitHub if you need the kraken/AMR run details.

## TL;DR — what landed today (afternoon)

1. **Merged `codex/snp-analysis` into `main`** (commit `56a8e06`). The
   posthoc SNP analysis subsystem is now live in production. Resolved 11
   conflict regions across `main.py` (8), `App.jsx` (5), `requirements.txt`,
   `styles.css`. Detailed resolution log lives in the merge commit message.

2. **Smoke-tested every merge region live on the OOD GUI.** All green —
   step2 dispatch (T-07 + shlex.quote), preview-xlsx, download-file with
   `download_name`, labeled-tre suppression, AND the brand-new
   `/posthoc/run` endpoint producing snp_matrix.csv + KDP + closest-neighbor
   plots on a 6-sample deer SARS-CoV-2 group.

3. **Filed T-36/37/38** for post-hoc resolver UX gaps surfaced during
   smoke testing (none are merge regressions — pre-existing on main).

## Current state

**Branches**: `main` is the canonical branch at `06c35ef` (= merge +
tickets). `merge/snp-analysis-into-main` and `codex/snp-analysis` are
deleted everywhere (local, remote, wgs3). `main-electron-archive` and
the kept feature/spike branches untouched.

**wgs3** (`/srv/kapurlab/tools/vsnp_gui`): on `main` @ `06c35ef`. Running
uvicorn (port 26911) was spawned at 09:18 on the merge content — content
hasn't changed since (fast-forward), no restart needed unless new code
lands. The deployed bundle hashes match what was built locally:
`index-dc8fef81.js`, `TreeStandalone-d281d2b6.js`.

**New deps installed on wgs3** (into `/srv/kapurlab/tools/vsnp3` conda env):
- `aiofiles==24.1.0` (via mamba conda-forge)
- `snp-dists==1.2.0` (via mamba bioconda)

Both verified via `shutil.which` from the running uvicorn — no restart
needed (registry checks per-request).

**Test fixture for `/posthoc/run`**: project `Retest` on wgs3 (deer
SARS-CoV-2 samples) has a complete posthoc run at
`/home/vxk1/projects/Retest/step2/name-All/posthoc/` — snp_matrix.csv,
kdp.{pdf,png}, closest_neighbor.{pdf,png}, stats.json. Useful regression
artifact when iterating on the SNP analysis primitive.

## Open items for the next session

### Priority 1 — red-team `PIPELINES_PACKAGE.md`

This is the deferred Priority 4 from the morning handoff. Spawn parallel
sub-agents to review the design doc from four angles before implementing
Phase 1 (`pipelines/amrfinder.py`):

- software architecture (interface contracts, dependency injection,
  testability)
- ops/deployment (multi-app conda envs, version pinning, secrets)
- bioinformatics workflow (does the contract fit real tools' I/O shapes?)
- code-review hygiene (naming, error handling, observability)

Today's working `posthoc/snp_analysis.py` is a *de facto* prototype of
what a primitive looks like, but it predates the PIPELINES_PACKAGE design.
Worth comparing: does posthoc fit the contract? If not, why?

### Priority 2 — file T-27 through T-35

The PIPELINES_PACKAGE design implies 9 new tickets. The morning handoff
sketched them; they were never added to `TICKETS.md` because the
afternoon went to the merge instead. Block of tickets:

- T-27 implement `pipelines/common/` (`AnalysisPrimitive` + `Project`
  workspace + provenance/badge/runner shims)
- T-28 implement `pipelines/amrfinder.py` against the contract; regression
  against today's 8 NivediXXX `amr_matrix.csv`
- T-29 wire AMR into vsnp_gui (post-step1 hook, badge, "Run AMR" button)
- T-30 re-deploy kraken_gui as OOD batch_connect; import `pipelines/kraken.py`
- T-31 standalone AMR OOD card
- T-32 sourmash card + `pipelines/sourmash.py`
- T-33 port NAHLN_AMR's MLST / Abricate / SeqSero2 wrappers
- T-34 cross-card navigation protocol (`?project=X&sample=Y`)
- T-35 `samples.json` schema + concurrent-write strategy (atomic
  tempfile+rename + per-project lockfile)

Filing them is a 15-min markdown task. Either before or after Priority 1
red-team is fine — having them filed makes the red-team scoping easier.

### Priority 3 — start T-29 (AMR wiring) once design is settled

T-29 is the first **end-to-end** consumer of the pipelines package. It
proves the contract works by being a real button users press. The
NivediXXX AMR fixture from the morning is the regression seed.

### Priority 4 (small) — T-36/37/38

Post-hoc sample resolver UX gaps. All three are scoped well in
`TICKETS.md:232-234`. Total work ~1-2 hours. Good warm-up if Priority 1
needs incubation time.

## Other observations

- **`docs/dev/TICKETS.md` "Branching" section is stale.** Line 237 still
  says "`web` is the OOD/FastAPI rewrite branch off `main`". That branch
  no longer exists — `web` → `main` rename happened earlier. Trivial
  cleanup: rewrite that paragraph to reflect current branching reality.

- **OOD batch_connect deployment model worth documenting.** The deploy
  is *literally whatever's checked out* in `/srv/kapurlab/tools/vsnp_gui`
  on wgs3 (or `/home/${USER}/vsnp_gui` fallback). No CI, no per-session
  clone — `git checkout X` on wgs3 = deploy. Worth adding to README or
  runbook so the next person doesn't have to discover it by reading
  `deploy/ood/template/script.sh.erb`.

- **GitHub branch protection still unset.** Got 403 "Upgrade to Pro or
  make public" earlier even though the repo is supposedly on a paid
  plan. Worth a 1-min check at github.com/settings/billing — might be
  on a different account/org.

- **`charming-sanderson-f6840e` worktree** was removed earlier today
  (was orphan on `main`). If you see references to it in old notes,
  ignore — gone.

## Verify when picking up

1. `git log --oneline -5` on `main` should show this handoff commit
   on top, then `06c35ef` (T-36/37/38), then `56a8e06` (the merge),
   then `3abbb1a` (the morning handoff).

2. `git status` — clean (no uncommitted; `.claude/` untracked is fine).

3. `ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui && git log --oneline -2'`
   should match origin/main.

4. `ssh wgs3 'curl -s http://localhost:26911/api/posthoc/tools | python3 -c "import sys,json; print(json.load(sys.stdin)[0][\"available\"])"'`
   should print `True`. (Port may differ if a new OOD session was
   started — read `connection.yml` in the most recent
   `~/ondemand/data/sys/dashboard/batch_connect/sys/vsnp_gui/output/<UUID>/`
   for the current port.)
