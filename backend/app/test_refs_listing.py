"""Unit tests for refs.list_references / refs.reference_locations.

The behaviors under test exist for one real confusion: a fresh install
downloads a reference set while the user already has one registered, and the
dropdowns then showed every reference twice — plus `.git`, because a
reference root is often a git clone and every subdirectory used to count as
a "reference". vsnp3 resolves `-t <name>` by directory name, first
registered root wins, so the duplicates were never two choices — just two
labels for one. list_references now returns each name once with its winning
root and any shadowed copies; reference_locations reports what each root
actually contributes so removing a redundant one is an informed act.

Run directly:  python test_refs_listing.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refs


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


def make_ref(root: Path, name: str, filename: str = "ref.fasta") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(">x\nACGT\n", encoding="utf-8")
    return d


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="refs_listing_"))
    try:
        vsnp3 = tmp / "vsnp3"
        deps = vsnp3 / "dependencies"
        deps.mkdir(parents=True)
        # The site's reference location list (refs.py), already seeded, so
        # vsnp3's own list inside the install is not consulted.
        site = tmp / "site" / "refs" / "vsnp3" / "locations"
        site.mkdir(parents=True)
        rop = site / "reference_locations.txt"
        (site / ".reference_locations.txt.seeded").write_text("test\n")
        cfg = {"vsnp3_path": str(vsnp3), "reference_locations_file": str(rop)}

        root_a = tmp / "downloaded_set"     # e.g. the install-time clone
        root_b = tmp / "user_set"           # e.g. a pre-existing local copy
        root_a.mkdir()
        root_b.mkdir()

        # root_a: a git clone — .git plus two references and a junk dir.
        (root_a / ".git").mkdir()
        (root_a / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        make_ref(root_a, "Brucella_suis3")
        make_ref(root_a, "Mycobacterium_H37")
        (root_a / "not_a_reference").mkdir()
        (root_a / "not_a_reference" / "notes.txt").write_text("x", encoding="utf-8")

        # root_b: duplicates Brucella_suis3, adds one of its own, and has one
        # just-created (xlsx templates only, FASTA still downloading).
        make_ref(root_b, "Brucella_suis3")
        make_ref(root_b, "mtbc0_v1.1", filename="H37_define_filter.xlsx")

        rop.write_text(f"{root_a}\n{root_b}\n", encoding="utf-8")

        print("\n[filtering + dedupe]")
        listed = refs.list_references(cfg)
        names = [r["name"] for r in listed]
        assert_eq(names, ["Brucella_suis3", "Mycobacterium_H37", "mtbc0_v1.1"],
                  "unique names, vsnp3 resolution order, no .git/junk")
        suis = next(r for r in listed if r["name"] == "Brucella_suis3")
        assert_eq(suis["path"], str(root_a / "Brucella_suis3"),
                  "first registered root wins (matches vsnp3 -t)")
        assert_eq(suis["root"], str(root_a), "winner's root reported")
        assert_eq([s["path"] for s in suis["shadowed"]],
                  [str(root_b / "Brucella_suis3")],
                  "the losing copy is reported as shadowed")
        assert_eq(next(r for r in listed if r["name"] == "mtbc0_v1.1")["shadowed"],
                  [], "unique reference has no shadowed copies")

        print("\n[a registered symlink of a registered root is not a duplicate]")
        link_root = tmp / "link_root"
        link_root.symlink_to(root_b)
        rop.write_text(f"{root_a}\n{root_b}\n{link_root}\n", encoding="utf-8")
        listed = refs.list_references(cfg)
        mtbc = next(r for r in listed if r["name"] == "mtbc0_v1.1")
        assert_eq(mtbc["shadowed"], [],
                  "same dir via symlinked root not counted as a copy")

        print("\n[reference_locations stats]")
        rop.write_text(f"{root_a}\n{root_b}\n{tmp / 'gone'}\n", encoding="utf-8")
        locs = refs.reference_locations(cfg)
        assert_eq(len(locs), 3, "one row per list line")
        row_a = next(l for l in locs if l["path"] == str(root_a))
        row_b = next(l for l in locs if l["path"] == str(root_b))
        row_gone = next(l for l in locs if l["path"] == str(tmp / "gone"))
        assert_eq((row_a["provides"], row_a["shadowed"]), (2, 0),
                  "first root provides both its references")
        assert_eq((row_b["provides"], row_b["shadowed"]), (1, 1),
                  "second root provides its unique ref, one shadowed")
        assert_eq((row_gone["exists"], row_gone["provides"]), (False, 0),
                  "missing path flagged, contributes nothing")
        assert_true(all(not l["implicit"] and l["source"] == "site" for l in locs),
                    "every row comes from the site list, none implicit")
        assert_eq((row_gone["status"], row_a["status"]), ("missing", "ok"),
                  "each row says whether it can be opened")

        print("\n[no list at all: no locations, and vsnp3's dependencies/ is not one]")
        rop.unlink()
        make_ref(deps, "Fallback_ref")
        assert_eq(refs.reference_locations(cfg), [], "no rows without a list")
        assert_eq(refs.list_references(cfg), [],
                  "vsnp3 never searches its dependencies/ either, so neither does the list")

        print("\nAll refs listing tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
