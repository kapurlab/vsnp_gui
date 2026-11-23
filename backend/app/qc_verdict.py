"""T-09 sample QC verdict.

Pure function that takes a `qc_summary` row (one of the dicts returned by
the /api/projects/<p>/qc_summary endpoint, fields named after vsnp3's
*_stats.xlsx columns) plus a thresholds object, and emits a three-tier
verdict (pass / review / fail) with a list of reasons that tripped the
non-pass tiers.

Signals consulted:

  - **Coverage** — `Average Depth`, e.g. "223.8X". Below `pass_min` ⇒ review;
    below `review_min` ⇒ fail. Missing field skips the signal.
  - **Mapping rate** — derived as `100 - Unmapped Percent`, e.g. "0.0%" ⇒ 100.
    Same threshold structure as coverage.
  - **Duplicate removal** — not a signal of its own, a qualifier on the depth
    one. vsnp3 runs `samtools markdup -r`, which for single-end reads can only
    call two reads duplicates when they share a start coordinate. An amplicon
    library piles every read on the same few starts, so nearly all of them go,
    and a sample sequenced to tens of thousands of fold reports about 1x.
    Measured 22 November 2026 on SRR6232295, an Ion Torrent run over a 141 bp
    stretch of VP1: 2,766,492 reads mapped, 100.0% of them removed, depth
    reported as 1.1x. Read plainly that says re-sequence deeper, when depth was
    never the problem. So where duplicates took most of the mapped reads the
    depth reason says so and gives the figure before removal.

  - **Contamination** — anything truthy in a future `Sourmash Contamination`
    /  `_contamination` field forces the verdict to at least `review`. vsnp3
    doesn't currently emit this — handled defensively so the verdict still
    lands when nothing's set. vsnp_gui's FMDV mode sets `_contamination` from
    its per-sample reference chooser (a distant reference, or a second
    serotype in the reads), with its own wording in `_contamination_reason`.
  - **Genome covered** — `Genome with Coverage`, e.g. "99.85%". Consulted
    only when the thresholds carry a `genome_coverage` block, which stock
    projects do not: for a bacterial isolate the fraction covered says little
    that depth does not, while for an 8 kb amplicon-sequenced virus it is the
    first thing to look at (the consensus and the SNP calls are only as good
    as the covered fraction).

Thresholds shape:

    {
      "coverage":     {"pass_min": 30.0, "review_min": 10.0},
      "mapping_rate": {"pass_min": 90.0, "review_min": 70.0},
      "contamination_review": True,
      "genome_coverage": {"pass_min": 90.0, "review_min": 70.0},   # optional
    }

A `review_min` of null in any block means that signal never fails, only
reviews (FMDV mode uses this for mapping rate, since host reads dominate a
field sample and a low rate describes the sample, not the result).

Per-project override: callers should pass the merged thresholds (project >
user-config > module DEFAULTS). `merge_thresholds()` handles the merge.
"""
from __future__ import annotations

import copy
from typing import Any


# Share of mapped reads that has to go before a depth figure is reported as
# what it is, a post-deduplication number. Half is already enough to halve the
# depth, and an ordinary isolate library sits far below it.
DEDUP_NOTE_MIN_PERCENT = 50.0


# Module-level defaults — match config.DEFAULTS["qc_thresholds"]. Duplicated
# here so callers (e.g. tests) can import without pulling the whole config
# stack. Keep the two in sync.
DEFAULTS: dict[str, Any] = {
    "coverage": {"pass_min": 30.0, "review_min": 10.0},
    "mapping_rate": {"pass_min": 90.0, "review_min": 70.0},
    "contamination_review": True,
}


# Levels are ordered: pass < review < fail. Used to escalate the verdict
# monotonically as signals fire.
_LEVELS = ("pass", "review", "fail")


def _level_index(level: str) -> int:
    return _LEVELS.index(level)


def _max_level(*levels: str) -> str:
    return max(levels, key=_level_index)


def _parse_depth(value: Any) -> float | None:
    """Parse 'Average Depth' values like "223.8X". Returns None if unparseable."""
    if value is None:
        return None
    # vsnp3 writes depth with a thousands separator past 999x ("2,762.2"),
    # which float() refuses; without the strip a deep sample simply had no
    # depth signal and the depth threshold was never applied to it.
    s = str(value).strip().rstrip("xX").rstrip("X").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_percent(value: Any) -> float | None:
    """Parse percent strings like "0.0%" or bare numbers. Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().rstrip("%").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def merge_thresholds(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow merge across nested dicts in priority order: later wins.

    Designed for `merge_thresholds(DEFAULTS, user_cfg, project_overrides)` —
    each layer can be None or partial. Sub-dicts are merged key-by-key so a
    project that only overrides `coverage.pass_min` still inherits the
    user's `mapping_rate` config.
    """
    out: dict[str, Any] = copy.deepcopy(DEFAULTS)
    for layer in layers:
        if not layer:
            continue
        for k, v in layer.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k].update(v)
            else:
                out[k] = v
    return out


def _parse_count(value: Any) -> float | None:
    """2,766,492 -> 2766492.0. None when the field is absent or not a number."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def duplicate_context(row: dict[str, Any]) -> dict[str, float | None]:
    """What duplicate removal did to this sample, from vsnp3's stats columns.

    Returns `share`, the percentage of mapped reads removed, and
    `depth_before`, the mean depth those reads gave across the reference
    before removal (mapped reads times mean read length over reference
    length). Either is None when the columns needed are not there, which is
    how a v1 stats sheet and a simulated sample both behave."""
    share = _parse_percent(row.get("Duplicate Percent of Mapped Reads"))
    mapped = (_parse_count(row.get("Mapped Single Reads")) or 0.0) + \
             (_parse_count(row.get("Mapped Paired Reads")) or 0.0)
    if share is None and mapped:
        dups = (_parse_count(row.get("Duplicate Single Reads")) or 0.0) + \
               (_parse_count(row.get("Duplicate Paired Reads")) or 0.0)
        share = 100.0 * dups / mapped
    read_len = _parse_count(row.get("R1 Ave Length"))
    ref_len = _parse_count(row.get("Reference Length"))
    depth_before = None
    if mapped and read_len and ref_len:
        depth_before = mapped * read_len / ref_len
    return {"share": share, "depth_before": depth_before}


def _dedup_qualifier(dup: dict[str, float | None]) -> str:
    """The clause appended to a depth reason when duplicates took most of the
    mapped reads. Empty when they did not, so an ordinary sample reads as
    before."""
    share = dup.get("share")
    if share is None or share < DEDUP_NOTE_MIN_PERCENT:
        return ""
    before = dup.get("depth_before")
    tail = f", about {before:,.0f}× before" if before else ""
    return f" (after duplicate removal, which took {share:.1f}% of mapped reads{tail})"


def compute_verdict(row: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    """Return {level, reasons, signals} for a single qc_summary row.

    `level` is one of "pass" / "review" / "fail". `reasons` is a list of
    short strings naming each signal that escalated the level (empty if
    pass). `signals` echoes the parsed numeric values so the frontend can
    render them without re-parsing.
    """
    level = "pass"
    reasons: list[str] = []
    signals: dict[str, float | bool | None] = {}

    # Coverage (Average Depth)
    cov = _parse_depth(row.get("Average Depth"))
    signals["coverage"] = cov
    dup = duplicate_context(row)
    signals["duplicate_share"] = dup["share"]
    signals["coverage_before_dedup"] = dup["depth_before"]
    dedup_note = _dedup_qualifier(dup)
    cov_cfg = thresholds.get("coverage") or {}
    pass_min = cov_cfg.get("pass_min")
    review_min = cov_cfg.get("review_min")
    if cov is not None and pass_min is not None and review_min is not None:
        if cov < review_min:
            level = _max_level(level, "fail")
            reasons.append(f"coverage {cov:.1f}× < {review_min:g}× fail threshold{dedup_note}")
        elif cov < pass_min:
            level = _max_level(level, "review")
            reasons.append(f"coverage {cov:.1f}× < {pass_min:g}× pass threshold{dedup_note}")

    # Mapping rate (100 - Unmapped Percent)
    unmapped = _parse_percent(row.get("Unmapped Percent"))
    mapping_rate = (100.0 - unmapped) if unmapped is not None else None
    signals["mapping_rate"] = mapping_rate
    mr_cfg = thresholds.get("mapping_rate") or {}
    mr_pass = mr_cfg.get("pass_min")
    mr_review = mr_cfg.get("review_min")
    if mapping_rate is not None and mr_pass is not None:
        if mr_review is not None and mapping_rate < mr_review:
            level = _max_level(level, "fail")
            reasons.append(f"mapping rate {mapping_rate:.1f}% < {mr_review:g}% fail threshold")
        elif mapping_rate < mr_pass:
            level = _max_level(level, "review")
            reasons.append(f"mapping rate {mapping_rate:.1f}% < {mr_pass:g}% pass threshold")

    # Genome covered (Genome with Coverage) — only when thresholds ask for it
    gc_cfg = thresholds.get("genome_coverage") or {}
    genome_cov = _parse_percent(row.get("Genome with Coverage"))
    signals["genome_coverage"] = genome_cov
    gc_pass = gc_cfg.get("pass_min")
    gc_review = gc_cfg.get("review_min")
    if genome_cov is not None and gc_pass is not None:
        if gc_review is not None and genome_cov < gc_review:
            level = _max_level(level, "fail")
            reasons.append(f"genome covered {genome_cov:.1f}% < {gc_review:g}% fail threshold")
        elif genome_cov < gc_pass:
            level = _max_level(level, "review")
            reasons.append(f"genome covered {genome_cov:.1f}% < {gc_pass:g}% pass threshold")

    # Contamination (placeholder for sourmash output — vsnp3 doesn't emit
    # this field today, so it's only consulted if explicitly set on the row).
    contam_flag = row.get("Sourmash Contamination") or row.get("_contamination")
    contam_truthy = bool(contam_flag) and str(contam_flag).strip().lower() not in ("", "0", "false", "none", "no")
    signals["contamination"] = contam_truthy if contam_flag is not None else None
    if contam_truthy and thresholds.get("contamination_review", True):
        level = _max_level(level, "review")
        reasons.append(row.get("_contamination_reason") or f"contamination flag set ({contam_flag})")

    return {"level": level, "reasons": reasons, "signals": signals}
