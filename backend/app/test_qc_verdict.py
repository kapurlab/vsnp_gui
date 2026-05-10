"""Smoke tests for app.qc_verdict — pure function, no fixtures needed."""
from __future__ import annotations

import sys

from qc_verdict import DEFAULTS, compute_verdict, merge_thresholds


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def main() -> int:
    print("[merge_thresholds]")
    merged = merge_thresholds(None, None)
    assert_eq(merged["coverage"]["pass_min"], 30.0, "defaults survive empty merge")

    user = {"coverage": {"pass_min": 50.0}}
    project = {"mapping_rate": {"review_min": 50.0}}
    merged = merge_thresholds(user, project)
    assert_eq(merged["coverage"]["pass_min"], 50.0, "user overrides default")
    assert_eq(merged["coverage"]["review_min"], 10.0, "user partial keeps default sub-key")
    assert_eq(merged["mapping_rate"]["review_min"], 50.0, "project overrides user")
    assert_eq(merged["mapping_rate"]["pass_min"], 90.0, "project partial keeps default sub-key")

    print("\n[compute_verdict — pass]")
    row = {"Average Depth": "223.8X", "Unmapped Percent": "0.0%"}
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "pass", "high cov + 100% mapped → pass")
    assert_eq(v["reasons"], [], "no reasons on pass")
    assert_eq(v["signals"]["coverage"], 223.8, "parsed coverage")
    assert_eq(v["signals"]["mapping_rate"], 100.0, "computed mapping_rate")

    print("\n[compute_verdict — review tiers]")
    row = {"Average Depth": "20X", "Unmapped Percent": "0.0%"}  # 20× → review
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "review", "20× depth → review")
    assert len(v["reasons"]) == 1, "exactly one reason on review"
    assert "coverage" in v["reasons"][0], "reason mentions coverage"
    print(f"  OK  reason = {v['reasons'][0]!r}")

    row = {"Average Depth": "100X", "Unmapped Percent": "20%"}  # mapping 80% → review
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "review", "80% mapping → review")
    assert "mapping rate" in v["reasons"][0]
    print(f"  OK  reason = {v['reasons'][0]!r}")

    print("\n[compute_verdict — fail tiers]")
    row = {"Average Depth": "5X", "Unmapped Percent": "0.0%"}  # below review_min
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "fail", "5× depth → fail")

    row = {"Average Depth": "100X", "Unmapped Percent": "50%"}  # mapping 50%
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "fail", "50% mapping → fail")

    print("\n[compute_verdict — escalation: fail beats review]")
    row = {"Average Depth": "5X", "Unmapped Percent": "20%"}  # cov fail + mapping review
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "fail", "fail signal beats review signal")
    assert len(v["reasons"]) == 2, "both reasons recorded"
    print(f"  OK  reasons = {v['reasons']!r}")

    print("\n[compute_verdict — missing fields]")
    row = {}
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "pass", "all-missing → pass (no signals to escalate)")
    assert_eq(v["signals"]["coverage"], None, "missing coverage signal None")
    assert_eq(v["signals"]["mapping_rate"], None, "missing mapping_rate signal None")

    row = {"Average Depth": "garbage"}
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["signals"]["coverage"], None, "unparseable coverage → None")
    assert_eq(v["level"], "pass", "unparseable signal doesn't trip verdict")

    print("\n[compute_verdict — contamination]")
    row = {"Average Depth": "100X", "Unmapped Percent": "0.0%", "_contamination": "Mycobacterium bovis"}
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "review", "contamination → review even with great cov+mapping")
    assert "contamination" in v["reasons"][0]
    print(f"  OK  reason = {v['reasons'][0]!r}")

    row = {"Average Depth": "100X", "Unmapped Percent": "0.0%", "_contamination": ""}
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "pass", "empty contamination string → pass")

    row = {"Average Depth": "5X", "Unmapped Percent": "0.0%", "_contamination": "yes"}
    v = compute_verdict(row, DEFAULTS)
    assert_eq(v["level"], "fail", "contamination cannot downgrade fail to review")

    print("\n[compute_verdict — project override changes verdict]")
    strict = merge_thresholds({"coverage": {"pass_min": 100.0, "review_min": 50.0}})
    row = {"Average Depth": "60X", "Unmapped Percent": "0.0%"}
    v_default = compute_verdict(row, DEFAULTS)
    v_strict = compute_verdict(row, strict)
    assert_eq(v_default["level"], "pass", "60× passes default 30× threshold")
    assert_eq(v_strict["level"], "review", "60× reviews under strict 100× threshold")

    print("\nAll qc_verdict smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
