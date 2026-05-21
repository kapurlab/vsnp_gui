# Tod queue — running list

Lightweight buffer of items to raise with Tod. Append as things come up;
clean up when sent / resolved / dropped. Companion to `TOD_WELCOME.md`.

**Status legend**: `Open` (not yet sent) · `Sent <date>` · `Resolved <date>` · `Dropped`

---

## On his `tstuber_2026-05-20` branch

### Auto-refresh step2 polling asymmetry — Sent 2026-05-20

Step2 polling doesn't guard `step2JobId` changes the way step1 does. New
run kicked off before the old interval tears down can fire
`loadStep2Runs()` against the new job before metadata's written → stale UI.
Mirror the step1 pattern.

→ Tod self-flagged this; we diagnosed it. Waiting on his fix.

### `_project_counts` lost its docstring — Sent 2026-05-20

`backend/app/projects.py:225` PermissionError branch — docstring explaining
the group-permission case got dropped in his edits.

### `project_set_reference` no concurrency check — Sent 2026-05-20

Two-tab race could silently overwrite. Pre-existing pattern in the
codebase, not a regression — flagging for when two-user editing matters.

### Metadata editor scope vs T-17a — Sent 2026-05-20

Tod's `*_metadata.xlsx` writer is correctly outside T-17a's approval-queue
scope (T-17a is for `define_filter` / `remove_from_analysis`). Confirmed,
no action needed unless he disagrees.

---

## Sample metadata editor — placement follow-ups — Sent 2026-05-21

Keeping editor in Reference Editor is right; upload has no per-sample
surface. Two adds + one polish:

1. Badge above Step 1 Results: "N of M samples have no display label →
   Add" — deep-links to editor with sample names pre-filled.
2. Same link in Step 1 Samples list (post-Setup, pre-Run).
3. Editor accepts URL param to pre-fill rows from project context.
4. Reference Editor doesn't auto-pick the selected project's reference
   even though projects now have one — a click he could save.

---

## Dropped

- ~~`e.g. 99-0100` example understates real name length~~ — Dropped
  2026-05-21. The short-ID example correctly represents the canonical
  USDA workflow (short opaque IDs → expanded display labels). The deer
  SARS-CoV-2 case is the outlier (metadata already in filename), not the
  norm.

---

## Open / not yet sent

### Adopt GitHub PRs as the merge gate? — Open (2026-05-21)

We're setting up a parallel dev OOD app (`vSNP GUI (dev)`) with a
branch-picker so we can test feature branches without touching prod.
The natural merge gate to pair with that is GitHub PRs: branch → push →
open PR → other dev reviews → both approve → squash-merge to `main` →
prod pulls.

This is a soft process change for Tod: he'd open PRs from his feature
branches instead of pushing to main directly (he hasn't yet, but the
norm matters going forward), and review Vivek's PRs in return.
`/ultrareview` available as an optional independent pre-merge read.

Needs his OK before we put it in place. Until then, dev env still gets
built (no process change there — it's just enabling).

### Test ask: cascade/sorted xlsx → IGV click — Open (2026-05-21)

New on branch `t02-cascade-igv-click`. Quick test (5 min):

1. OOD dashboard → Bioinformatics → **SNP Analysis (dev)** → **vSNP GUI (dev)**
2. **Git branch** field: `t02-cascade-igv-click` → Launch
3. Open a project with a Step 2 cascade or sorted xlsx (e.g.
   `quick2_NC_045512_wuhan-hu-1`); view a `name-All_cascade*` or
   `name-All_sorted_*` xlsx
4. Hover a colored variant cell → a small dark "↗ this  ↗ all" pill
   appears in the corner. Click either:
   - **↗ this**: opens IGV in a new tab with just that sample's BAM at
     the cell's locus
   - **↗ all**: opens IGV with every sample in the table at that locus

Edge case worth poking: try the `nagalingam_test` project. Its SRR/ERR
rows came in as direct VCF imports (no Step 1 alignment, no BAM). For
those rows, "↗ this" should be greyed out with a "no Step 1 BAM"
tooltip; "↗ all" still works and reports the no-BAM samples as
"imported VCF — no BAM to load" in the IGV header.

The dev session has `uvicorn --reload` so backend tweaks hot-pickup —
useful if he wants to iterate on anything from his laptop. Frontend
edits still need a `vite build`.
