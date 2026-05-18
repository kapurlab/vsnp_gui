# Session handover — May 17–18 2026

Continuation notes for the next Claude session. Read this first, then
[`docs/dev/redteam/FINDINGS.md`](redteam/FINDINGS.md),
[`docs/dev/redteam/DECISIONS.md`](redteam/DECISIONS.md), and
[`docs/dev/TICKETS.md`](TICKETS.md).

Prior handoff (afternoon of May 16 — merge + smoke green) is preserved at
commit `81944c7` in git history. The session that just ended started with
the red-team review setup from that handoff and pivoted hard into a long
stretch of bug fixes triggered by Vivek using the GUI on a real 41-sample
LSDV India dataset.

## TL;DR — what landed (21 commits since `81944c7`)

**The big one:** [`redteam/`](redteam/) — a self-contained archive of a
3-round adversarial design review of [`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md)
before T-27 (`pipelines/common/`) implementation begins. 8 R1 attack
angles + 6 R2 cross-examinations + 1 R3 synthesis = 54 attack vectors
binned. Bottom line: **12 confirmed blockers, 9 majors, 5 narrowed,
10 refuted, 3 tradeoffs, 2 UNRESOLVED.** The two unresolved items are
called out explicitly below — they need your decision before T-27 starts.

**The unglamorous pile:** 18 bug-fix / UX-improvement commits triggered by
the LSDV India end-to-end run. Class signature: every one of them was an
"obvious in retrospect" mismatch between what the discovery layer accepts
and what the dispatch layer can actually process. See "Pattern worth
documenting" at the bottom.

## Two UNRESOLVED red-team decisions you need to make

Both have all sides preserved verbatim in
[`docs/dev/redteam/DECISIONS.md`](redteam/DECISIONS.md). Skim that first.

1. **`ensure_assembly()` locking posture** — pre-T-27 blocker, or P2
   annotation? Real 5-way R2 split (REBUT / UNRESOLVED / ESCALATE /
   ESCALATE-blocker / PARTIAL-REBUT). The question is whether to fix
   the race now (`fcntl.flock` in the sketch) or annotate-and-defer
   until concurrent OOD cards actually materialize at Step 6.

2. **§6 provenance schema vs T-07** — port `capture_env_snapshot()` into
   §6 now, or accept the reduction? R2-ops empirically confirmed the
   regression with file:line evidence (`provenance_writer.py:476-487`
   captures 6+ env fields where §6 captures one); R2-ux rebutted as
   "not a UX concern, defer." Coherent positions on both sides.

Once these are decided, the T-27–T-35 ticket block (filed earlier today
in `TICKETS.md`) can have its scope frozen and implementation can start.

## What shipped (commit graph since `81944c7`)

```
3a5a818 step2_setup: honor remove_from_analysis.xlsx when building VCF source
4212688 T-46 Phase 1: auto-skip single-end + junk fastqs at Step 1 dispatch
9417773 SRA crosswalk: surface in GUI (Inputs panel + SRA download section)
0e86b9a xlsx preview: fix "Download xlsx" link to preserve existing query params
b5edfa9 SRA download: persist input→runs crosswalk to download dir
a62f34f xlsx preview: round General-format floats to whole numbers
ea1a9a5 xlsx preview: round non-integer floats to 2 decimals (superseded)
9e1d7ea kapurlab-rename-project: rewrite intra-project absolute symlinks
2f6c0d5 kapurlab-rename-project: update project.json to match new dir name
4edbc9e Project create: auto-normalize names (spaces → underscores) + T-43
eb631e3 Reference download: optional display name for dropdown clarity
c2eccc1 Reference download: auto-fill output dir + load refPaths on mount
14acf47 docs: T-42 — SRA download progress UI + status-writer bug fix
d237abd Stats button: open xlsx preview in tab instead of downloading
ac86e15 docs: T-41 — record T-39/T-40 done, broaden T-01 scope note
b7554fd T-39: reference-file re-upload route (close offline-edit loop)
50210a7 Edit Log: render JSONL inline; remove dead xdg-open endpoints (T-40)
7c7351d Reference Editor sweep follow-up: replace xdg-open helpers w/ in-browser viewers
3df0383 docs: T-17a — reference-file edit + approval chain
21a604a docs: red-team adversarial review of PIPELINES_PACKAGE.md
8d74fe1 Reference Editor: fix broken 'Edit in Spreadsheet App' in OOD context
```

## Tickets filed / resolved this session

| Ticket | Status | What |
|---|---|---|
| **T-17a** | filed (deferred pending UNRESOLVEDs) | Reference-file edit + approval chain — generalizes T-17 MHC approval pattern to reference xlsx files. Phase 1: upload + rationale + admin queue. Phase 2: schema-aware in-browser editor. Permission model: kapurlab-admins; self-approve allowed but logged. |
| **T-27–T-35** | filed (blocked on UNRESOLVEDs) | Pipelines-package architectural backbone — filed earlier today in TICKETS.md before the red-team. Currently the load-bearing forward work; should not start until UNRESOLVED-1/2 are decided. |
| **T-39** | ✅ done (commit `b7554fd`) | Reference-file re-upload route. POST /api/references/{ref}/upload-file with whitelist + atomic write + audit log to `/srv/kapurlab/audit/reference-changes.jsonl`. Forward-compatible with T-17a's schema. |
| **T-40** | ✅ done (commit `50210a7`) | Retired dead xdg-open endpoints. Three `/open` endpoints + `_open_path()` helper deleted. Frontend already on in-browser viewers across two prior sweeps. |
| **T-41** | ✅ done (commit `ac86e15`) | Docs cleanup: TICKETS.md T-01 scope note + T-39/T-40 done entries + stale "Branching" prose. |
| **T-42** | filed | SRA download progress UI (Phase 1: counts + bar, Phase 2: per-accession chips) + prerequisite status-writer bug fix (3 successful runs without `.status_*` sentinel; 5 successful runs falsely reported as `[FAILED]`). |
| **T-43** | filed | vsnp3 upstream patch for unquoted seqkit subprocess invocation (the bug that broke Step 1 on the `LSDV India` project before the rename). Also audit bwa/samtools/bcftools for the same quoting weakness. |
| **T-46** | Phase 1 ✅ done (commit `4212688`); Phase 2 filed | Single-end + junk-fastq handling. Phase 1: `_step1_dispatch_plan()` auto-filters single-end and <1 MB fastqs at dispatch time, surfaces skipped list via the response so the GUI alerts the user. Phase 2: real single-end Illumina support (vsnp3 patch). |

**Not filed but discussed:**
- T-44 / T-45 — depth-cap on mapping (post-mapping cap at 300× via vsnp3 `--max-mapped-depth` patch). Vivek deferred ("still not sure"). The use case is real for over-deep samples on small viral genomes (LSDV India had a few 1000×+ samples) but problematic for low-yield mapping (LSDV samples are 2% viral due to host contamination, so pre-mapping caps would discard real viral reads). Right semantic is cap-mapped-not-raw; revisit when there's real per-sample mapping-rate data to decide on.

## Production state on wgs3 right now

- `main` @ `3a5a818` on both `/srv/kapurlab/tools/vsnp_gui` (deployed) and origin/main
- Frontend dist freshly rebuilt; new hashes are live
- **uvicorn restart needed for the latest backend changes to be active** —
  start a fresh OOD session to pick up step2_setup exclusion filtering,
  T-46 dispatch plan, and SRA crosswalk endpoint
- Active project: `/home/vxk1/projects/LSDV_India/` — 73 LSDV samples
  ran Step 1 successfully against `LSDV_Neethling_2490` reference;
  Step 2 in flight with 3 samples excluded per QC
- Reference: `LSDV_Neethling_2490` lives at
  `/srv/kapurlab/refs/vsnp3/reference_options/LSDV_Neethling_2490/`
  (AF325528.1 fasta + gbk + gff, plus templates copied from vsnp3
  dependencies, plus a `_define_filter.xlsx` and `_remove_from_analysis.xlsx`)

## Pattern worth documenting (and using to design T-27)

Three bugs landed in this session share an underlying shape:

1. **Step 1 setup created sample dirs for single-end fastqs** but T-07
   provenance dispatch couldn't process them → batch aborts (fixed in
   T-46 Phase 1).
2. **Project rename moved the dir** but T-07 provenance dispatch followed
   absolute symlinks to the old space-path → batch aborts (fixed in
   `9e1d7ea`).
3. **Step 2 setup linked every step1 VCF into vcf_source/** but vsnp3
   honored the exclusion list at run time → file count lied, user
   confusion (fixed in `3a5a818`).

Common shape: **discovery accepts more samples than dispatch can honor.**

This is exactly what the red-team's BLOCKER-2 (`applicable()` TOCTOU +
undefined `PrimitiveError`) and NARROWED-2 (`applicable()` expressiveness
needs `pre_run_check()` for environment validation) are pointing at for
T-27. The shared design principle to encode in the `AnalysisPrimitive`
contract: **if discovery says "this is a valid sample," dispatch must
either run it or surface a non-fatal skip with a clear reason — never
abort the whole batch.** Worth documenting in `PIPELINES_PACKAGE.md`
§4 as a design invariant before T-27 starts.

## Tod onboarding — open

The user asked me to confirm Tod can access the pipeline + populate a
few trial projects. State on wgs3 right now:

- **Tod has no Linux account.** Not in `kapurlab-members` (which has
  only `vxk1` and `ro_test`).
- **No shared trial projects** yet — only `sanity_test` from the T-12a
  rollout exists under `/srv/kapurlab/projects/`.
- **Onboarding tooling exists**:
  `/srv/kapurlab/tools/vsnp_gui/deploy/admin/kapurlab-add-user.sh`
  handles the user + group + SSH-self-loopback bootstrap idempotently.

A welcome note for Tod is staged at [`docs/dev/TOD_WELCOME.md`](TOD_WELCOME.md).
The next session can either pick this up or it gets handled inline.
Decision points: admin vs member, password vs SSH key, which trial
projects to seed.

## Open follow-ups for next session

In rough priority order:

1. **Adjudicate UNRESOLVED-1 and UNRESOLVED-2** from the red-team. Until
   these are decided, T-27 is blocked.
2. **Sequence T-27 → T-29** once UNRESOLVEDs settle. The scope-freeze
   punch list is at the bottom of [`FINDINGS.md`](redteam/FINDINGS.md);
   work it top-to-bottom.
3. **Onboard Tod** (or kick it back to user) — admin tooling is ready,
   need user decision on credentials/role.
4. **Pick up T-42 / T-43 / T-46 Phase 2** opportunistically — each is
   independently scoped and could ride along with whatever the user is
   doing.
5. **Document the "discovery vs dispatch" invariant** in
   `PIPELINES_PACKAGE.md` §4 before T-27 implementation starts (see
   "Pattern worth documenting" above).

## Verify when picking up

```bash
git log --oneline -3
# 3a5a818 step2_setup: honor remove_from_analysis.xlsx ...
# 4212688 T-46 Phase 1: auto-skip single-end + junk fastqs ...
# 9417773 SRA crosswalk: surface in GUI ...

ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui && git log --oneline -1'
# Should match the local HEAD.

ls docs/dev/redteam/  # should contain FINDINGS.md, DECISIONS.md, R1-*.md, R2-*.md, sources/, etc.

# Confirm the LSDV India project finished step1 cleanly:
ssh wgs3 'ls /home/vxk1/projects/LSDV_India/step1/*/alignment_*/*_filtered_hapall_annotated.vcf 2>/dev/null | wc -l'
# Should be 73 (the non-skipped paired samples)
```
