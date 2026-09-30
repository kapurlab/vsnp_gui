"""Unit tests for the SNP-table preview cache's disk budget.

The cache lives in the user's HOME by default, and an HPC home directory is
usually quota'd. Left unbounded it would genuinely fill one: a single project
here holds 2,330 Step 2 SNP tables (480 of them over 1 MB), which is roughly
700 MB of rendered HTML if someone browsed all of them — and a site runs
several such projects, with duplicate entries accruing besides, because the
cache key includes which samples have BAMs and that set changes as Step 1
progresses.

So the budget is the feature, and this pins it down: the cache stays under its
cap, evicts least-recently-used first, and can be switched off entirely.

Run directly:  python test_xlsx_cache.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as app_main  # noqa: E402


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


def dir_bytes(d: Path) -> int:
    return sum(f.stat().st_size for f in d.glob("*.html"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="xlsx_cache_"))
    saved = {k: os.environ.get(k) for k in
             ("VSNP_GUI_PREVIEW_CACHE_DIR", "VSNP_GUI_PREVIEW_CACHE_MB")}
    try:
        cache = tmp / "cache"
        cache.mkdir()
        os.environ["VSNP_GUI_PREVIEW_CACHE_DIR"] = str(cache)

        print("\n[the cache is bounded and evicts oldest first]")
        # Ten 100 KB entries against a budget of ~0.5 MB.
        os.environ["VSNP_GUI_PREVIEW_CACHE_MB"] = "0.5"
        budget = app_main._xlsx_cache_budget_bytes()
        assert_eq(budget, int(0.5 * 1024 * 1024), "budget honours the env var")
        for i in range(10):
            entry = cache / f"{i:064d}.html"
            entry.write_text("x" * 100_000)
            # Distinct, increasing mtimes so "oldest" is unambiguous.
            os.utime(entry, (time.time() + i, time.time() + i))
        assert_eq(len(list(cache.glob("*.html"))), 10, "ten entries written")

        app_main._xlsx_cache_prune(cache, budget)
        remaining = sorted(p.name for p in cache.glob("*.html"))
        assert_true(dir_bytes(cache) <= budget, "cache is within budget after pruning")
        assert_true(len(remaining) < 10, "something was evicted")
        # The newest survive: entries were stamped oldest-first by index.
        assert_true(remaining[-1] == f"{9:064d}.html", "newest entry kept")
        assert_true(f"{0:064d}.html" not in remaining, "oldest entry evicted")
        print(f"      kept {len(remaining)} of 10 entries "
              f"({dir_bytes(cache)} bytes <= {budget})")

        print("\n[a cache already under budget is left alone]")
        before = sorted(p.name for p in cache.glob("*.html"))
        app_main._xlsx_cache_prune(cache, 10 * 1024 * 1024)
        assert_eq(sorted(p.name for p in cache.glob("*.html")), before,
                  "no eviction when there is room")

        print("\n[caching can be switched off entirely]")
        os.environ["VSNP_GUI_PREVIEW_CACHE_MB"] = "0"
        assert_eq(app_main._xlsx_cache_budget_bytes(), 0, "zero budget")
        fake = tmp / "table.xlsx"
        fake.write_text("not really an xlsx")
        assert_eq(app_main._xlsx_cache_path(fake, "proj"), None,
                  "no cache path when disabled")

        print("\n[the directory is redirectable, and the key is stable]")
        os.environ["VSNP_GUI_PREVIEW_CACHE_MB"] = "250"
        p1 = app_main._xlsx_cache_path(fake, "proj", extra="meta:1")
        p2 = app_main._xlsx_cache_path(fake, "proj", extra="meta:1")
        assert_eq(p1, p2, "same inputs give the same entry")
        assert_true(str(p1).startswith(str(cache)), "entry lands in the chosen dir")
        p3 = app_main._xlsx_cache_path(fake, "proj", extra="meta:2")
        assert_true(p1 != p3, "a changed selection or metadata is a different entry")
        p4 = app_main._xlsx_cache_path(fake, "other", extra="meta:1")
        assert_true(p1 != p4, "another project is a different entry")

        print("\n[which rows can open is checked on a hit, not baked into the key]")
        # The key no longer carries the sample sets (building them walked
        # every Step 1 folder); a cached window lists the rows it drew without
        # reads and only those are asked about again.
        window = {"unlinked": ["A"], "calls_only": ["B"], "rows": []}
        assert_true(not app_main._preview_links_stale(window, set(), set()),
                    "nothing gained: the cached render stands")
        assert_true(app_main._preview_links_stale(window, {"A"}, set()),
                    "an unlinked row that now has reads makes the render stale")
        assert_true(app_main._preview_links_stale(window, set(), {"A"}),
                    "an unlinked row that now has an imported VCF makes it stale")
        assert_true(app_main._preview_links_stale(window, {"B"}, set()),
                    "a calls-only row that now has reads makes it stale")
        assert_true(not app_main._preview_links_stale(window, set(), {"B"}),
                    "a calls-only row still without reads does not")
        assert_true(not app_main._preview_links_stale({"rows": []}, {"A", "B"}, {"A"}),
                    "a window that linked every row is never stale")
        asked = []

        class Asks:
            def __contains__(self, name):
                asked.append(name)
                return False
        app_main._preview_links_stale({"unlinked": ["A"], "calls_only": ["B"]}, Asks(), set())
        assert_eq(asked, ["A", "B"], "only the rows drawn without reads are asked about")
        e1 = app_main._preview_etag(p1, {"unlinked": ["A"], "calls_only": []})
        e2 = app_main._preview_etag(p1, {"unlinked": [], "calls_only": []})
        assert_true(e1.startswith(p1.name) and e2.startswith(p1.name),
                    "the ETag carries the entry's name")
        assert_true(e1 != e2, "a re-render that links a row is a new ETag")
        assert_eq(e1, app_main._preview_etag(p1, {"unlinked": ["A"], "calls_only": []}),
                  "the same render has the same ETag")

        print("\n[a bad budget value falls back rather than crashing]")
        os.environ["VSNP_GUI_PREVIEW_CACHE_MB"] = "not-a-number"
        assert_eq(app_main._xlsx_cache_budget_bytes(),
                  app_main._XLSX_CACHE_DEFAULT_MB * 1024 * 1024,
                  "garbage env value falls back to the default")

        print("\nAll preview-cache tests passed.")
        return 0
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
