"""The FMDV library's lineage folders are a reference location of their own.

FMDV mode gives vsnp3 a lineage's reference folder itself, so for its first
days nothing listed <library>/reference_options as a reference LOCATION. The
Reference Editor, the defining-SNP groups, the filter positions and the remove
list all resolve a reference through the locations, so an FMDV project had
nothing to open: the files existed, ordinary vsnp3 files, and the only way to
edit them in the GUI was to know the folder and add it by hand, per account
(Ames HPC, 2026-11-20).

Now the folder is a location wherever a library is built: found the way the
library is found, on no list, the same for every user, last in the order so it
never changes which folder an existing name resolves to.

Run directly:  python test_fmdv_reference_location.py
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_ENV_KEYS = ("VSNP_GUI_SITE_ROOT", "BDTOOLS_SITE_ROOT", "BDTOOLS_TOOLS_ROOT", "BDTOOLS_DB_ROOT",
             "VSNP_GUI_SHARED_PROJECTS_ROOT", "XDG_CONFIG_HOME", "HOME")


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}")


def assert_true(cond, label):
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def load(site: Path, home: Path):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ["VSNP_GUI_SITE_ROOT"] = str(site)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["HOME"] = str(home)
    import config
    import refs
    return importlib.reload(config), importlib.reload(refs)


def make_ref(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.fasta").write_text(">x\nACGT\n")
    return d


def make_library(lib: Path, names) -> Path:
    """A built library: the index, and one reference folder per lineage as
    fmdv_reflib.materialize leaves it."""
    root = lib / "reference_options"
    root.mkdir(parents=True)
    (lib / "lineages.json").write_text(json.dumps({"genomes": [{"safe": n} for n in names]}))
    for n in names:
        d = make_ref(root, n)
        (d / f"{n}_define_filter.xlsx").write_text("x")
        (d / f"{n}_remove_from_analysis.xlsx").write_text("x")
    # What the build leaves beside them must not turn into references.
    (root / ".DS_Store").write_text("x")
    (root / "README.txt").write_text("x")
    return root


def site_list(site: Path, lines) -> None:
    folder = site / "refs" / "vsnp3" / "locations"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reference_locations.txt").write_text("".join(f"{l}\n" for l in lines))
    (folder / ".reference_locations.txt.seeded").write_text("test\n")


def main() -> int:
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp).resolve()
            site, home = t / "site", t / "home"
            site.mkdir(); home.mkdir()
            tb = t / "tb_refs"
            make_ref(tb, "Mycobacterium_AF2122")
            site_list(site, [tb])

            print("no library: nothing changes")
            config, refs = load(site, home)
            cfg = config.load_config()
            assert_eq(cfg["fmdv_reference_root"], "", "no library, no root")
            assert_eq([r["name"] for r in refs.list_references(cfg)], ["Mycobacterium_AF2122"],
                      "the references are the site's")
            assert_eq([l["source"] for l in refs.reference_locations(cfg)], ["site"], "one location")
            assert_true(all("fmdv" not in r for r in refs.list_references(cfg)),
                        "an ordinary reference carries no fmdv mark")

            print("a library built under the site: its lineages are references")
            lineages = ["O-EA-2_MH053318", "SAT1-I_KM268899", "SAT2-VII_KU821592"]
            root = make_library(site / "refs" / "fmdv", lineages)
            config, refs = load(site, home)          # a new process: no memo
            cfg = config.load_config()
            assert_eq(cfg["fmdv_reference_root"], str(root), "the library's reference_options")
            listed = refs.list_references(cfg)
            assert_eq([r["name"] for r in listed], ["Mycobacterium_AF2122"] + lineages,
                      "the site's own references first, then the lineages, and no stray file")
            assert_eq([bool(r.get("fmdv")) for r in listed], [False, True, True, True],
                      "each lineage is marked fmdv")
            assert_eq(refs.reference_roots(cfg), [tb, root], "last in the resolution order")
            row = refs.reference_locations(cfg)[-1]
            assert_eq((row["source"], row["provides"], row["implicit"], row["removable"], row["status"]),
                      ("fmdv", 3, True, False, "ok"), "its row: built in, not removable")

            print("it is never written to config.json, and a stored copy is ignored")
            config.save_config(cfg)
            stored = json.loads(config.CONFIG_PATH.read_text())
            assert_true("fmdv_reference_root" not in stored, "not stored")
            stored["fmdv_reference_root"] = "/somewhere/else"
            config.CONFIG_PATH.write_text(json.dumps(stored))
            assert_eq(config.load_config()["fmdv_reference_root"], str(root), "computed, not read back")

            print("adding or removing it says what it is, and changes nothing")
            saves = []
            res = refs.add_reference_location(cfg, str(root), saves.append)
            assert_eq((res["list"], saves), (None, []), "Add: already there, nothing saved")
            assert_true("always included" in res["note"], "Add says it is always included")
            res = refs.remove_reference_location(cfg, str(root), saves.append)
            assert_eq((res["removed_from"], saves), ([], []), "Remove: nothing removed, nothing saved")
            assert_true("no list" in res["note"], "Remove says it is on no list")
            assert_eq(len(refs.list_references(cfg)), 4, "still listed")

            print("an existing name keeps the folder it resolved to")
            mine = make_ref(tb, "SAT1-I_KM268899")
            sat1 = next(r for r in refs.list_references(cfg) if r["name"] == "SAT1-I_KM268899")
            assert_eq(sat1["path"], str(mine), "the site's own copy still wins")
            assert_true("fmdv" not in sat1, "and it is not the library's")
            assert_eq(sat1["shadowed"], [{"path": str(root / "SAT1-I_KM268899"), "root": str(root), "fmdv": True}],
                      "the library's copy is reported, marked as the library's")
            import shutil
            shutil.rmtree(mine)

            print("the folder also on a list, through a link: one location, not two")
            link = t / "link_to_library"
            link.symlink_to(root)
            site_list(site, [tb, link])
            listed = refs.list_references(cfg)
            assert_eq([r["name"] for r in listed], ["Mycobacterium_AF2122"] + lineages, "each lineage once")
            assert_eq([bool(r.get("fmdv")) for r in listed], [False, True, True, True],
                      "still marked fmdv, though a list provides them")
            assert_true(all(not r["shadowed"] for r in listed), "nothing shadowed")
            assert_eq([(l["source"], l["provides"]) for l in refs.reference_locations(cfg)],
                      [("site", 1), ("site", 3)], "the built-in row is left out")
            site_list(site, [tb])

            print("a lineage's annotation is found without opening the other lineages")
            for n in lineages:
                (root / n / f"{n}.gff").write_text("##gff-version 3\n")
            align = t / "proj" / "step1" / "s1" / "alignment_SAT1-I_KM268899"
            align.mkdir(parents=True)
            fasta = align / "SAT1-I_KM268899.fasta"
            fasta.write_text(">KM268899\nACGT\n")
            assert_eq(refs.find_gff_for_fasta(fasta, cfg), root / "SAT1-I_KM268899" / "SAT1-I_KM268899.gff",
                      "the lineage's own GFF")
            globbed = []
            real_glob = Path.glob

            def spy(self, pattern):
                globbed.append(str(self))
                return real_glob(self, pattern)
            Path.glob = spy
            try:
                refs.find_gff_for_fasta(fasta, cfg)
            finally:
                Path.glob = real_glob
            in_lib = sorted({g for g in globbed if g.startswith(str(root))})
            assert_eq(in_lib, [str(root / "SAT1-I_KM268899")], "only that lineage's folder is searched")

            print("a library the user points at replaces the site's")
            own = make_library(home / "my_fmdv", ["A-ASIA_MN116688"])
            cfg2 = dict(cfg, fmdv_reflib=str(home / "my_fmdv"))
            assert_eq(config.fmdv_reference_root(cfg2), str(own), "the user's own library")
        print("test_fmdv_reference_location: all checks passed")
        return 0
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    sys.exit(main())
