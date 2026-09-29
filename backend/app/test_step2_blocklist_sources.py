"""A sample the remove list holds back says which workbook, and which row.

Reported from the Ames HPC: Step 2 struck samples through as "blocked
(reference)" that were not in the reference's remove_from_analysis workbook,
and nothing on screen said why. Tier A reads EVERY *_remove_from_analysis*.xlsx
in EVERY folder named for the reference, across all registered reference
locations. A second folder of that name in another location is never read by
vsnp3 (its first hit wins) and never opened by the Reference Editor, yet its
list still removed samples from every run.

What is pinned here:

  * the blocked set is unchanged — the new code against the old, kept as an
    oracle, on a layout chosen to be awkward (a shadow copy, a second workbook,
    an Excel lock file, the configured root spelled through a symlink);
  * each name comes back with the Excel row it is on, counting blank and
    hidden rows the way Excel numbers them;
  * /step2/blocklist tells the reference's own workbook from a copy's, even
    when the configured root and reference_options_paths.txt spell the
    reference's folder two ways;
  * the endpoint makes exactly the filesystem calls it made before, however
    the roots are spelled: which folder is the reference's own is read off the
    resolved roots and folder checks the walk already makes.

Run directly:  <env>/bin/python backend/app/test_step2_blocklist_sources.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl  # noqa: E402

import app.main as m  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


class Calls:
    """Counts os.stat / os.lstat / os.scandir / os.listdir calls."""

    def __init__(self):
        self.n = 0
        self._lock = threading.Lock()
        self._saved = {}

    def __enter__(self):
        for name in ("stat", "lstat", "scandir", "listdir"):
            real = getattr(os, name)
            self._saved[name] = real

            def counted(*a, _real=real, **k):
                with self._lock:
                    self.n += 1
                return _real(*a, **k)
            setattr(os, name, counted)
        return self

    def __exit__(self, *exc):
        for name, real in self._saved.items():
            setattr(os, name, real)


# --- oracle: the tier-A reader this replaced, verbatim ---------------------

def old_reference_blocklist_names(cfg, reference):
    if not reference:
        return []
    roots = []
    seen_roots = set()
    configured = str(cfg.get("vsnp3_reference_options_root", "") or "").strip()
    candidates = ([Path(configured)] if configured else []) + list(
        m.reference_roots(Path(str(cfg.get("vsnp3_path", "") or "")))
    )
    for r in candidates:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        roots.append(r)
    names = set()
    for root in roots:
        ref_dir = root / reference
        if not ref_dir.is_dir():
            continue
        for f in ref_dir.glob("*remove_from_analysis*.xlsx"):
            if f.name.startswith("~$"):
                continue
            names.update(m._read_remove_xlsx_names(f))
    return sorted(names)


def old_blocklist_endpoint(project_dir, cfg):
    """The pre-change endpoint's filesystem work, call for call."""
    ref = m._project_reference(project_dir) or ""
    ref_dir = m._project_reference_dir(project_dir, cfg)
    ineffective = m._remove_list_entries(ref_dir)["ineffective"] if ref_dir else []
    return {"reference": ref, "samples": old_reference_blocklist_names(cfg, ref),
            "ineffective": ineffective}


def workbook(path: Path, cells) -> Path:
    """cells: {row: value} in column A (plus hidden rows via a set)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    hidden = cells.pop("hidden", set())
    for row, value in cells.items():
        ws.cell(row=row, column=1, value=value)
    for row in hidden:
        ws.row_dimensions[row].hidden = True
    wb.save(path)
    wb.close()
    return path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="blocklist_sources_"))
    refs_a = tmp / "refsA"          # first registered root: vsnp3 reads H5 here
    refs_b = tmp / "refsB"          # a later root holding another H5 folder
    (refs_a / "H5").mkdir(parents=True)
    (refs_b / "H5").mkdir(parents=True)
    own = workbook(refs_a / "H5" / "H5_remove_from_analysis.xlsx", {
        2: "22-A",
        4: "  22-B ",
        5: "22-H", "hidden": {5},    # a hidden row still removes the sample
        6: 12345,
    })
    # Excel's lock file while someone has the workbook open: never a list.
    (refs_a / "H5" / "~$H5_remove_from_analysis.xlsx").write_bytes(b"lock")
    shadow = workbook(refs_b / "H5" / "H5_remove_from_analysis.xlsx", {1: "22-A", 2: "22-C"})
    second = workbook(refs_b / "H5" / "H5_remove_from_analysis_old.xlsx", {1: "22-D"})
    # The configured root is the same folder as refsA, spelled another way.
    link_a = tmp / "linkA"
    link_a.symlink_to(refs_a, target_is_directory=True)

    proj = tmp / "projects" / "p1"
    proj.mkdir(parents=True)
    (proj / "project.json").write_text('{"reference": "H5"}', encoding="utf-8")

    cfg = {"vsnp3_path": str(tmp / "vsnp3"), "projects_root": str(proj.parent),
           "vsnp3_reference_options_root": str(link_a)}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda _cfg, _name: proj
    m.reference_roots = lambda _p: [refs_a, refs_b]

    print("[_read_remove_xlsx_rows: the row Excel shows]")
    m._REMOVE_XLSX_CACHE.clear()
    check("blank rows are counted, whitespace trimmed, hidden rows kept, numbers read as text",
          m._read_remove_xlsx_rows(own) == [("22-A", 2), ("22-B", 4), ("22-H", 5), ("12345", 6)])
    check("_read_remove_xlsx_names is the same names, in the same order",
          m._read_remove_xlsx_names(own) == ["22-A", "22-B", "22-H", "12345"])
    rows = m._read_remove_xlsx_rows(own)
    rows.append(("MUTATED", 99))
    check("callers get copies; mutating one cannot poison the cache",
          ("MUTATED", 99) not in m._read_remove_xlsx_rows(own))
    check("a missing workbook is no rows", m._read_remove_xlsx_rows(tmp / "none.xlsx") == [])

    print("[the blocked set is the old one]")
    old = old_reference_blocklist_names(cfg, "H5")
    new = m._reference_blocklist_names(cfg, "H5")
    check("_reference_blocklist_names matches the oracle", new == old)
    check("...which is every copy's names, the lock file skipped",
          new == sorted({"22-A", "22-B", "22-H", "12345", "22-C", "22-D"}))
    check("no reference, no names", m._reference_blocklist_names(cfg, "") == [])

    print("[/step2/blocklist: where each name comes from]")
    bl = m.step2_blocklist_get("p1")
    check("samples is the blocked set, as before", bl["samples"] == old)
    check("the reference's folder is the one vsnp3 resolves",
          bl["reference_dir"] == str(refs_a / "H5"))
    got = [(Path(s["path"]).name, Path(s["folder"]).parent.name, s["in_reference_dir"], s["count"])
           for s in bl["sources"]]
    check("one source per workbook, the reference's own told from the copy's, spelled twice or not",
          got == [("H5_remove_from_analysis.xlsx", "linkA", True, 4),
                  ("H5_remove_from_analysis.xlsx", "refsB", False, 2),
                  ("H5_remove_from_analysis_old.xlsx", "refsB", False, 1)])
    check("a name on two lists lists both, with its row in each",
          bl["where"]["22-A"] == [[0, 2], [1, 1]])
    check("a name only a copy holds back points at the copy",
          bl["where"]["22-C"] == [[1, 2]] and bl["where"]["22-D"] == [[2, 1]])
    check("the Excel row survives into the payload", bl["where"]["22-H"] == [[0, 5]])
    check("the ineffective-entry check is untouched", bl["ineffective"] == [])

    print("[a reference that resolves nowhere claims no copy is foreign]")
    m.reference_roots = lambda _p: []
    bl_none = m.step2_blocklist_get("p1")
    check("with no folder of its own to compare against, the flag is None",
          [s["in_reference_dir"] for s in bl_none["sources"]] == [None])
    check("...and the reference dir is blank", bl_none["reference_dir"] == "")
    m.reference_roots = lambda _p: [refs_a, refs_b]

    print("[round trips: nothing slower]")
    # Warm every cache both paths share, then count each once.
    old_blocklist_endpoint(proj, cfg)
    m.step2_blocklist_get("p1")
    with Calls() as c_old:
        old_blocklist_endpoint(proj, cfg)
    with Calls() as c_new:
        m.step2_blocklist_get("p1")
    check(f"the configured root spelled through a symlink: the old calls exactly "
          f"({c_old.n} -> {c_new.n})", c_new.n == c_old.n)
    cfg["vsnp3_reference_options_root"] = str(refs_a)
    old_blocklist_endpoint(proj, cfg)
    m.step2_blocklist_get("p1")
    with Calls() as c_old:
        old_blocklist_endpoint(proj, cfg)
    with Calls() as c_new:
        bl_same = m.step2_blocklist_get("p1")
    check(f"spelled the same: the old calls exactly ({c_old.n} -> {c_new.n})",
          c_new.n == c_old.n)
    check("...and the copy is still told apart",
          [s["in_reference_dir"] for s in bl_same["sources"]] == [True, False, False])

    print("[the configured root is not what vsnp3 reads]")
    # A folder under the configured root that reference_options_paths.txt
    # does not list: vsnp3 never sees it, so it is a copy, not the reference.
    extra = tmp / "configured"
    (extra / "H5").mkdir(parents=True)
    workbook(extra / "H5" / "H5_remove_from_analysis.xlsx", {1: "22-E"})
    cfg["vsnp3_reference_options_root"] = str(extra)
    bl_cfg = m.step2_blocklist_get("p1")
    check("its workbook is flagged as not the reference's own",
          [(Path(s["folder"]).parent.name, s["in_reference_dir"]) for s in bl_cfg["sources"]]
          == [("configured", False), ("refsA", True), ("refsB", False), ("refsB", False)])
    check("...and its names are still blocked, as before",
          bl_cfg["samples"] == old_reference_blocklist_names(cfg, "H5") and "22-E" in bl_cfg["samples"])

    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
