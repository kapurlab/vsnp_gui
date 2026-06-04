# Session handover — June 4 2026

Continuation notes for the next Claude session. Read this first, then
[`docs/dev/TICKETS.md`](TICKETS.md), [`docs/dev/TOD_QUEUE.md`](TOD_QUEUE.md),
and [`docs/dev/redteam/DECISIONS.md`](redteam/DECISIONS.md).

Prior handoff (May 17–18) is preserved at commit `8985763` in git
history. That session ended with Tod's `tstuber_2026-05-20` branch
pending merge and two UNRESOLVED red-team decisions blocking T-27. This
session covered ~3 weeks and pivoted hard into a cascade-table → IGV
feature that grew into a full T-02 phase 0 land, plus operational
infra (dev OOD app with branch picker, repo transfer to kapurlab,
CodeRabbit setup, cross-user git fixes).

## TL;DR — what landed since `8985763`

**Cascade-table → IGV click** (T-02 phase 0, merged 2026-05-28 via
`e5c2745`, refined through `f57008d` 2026-05-29). Hover any colored
variant cell in a `name-All_cascade*` or `name-All_sorted_*` xlsx
preview → small dark `↗ this  ↗ all` pill in the corner → click opens
IgvStandalone in a named tab with that sample's BAM (reads), the
sample's `_filtered_hapall_annotated.vcf` (calls, with rich
gene/product/AA-change annotation in the ID column), and the
reference GFF (gene-structure annotation). Subsequent clicks add
samples additively to the *same* IGV tab via postMessage rather than
opening N new tabs. Same-window navigation worked first try in Chrome,
took a separate Safari-specific fix (`window.open` via onclick because
Safari ignores HTML `target="<name>"` for tracking-prevention reasons).
Single-position loci on large genomes (MTBC0 4.4 Mb) needed an
explicit `browser.search()` call after `createBrowser` to actually
zoom in — `config.locus` was silently dropped on big contigs.

**Tod's `tstuber_2026-05-20` branch merged to main** (2026-05-23, commit
`58c3a28`). Project-level reference, timestamped step2 runs, VCF
sample browser, sample metadata editor, `<project>_VCFs` accumulation
folder — all on main now. No conflicts on merge (predictable
SESSION_HANDOFF.md collision resolved keeping main's version via
three-way merge).

**Dev OOD app** (`vsnp_gui_dev` + `kraken_id_parse_gui_dev`). Branch
picker on the form, per-session worktree at `/tmp/vsnp_gui_dev_<tag>/`
or `/tmp/kraken_id_parse_gui_dev_<tag>/`, `vite build` in the
worktree's frontend at session start (node_modules symlinked from
prod), `uvicorn --reload` so backend tweaks hot-pickup without a
session relaunch. Cross-user `safe.directory` fix on both dev cards'
`before.sh.erb` because the prod checkouts have different Linux
owners (vsnp_gui→vxk1, kraken→tks5563) — any user not matching the
checkout owner used to hit `fatal: detected dubious ownership` and
abort. Source-controlled copies in [`ood/apps/`](../../ood/apps/).

**Repo transferred from `vkapur/vsnp_gui` to `kapurlab/vsnp_gui`**
(2026-05-29). Public visibility (required for free-tier org
collaborator slots). Old URL redirects automatically. Local + wgs3
remotes both updated to the kapurlab URL.

**CodeRabbit installed at the kapurlab org level** with a tuned
`.coderabbit.yaml` at the repo root. Profile is `chill`, no
request-changes-workflow, path-specific reviewer instructions encode
our invariants (OOD relative-URL rule, filesystem path-allowlist,
hardcoded `/srv/kapurlab/` paths flagged for the NIVEDI
parameterization story, `target="vsnp_igv"` and the
`window.__vsnpLaunchIgv` JS shim, etc.). First real PR opened
during this session — Tod's `feature/cross-tool-file-visibility`
(PR #1) — got CodeRabbit's first review; merged cleanly via
`0ecfbeb`.

**Tickets filed** without implementation (deferred to a future
session): **T-47** (auto-refresh step2 polling asymmetry — Tod
self-flagged it, we diagnosed it, small fix, hasn't shipped),
**T-48** (project access control via per-project groups — design
doc, no code yet).

**NIVEDI install playbook** (T-49 + T-50) — scoping conversation
done, no code. NIVEDI: 64 CPUs / 500 GB / 20 TB, local Linux users,
internal-only, weeks-preferred timeline. Recommended approach is the
hybrid (OSC's official Ansible playbook for OOD core + our
`install.sh` for the vSNP layer on top) with a `site.conf`
parameterization for site-specific paths/names/group prefixes
(`/srv/kapurlab/` → `/srv/nivedi/`, `kapurlab-admins` →
`nivedi-admins`, etc.). Tod's reply to `PLATFORM_PROPOSAL-2.md` is
substantive and not yet integrated into the proposal — six concrete
edits drafted (controls section, Conda/Apptainer reframe, NAHLN bar
simplification, handoff package clarification, audit-recursion
section, multi-institution rephrase). The proposal still lives in
`/Users/vivekkapur/Downloads/PLATFORM_PROPOSAL-2.md` (not yet
committed to the repo).

## Production state on wgs3

- **`/srv/kapurlab/tools/vsnp_gui`** is on `main` at `507f192`.
  Frontend rebuilt for the merge commit + every subsequent fix; OOD
  prod card serves the current `dist/`.
- **`/srv/kapurlab/tools/kraken_id_parse_gui`** is on `main` (Tod's
  workspace; he owns it). Dev card patched on wgs3 (see TOD_QUEUE.md
  for the mirror-back-to-source ask).
- **Dev OOD cards** (`vsnp_gui_dev`, `kraken_id_parse_gui_dev`) both
  live and tested. Branch picker accepts any branch on origin; per
  session worktree under `/tmp/<app>_dev_<timestamp>_<pid>/` with
  daily cleanup.
- **Active projects on wgs3** for testing:
  - `/home/vxk1/projects/quick2/` (SARS-CoV-2 deer, 6 samples,
    cascade + sorted xlsx exist). Vivek's only.
  - `/home/vxk1/projects/nagalingam_test/` (MTBC, 18 samples Mg+SRR,
    cascade1 + La3_orygis_cascade1 + La3_orygis_sorted xlsx). Has
    imported-VCF rows (SRR/ERR/CP) that exercise the no-BAM path.
    Vivek's only.
  - `/srv/kapurlab/projects/demo_sars_cov_2/` (shared,
    `proj-demo_sars_cov_2` group readable by both Vivek and Tod —
    Tod's accessible test target).
  - Tod's `/home/tks5563/projects/test{,2,3,4}_tb_sra/` (Tod's
    own; he runs them with the new Kraken-from-Step1 branch).

## Active feature branches awaiting merge

Tod's testing pipeline; he's deliberately holding off on merging
these until he completes a larger-dataset run.

| Branch | Repo | Scope | Status |
|---|---|---|---|
| `feature/step1-run-kraken` | vsnp_gui | 9 commits, +785 LOC. Kraken launch from Step 1 + per-sample results in vSNP sample view | Backend smoke-tested, paired with kraken_id_parse_gui's feature/create-projects |
| `feature/create-projects` | kraken_id_parse_gui | 7 commits, ~1100 changes. Project-based UX parity with vSNP + the `mtime` KeyError fix | Backend smoke-tested by Vivek on demo_sars_cov_2 |
| `feature/cross-tool-file-visibility` | vsnp_gui | (merged via PR #1, `0ecfbeb`) | Done |
| `feature/posthoc-step1` | vsnp_gui | Tod's WIP | Status unknown |
| `feature/vcf-edit` | vsnp_gui | Tod's WIP | Status unknown |

## Open items for the next session

In rough priority order:

1. **NIVEDI install playbook** (T-49 + T-50) — start the wgs3 audit,
   draft `docs/deploy/INSTALL_OOD.md`, draft `deploy/install_ood.sh`
   with a `site.conf` parameterization. No NIVEDI SSH access yet, so
   the work is local-only (audit + draft). Vivek has the green light.

2. **PROPOSAL.md integration of Tod's feedback.** Six concrete edits
   drafted (above). Currently the proposal isn't in the repo — bring
   it under `docs/proposals/PLATFORM_PROPOSAL.md` first, then apply
   the edits, then send Tod the updated version. He hasn't replied
   since the original substantive note.

3. **T-47** (step2 auto-refresh polling asymmetry). Small fix (~10
   LOC in `App.jsx`), defer-it-but-don't-forget-it level.

4. **CodeRabbit calibration.** First PR landed (Tod's #1), see how its
   review reads. Tune the `.coderabbit.yaml` if it's noisy or missing
   things. Probably one or two pass-throughs to settle.

5. **Tom's branches review when he's ready to merge.** He's
   explicitly holding off; he'll signal. When he opens PRs, CodeRabbit
   will pre-review; we add the second human pair of eyes.

6. **The two red-team UNRESOLVEDs still blocking T-27** — never
   adjudicated this session because the cascade-IGV work grew into
   multi-day work. `ensure_assembly()` locking posture (defer or fix
   now) and §6 provenance schema vs T-07 (port `capture_env_snapshot`
   or accept reduction). T-27 still blocked on these.

7. **Mirror Tod's note items.** Two items already in TOD_QUEUE.md for
   the next time Vivek messages him: the `--kraken-only` "at None"
   cosmetic, the kraken GUI auto-refresh, and the mirror-back of the
   kraken_id_parse_gui_dev safe.directory patch to that repo's source.

## Recent commits worth knowing about

```
507f192 ood-dev: set git safe.directory before fetching prod repo
02bb234 gitignore: ignore Office lock files (~$*)
642d322 ci: add .coderabbit.yaml for tuned auto-review
0ecfbeb Merge pull request #1 from vkapur/feature/cross-tool-file-visibility
be8e8b4 dev OOD: build frontend from worktree source on session start
000ecf4 Surface Kraken ID Parse results in vSNP sample view
f57008d IgvStandalone: call browser.search() after createBrowser
1e5ba98 IgvStandalone: expand single-position locus to a small range
e5c2745 Merge t02-cascade-igv-click — cascade IGV click + GFF/VCF tracks + same-window navigation
a97ede2 xlsx_html: visually distinguish calls-only IGV links (imported VCFs)
0b257b0 IGV: additive same-window navigation (postMessage, not URL replace)
cd1e7e8 xlsx_html: window.open onclick handler for same-window IGV (Safari fix)
51f6f6d docs: T-48 — project access control via per-project groups
58c3a28 Merge tstuber_2026-05-20 — project-level reference, timestamped step2, VCF browser, metadata editor, _VCFs folder
```

Full history via `git log --oneline 8985763..` (about 30 commits).

## Verify when picking up

```sh
# Local Mac, main HEAD
git log --oneline -3 main
# Expected: 507f192 ood-dev: set git safe.directory ...

# Remote sanity
git remote -v
# Expected: origin https://github.com/kapurlab/vsnp_gui.git

# Recent open PRs / CodeRabbit activity
gh pr list --state open --repo kapurlab/vsnp_gui
gh pr list --state merged --limit 3 --repo kapurlab/vsnp_gui

# Branches Tod's keeping warm
git fetch origin --prune
git branch -r | grep feature/

# wgs3 prod checkout
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui && git log --oneline -2'
# Expected: 507f192 ... ; on main

# Dev card sanity (both vsnp_gui_dev and kraken_id_parse_gui_dev should
# launch cleanly for vxk1 — cross-user safe.directory is now in)
ssh wgs3 'sudo ls /var/www/ood/apps/sys/ | grep -E "vsnp_gui|kraken"'

# CodeRabbit yaml present
test -f .coderabbit.yaml && echo "yes" || echo "no"
```

## Anchor docs (read these next, in order)

- [`docs/dev/TICKETS.md`](TICKETS.md) — T-IDs, milestones, T-47/T-48
  status
- [`docs/dev/TOD_QUEUE.md`](TOD_QUEUE.md) — open items for next Tod
  conversation (kraken note items, GitHub PR merge gate, sample
  metadata editor deep-link, etc.)
- [`docs/dev/redteam/DECISIONS.md`](redteam/DECISIONS.md) — the two
  UNRESOLVED red-team decisions still blocking T-27
- [`ood/README.md`](../../ood/README.md) — OOD app deploy convention
  (sudo cp + restart pattern)
- `/Users/vivekkapur/Downloads/PLATFORM_PROPOSAL-2.md` — the v1.1
  platform proposal Tod gave point-by-point feedback on; the
  proposal needs (a) to move into the repo, (b) the six edits from
  Tod's reply applied

## Note for next-session Claude

This session was unusually long. The cascade-IGV work alone went
through ~12 iterations because every browser-quirk (Safari named
windows, large-genome locus zoom, imported-VCF samples, cross-user
git ownership) surfaced something the prior fix didn't catch. The
pattern: each "small" feature exposes ~3 environmental edge cases.
Budget the same generosity for similar UI-meets-OOD-meets-bioinformatics
work.

The user is Vivek, working from Mac (`vxk1@kapurlab-wgs3.tailf38ff4.ts.net`
via Tailscale). Tod (`tks5563`) is the second collaborator, USDA NAHLN
context. Both have admin sudo on wgs3 via `kapurlab-admins` group.
Repo is now `kapurlab/vsnp_gui`, public, CodeRabbit reviewing.
