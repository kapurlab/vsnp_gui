# Multi-user / multi-app architecture

This is the agreed design for `kapurlab-wgs3` once it has more than one user and more than one pipeline app. It's the single source of truth — when you wonder "where should X live," check here before improvising.

Status: design agreed; rolling out via tickets T-19 → T-11 → T-12a → … (see `TICKETS.md`).

## Storage layout

Once T-19 lands:

```
/                                     OS (ext4, system NVMe, 932 GB)
/home/<user>/                         per-user, XFS w/ usrquota, fast NVMe (3.7 TB)
   ├── projects/                      private projects
   └── uploads/                       inbound staging (sequencer cron drops here)

/srv/kapurlab/                        shared lab data, XFS w/ prjquota, HDD (10.9 TB)
├── refs/                             read-mostly; admin-write only
│   ├── vsnp3/
│   │   ├── reference_options/        vsnp3 expects this layout (USDA convention)
│   │   │   ├── NC_045512_wuhan-hu-1/
│   │   │   ├── Mycobacterium_AF2122/
│   │   │   └── …
│   │   └── vcf_db_folders/           context-specific curated VCF sets
│   │       ├── mtbc_global_v3/
│   │       └── deer_sarscov2_2020/
│   ├── kraken/
│   │   ├── standard_2025_03/
│   │   └── viral_2025_03/
│   └── mhc/
│       ├── bola_current/             canonical alleles
│       ├── hla_current/
│       └── pending/                  approval queue (any member can write here)
│
├── projects/                         per-project, group-scoped (rwx via proj-<name>)
│   ├── btb_india/
│   │   ├── download/                 fastq (moved here from staging)
│   │   ├── step1/                    per-sample BAM/VCF (vsnp3 layout)
│   │   ├── step2/                    cluster outputs
│   │   └── audit/edits.jsonl         append-only VCF edit log (chattr +a)
│   └── …
│
├── tools/                            shared installed software
│   └── vsnp3/                        single canonical install (T-11 moves here)
│
└── audit/                            top-level append-only ledgers
    └── mhc-approvals.jsonl

/srv/kapurlab/backup/                 nightly mirror of /srv/kapurlab (HDD, 5.5 TB)
```

### Why this shape

- `/srv/kapurlab/` over `/shared/`: FHS reserves `/srv` for site-specific data. Namespaced by lab so multi-tenant is possible later.
- HDD for `/srv/kapurlab/`: vsnp3 is CPU-bound (BWA, RAxML); fastq read is sequential. HDDs handle that fine. Frees 3.7 TB NVMe for `/home/` where IOPS matters (Step 1 staging, IGV index seeks).
- 5.5 TB sdb dedicated to backup: rsync/snapshot mirror of the 10.9 TB primary. 1:2 retention is plenty for canonical projects + refs. Off-site backup is a separate concern (project T-?? for later).
- Backups never touch `/`. The OS is reproducible from documentation; the lab's data isn't.

## Linux groups

| Group | Purpose | Members (initial) |
|---|---|---|
| `kapurlab-members` | every lab user; r-x on `/srv/kapurlab/refs/`, ability to launch OOD apps | Vivek, Tod, Ro, Lingling, Dev, Dee |
| `kapurlab-admins` | rwx on `refs/`, approve MHC pending → current, manage projects | Vivek, Tod, Ro |
| `proj-<name>` | rwx on `/srv/kapurlab/projects/<name>/`; scoped per project | varies |

Setgid (`2770`) on each `proj-<name>` directory ensures files created mid-pipeline (BAMs, VCFs) inherit the group automatically — no chmod afterthought.

Per-user primary group stays the user's own (Linux convention); group rights ride on supplementary groups.

A small admin script (`/usr/local/sbin/kapurlab-setup-project.sh <project> <user>...`) lands in T-12a as the canonical way to spin up new projects.

### Initial user → group map

| User | members | admins | project groups |
|---|---|---|---|
| Vivek | ✓ | ✓ | all `proj-*` |
| Tod | ✓ | ✓ | all `proj-*` |
| Ro | ✓ | ✓ | all `proj-*` |
| Lingling | ✓ | — | `proj-mhc-*` |
| Dev | ✓ | — | `proj-mtbc-*` |
| Dee | ✓ | — | (TBD by Vivek) |

## Quotas

XFS project quotas at mount time (T-19), populated and enforced in T-12a.

- `/home/<user>/`: usrquota, soft 1 TB / hard 1.5 TB, 7-day grace.
- `/srv/kapurlab/projects/<name>/`: prjquota, soft 5 TB / hard 7 TB, 7-day grace.
- `/srv/kapurlab/refs/` and `/srv/kapurlab/tools/`: unquotaed (admin-managed, no growth pressure).

Weekly cron emails admins a `xfs_quota -x -c "report -h"` summary. Bumping a project's quota is a one-line command; we don't need to be conservative.

## fastq ingestion

```
production sequencer
   ↓ cron rsync
/home/<user>/uploads/<run-id>/        per-user staging, on usrquota
   ↓ user runs QC, decides what to keep
GUI "Add to project" button
   ↓ rename(2) — same FS, instant
/srv/kapurlab/projects/<name>/download/<run-id>/
```

The GUI move is `rename(2)` when source and destination are on the same filesystem. Both are on `/`-XFS-tier hardware (different mounts, but same physical box) — `rename(2)` won't work cross-mount, so the move falls back to copy + verify + unlink. Acceptable; sequencer fastq is moderate size.

The "user QC" gate prevents half-uploaded or ambiguous-quality data from hitting the project group's view, and quota accounting cleanly switches owners when the file moves.

## VCF edit flow

Already implemented in vsnp_gui; carries forward unchanged for multi-user except for stricter audit:

- Originals (`<sample>/alignment_<ref>/<sample>_zc.vcf`) are immutable.
- Edits go to `<sample>/alignment_<ref>/vcf_edits/`, with each patched VCF named `<sample>_v<n>.vcf` and a JSONL patchlog (locus, original ALT, new ALT, reason, user, timestamp).
- Step 2 picks the latest patched VCF if present; original otherwise.

Multi-user additions:

- A user can edit ⇔ they're in `proj-<name>` for that project. Filesystem permissions enforce; no GUI-side check needed.
- A project-level `audit/edits.jsonl` is `chattr +a` (append-only). Every patchlog line is mirrored there in real time. Even a project member can't rewrite history.

## vsnp3 installation

One canonical install at `/srv/kapurlab/tools/vsnp3/` (T-11). All users' PATH points there.

- The `reference_options_paths.txt` file is at `<install>/dependencies/reference_options_paths.txt`. Single shared file means one source of truth for which references vsnp3 sees.
- Adding a reference is `vsnp3_path_adder.py -d /srv/kapurlab/refs/vsnp3/reference_options` once at install time, plus `vsnp3_path_adder.py -d /srv/kapurlab/refs/vsnp3/vcf_db_folders` (parent of curated VCF sets).
- `vsnp3_path_adder.py` writes to `dependencies/reference_options_paths.txt` — that file lives inside the shared install, so only `kapurlab-admins` can run the adder. Acceptable: adding a new reference type is an admin operation.
- `conda update vsnp3` clobbers our local patches (column[0] / VSNP3_BOOTSTRAP). Lock the env or pin the version until upstream fixes land — see USDA-VS/vSNP3#22, #23.

## MHC approval chain

When the MHC app arrives:

```
member submits a putative allele
   ↓
/srv/kapurlab/refs/mhc/pending/<panel>/<submission-id>/
    fasta, metadata.json (submitter, sample of origin, evidence)
   ↓
admin runs `kapurlab-mhc review <submission-id>`
   diff vs current panel, sanity-check evidence
   ↓
   approve → moves into refs/mhc/<panel>_current/
             bumps panel version
             writes line to /srv/kapurlab/audit/mhc-approvals.jsonl
   reject  → moves into refs/mhc/rejected/<id>/ with notes
             also logged
```

Permissions:

- `pending/` is `2775` (any member can submit; nobody can rewrite an existing submission).
- `<panel>_current/` is `0750` group `kapurlab-admins` (members read; only admins write).
- `audit/mhc-approvals.jsonl` is `chattr +a`.

## Kraken

Simpler than vSNP. Only the database is shared; per-run data is per-user/per-project.

- `/srv/kapurlab/refs/kraken/<db>/` — the k2d files. Read-only via `kapurlab-members`.
- Multiple users running Kraken2 against the same DB simultaneously is fine — kraken2 mmaps the DB.
- Project space (reads + classification reports) follows the same pattern as vSNP projects.

## Cross-project sample sharing

Two phases:

**T-12a (initial)**: per-project trees with cross-project **symlinks**. A sample lives in its "owning" project (whichever first ingested it). Other projects that reference the same sample symlink `step1/<sample>` to the owning project. Manifest line records the link. Zero changes to vsnp3 internals.

**T-12b (deferred — when symlinks bite)**: flat sample-store at `/srv/kapurlab/samples/<sample-id>/`. Projects become views (a list of sample IDs + project-level config). Cleaner deduplication and provenance, but requires backend refactor of `_resolve_sample_dir`, the import flow, and a migration script.

## Backup strategy

Cron-driven nightly:

- `rsync -aAX --delete /srv/kapurlab/{refs,projects,tools,audit}/ /srv/kapurlab/backup/$(date +%Y%m%d)/`
- Retention: 7 daily, 4 weekly, 12 monthly. ~6 months of recoverable history.
- Off-site backup of the most critical pieces (`refs/`, `audit/`) is a separate concern; option is rsync to a Penn State storage allocation if available.

`/home/<user>/` is **not** backed up at the lab level. Users responsible for their own home backups; if they want lab-level retention for project work, it lives in `/srv/kapurlab/projects/<name>/`.

## Migration order (recap)

1. T-19 — storage layout (downtime window).
2. T-04 — remove `/home/vxk1/` literals from backend.
3. T-11 — move references; install shared vsnp3.
4. T-12a — groups + projects + setup script.
5. T-21 — migrate Vivek's Mac Electron projects.
6. T-07 — run provenance (must land before any "real" run).
7. T-16 — KapurLab landing page (in three phases).
8. T-15+T-08 — multi-app deploy template.
9. T-18 — Kraken DB layout.
10. T-17 — MHC approval chain.
11. Then polish: T-09, T-05, T-13, T-14, T-06.
