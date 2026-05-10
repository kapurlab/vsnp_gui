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
  - **Contamination** — anything truthy in a future `Sourmash Contamination`
    /  `_contamination` field forces the verdict to at least `review`. vsnp3
    doesn't currently emit this — handled defensively so the verdict still
    lands when nothing's set.

Thresholds shape:

    {
      "coverage":     {"pass_min": 30.0, "review_min": 10.0},
      "mapping_rate": {"pass_min": 90.0, "review_min": 70.0},
      "contamination_review": True,
    }

Per-project override: callers should pass the merged thresholds (project >
user-config > module DEFAULTS). `merge_thresholds()` handles the merge.
"""
from __future__ import annotations

import copy
from typing import Any


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
    s = str(value).strip().rstrip("xX").rstrip("X").strip()
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
    s = str(value).strip().rstrip("%").strip()
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
    cov_cfg = thresholds.get("coverage") or {}
    pass_min = cov_cfg.get("pass_min")
    review_min = cov_cfg.get("review_min")
    if cov is not None and pass_min is not None and review_min is not None:
        if cov < review_min:
            level = _max_level(level, "fail")
            reasons.append(f"coverage {cov:.1f}× < {review_min:g}× fail threshold")
        elif cov < pass_min:
            level = _max_level(level, "review")
            reasons.append(f"coverage {cov:.1f}× < {pass_min:g}× pass threshold")

    # Mapping rate (100 - Unmapped Percent)
    unmapped = _parse_percent(row.get("Unmapped Percent"))
    mapping_rate = (100.0 - unmapped) if unmapped is not None else None
    signals["mapping_rate"] = mapping_rate
    mr_cfg = thresholds.get("mapping_rate") or {}
    mr_pass = mr_cfg.get("pass_min")
    mr_review = mr_cfg.get("review_min")
    if mapping_rate is not None and mr_pass is not None and mr_review is not None:
        if mapping_rate < mr_review:
            level = _max_level(level, "fail")
            reasons.append(f"mapping rate {mapping_rate:.1f}% < {mr_review:g}% fail threshold")
        elif mapping_rate < mr_pass:
            level = _max_level(level, "review")
            reasons.append(f"mapping rate {mapping_rate:.1f}% < {mr_pass:g}% pass threshold")

    # Contamination (placeholder for sourmash output — vsnp3 doesn't emit
    # this field today, so it's only consulted if explicitly set on the row).
    contam_flag = row.get("Sourmash Contamination") or row.get("_contamination")
    contam_truthy = bool(contam_flag) and str(contam_flag).strip().lower() not in ("", "0", "false", "none", "no")
    signals["contamination"] = contam_truthy if contam_flag is not None else None
    if contam_truthy and thresholds.get("contamination_review", True):
        level = _max_level(level, "review")
        reasons.append(f"contamination flag set ({contam_flag})")

    return {"level": level, "reasons": reasons, "signals": signals}
