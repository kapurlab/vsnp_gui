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

- **`/srv/kapurlab/tools/vsnp_gui` is on `tstuber_2026-05-20` (Tod's branch)**,
  NOT on `main`. Currently 11 commits ahead of `origin/main`. See
  "Tod's branch" section below for the review queue.
- `origin/main` is at `eb36c18` (May 17–18 handoff); local Mac matches.
- The Tod-branch dist served by wgs3's Apache is freshly built.
- **uvicorn restart needed for any backend change to be active** — fresh
  OOD session picks up the current Tod-branch state including timestamped
  step2 dirs, project-level reference, VCF browser, and metadata editor.
- Active projects on wgs3:
  - `/home/vxk1/projects/LSDV_India/` — 73 LSDV samples, Step 1 done
    against `LSDV_Neethling_2490`; some Step 2 work in flight.
  - `/srv/kapurlab/projects/demo_sars_cov_2/` — Tod's pre-populated demo
    project (copy of `Retest`, 6 deer SARS-CoV-2, full step1+step2
    finished).
- Reference: `LSDV_Neethling_2490` at
  `/srv/kapurlab/refs/vsnp3/reference_options/LSDV_Neethling_2490/`
  (AF325528.1 fasta + gbk + gff + templates + define_filter +
  remove_from_analysis).

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

## Tod onboarding — done

Resolved inline at the end of the session:

- **`tks5563` already had a Linux account** (UID 1000, sudo group). Just
  needed kapurlab-{members,admins} group membership + SSH self-loopback
  bootstrap. Done via `kapurlab-add-user.sh tks5563 --admin`.
- **Demo project staged**: `/srv/kapurlab/projects/demo_sars_cov_2/` —
  copy of `Retest` (6 deer SARS-CoV-2 samples, step1+step2 complete),
  group-owned `proj-demo_sars_cov_2` (tks5563 + vxk1 members),
  project.json corrected (`name: demo_sars_cov_2`), 12 intra-project
  absolute symlinks rewritten to the new path.
- **PSU VPN access enabled**: OOD `ood_portal.yml` now has
  `server_aliases: [172.29.62.6, a8-an-vxk1-u5]` so Apache stops
  301-redirecting non-Tailscale traffic to the Tailscale IP. Apache
  vhost regenerated and reloaded. PSU campus / PSU VPN + Tailscale all
  work as access paths. Backup at
  `/etc/ood/config/ood_portal.yml.pre-server-aliases-bak` for rollback.
- Welcome note at [`TOD_WELCOME.md`](TOD_WELCOME.md), polished
  email-ready version delivered inline at session end for Vivek to send.

## Tod's branch: `tstuber_2026-05-20` — review needed

Tod has been working in parallel on his own feature branch and that work
is now on origin (pushed at session end). **It is not yet merged into
main**, but wgs3 is currently running it (the deployed checkout is
`tstuber_2026-05-20`, not main). The next session should review and
either merge or sequence the integration.

**State**:
- Branch is 11 commits ahead of `main`, based on `eb36c18` (the May 17-18
  handoff commit — the last thing we shipped before Tod started)
- Zero merge conflicts (main hasn't advanced since Tod branched)
- Touches 4 files: `backend/app/main.py` (+476 lines),
  `frontend/src/App.jsx` (+748), `backend/app/projects.py` (+17),
  `backend/app/provenance_writer.py` (+19). Net +1162 / -98 lines.
- Available on GitHub at
  https://github.com/vkapur/vsnp_gui/tree/tstuber_2026-05-20

**11 commits (oldest → newest)**:

```
11e18c9 provenance: fix git safe.directory for OOD cross-user deployment
29f913a Commit A: project-level reference
89cad60 Commit B: timestamped step2 runs + concurrency guard
ee94915 Auto-refresh after step1/step2 complete; fix VCF DB reference display
a03507f Fix import count labels; polling-based auto-refresh for step1/step2
24293c7 Separate dedup vs ref-mismatch exclusion counts in import result
79bfe23 Add searchable VCF sample browser to VCF Databases panel
93d5cb4 Fix VCF set count to show actual files in set, not total source VCFs
6539e5e Make VCF set count authoritative from sample manifest
a430228 Add sample metadata editor to Reference Editor panel
a815b4b Add <project>_VCFs accumulation folder wired through Step 1, Step 2, and metadata
```

**Tod's own summary of what's in there** (verbatim from the user's message):

- **Bug fix**: Step 1 provenance dispatch failure caused by missing git
  state in `/srv/kapurlab/tools/vsnp_gui`. New branch was created and
  fix applied there. (Likely the `11e18c9` provenance/safe.directory
  commit — relates to the same provenance dispatch class as our T-46
  Phase 1 work.)
- **Step 2 timestamped subdirs**: `step2/2026-05-20_08-52-00/` so
  multiple comparisons coexist; UI option to switch between past runs.
- **Reference selection moved to project level**: reference type is now
  picked at the project level (not repeated per-step). Reference
  Selection pane relocated alongside Projects pane. (**Overlaps with
  our `c2eccc1` auto-fill output dir + `eb631e3` display name work** —
  worth checking the integration is coherent.)
- **Auto-refresh after step completion** for Steps 1 and 2. Tod flagged
  "for some reason this still doesn't seem to be working as it should"
  — known incomplete.
- **VCF file browser**: searchable browser for VCF sample names in a
  build, scaled for large sample sets.
- **Metadata editor (Reference Editor)**: vSNP3 two-column Excel
  metadata format. Bulk paste of tab-delimited text or single entries.
  Persists to the reference xlsx file. (**Overlaps with our `b7554fd`
  T-39 reference-file re-upload + `3df0383` T-17a approval-chain
  design** — Tod's edits write directly to the reference xlsx, our
  T-17a design routes through a propose+approve queue. Need to
  reconcile whether this is the "direct edit for admins, queue for
  others" pattern from T-17a or a separate primitive.)

**What the next session should do**:

1. **Read Tod's diff** —
   `git diff main..origin/tstuber_2026-05-20 -- backend/app/main.py frontend/src/App.jsx`
   — look for code-quality issues, style alignment with the rest of
   main.py / App.jsx, anything that conflicts with our T-46 dispatch
   plan or the T-17a approval-chain design.
2. **Verify the auto-refresh issue Tod flagged** by exercising step1 +
   step2 in the GUI and watching whether the status panel updates
   without manual refresh. May just need a poll-interval tweak.
3. **Decide on merge strategy**: (a) fast-forward merge if review is
   clean, (b) squash-merge if you want a tidier main history, or
   (c) cherry-pick selected commits and leave others for follow-up.
4. **Reconcile metadata editor with T-17a**: Tod's direct-edit-to-xlsx
   for metadata is different from the proposal-queue flow T-17a
   specifies for `*_define_filter.xlsx` / `*_remove_from_analysis.xlsx`.
   The metadata sheet is a *separate* xlsx in the reference dir, so it
   may legitimately live outside T-17a's scope — but document the
   distinction before either ships permanently.

## Open follow-ups for next session

In rough priority order:

1. **Review Tod's `tstuber_2026-05-20` branch** (see section above) —
   blocks rolling main forward.
2. **Adjudicate UNRESOLVED-1 and UNRESOLVED-2** from the red-team. Until
   these are decided, T-27 is blocked.
3. **Sequence T-27 → T-29** once UNRESOLVEDs settle. The scope-freeze
   punch list is at the bottom of [`FINDINGS.md`](redteam/FINDINGS.md);
   work it top-to-bottom.
4. **Pick up T-42 / T-43 / T-46 Phase 2** opportunistically — each is
   independently scoped and could ride along with whatever the user is
   doing.
5. **Document the "discovery vs dispatch" invariant** in
   `PIPELINES_PACKAGE.md` §4 before T-27 implementation starts (see
   "Pattern worth documenting" above).
6. **PSU networking** — Scott (PSU IT) confirmed inter-VLAN routing
   between Wartik (172.17.243.0/24) and Ag IoT (172.29.62.0/23) works
   both directions; sustained throughput ~65–70 MB/s on 1 Gbps links
   (only ~6% slower over Tailscale, so the perf gap is modest). The
   GlobalProtect → HTTP/HTTPS path to wgs3 is still pending the network
   lead's review (SSH works via GP; HTTP doesn't yet). Once HTTP via GP
   is open, the OOD server_aliases we added (`172.29.62.6` +
   `a8-an-vxk1-u5`) make PSU VPN access work without further config.
7. **Real-rsync throughput test** on sequencer → wgs3 once there's a
   representative fastq batch to move. nc-based test showed 65–70 MB/s
   lower bound on a 1 Gbps link; rsync with parallelism should do
   better in practice.

## Verify when picking up

```bash
# Local main HEAD (origin)
git log --oneline -3 main
# eb36c18 docs: session handoff (May 17-18) + Tod welcome note
# 3a5a818 step2_setup: honor remove_from_analysis.xlsx ...
# 4212688 T-46 Phase 1: auto-skip single-end + junk fastqs ...

# Tod's branch on origin (review-and-merge queue)
git fetch origin && git log --oneline main..origin/tstuber_2026-05-20 | wc -l
# 11

# wgs3 is currently on Tod's branch (deployed state):
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui && git branch --show-current && git log --oneline -1'
# tstuber_2026-05-20
# a815b4b Add <project>_VCFs accumulation folder ...

# Red-team archive should be intact
ls docs/dev/redteam/  # FINDINGS.md, DECISIONS.md, R1-*.md, R2-*.md, sources/, etc.

# Confirm LSDV India Step 1 finished cleanly
ssh wgs3 'ls /home/vxk1/projects/LSDV_India/step1/*/alignment_*/*_filtered_hapall_annotated.vcf 2>/dev/null | wc -l'
# 73

# Confirm Tod's demo project is in place
ssh wgs3 'ls /srv/kapurlab/projects/demo_sars_cov_2/step1/ | head -5'
```
