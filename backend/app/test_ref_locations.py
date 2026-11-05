"""Reference locations: a site list plus a personal list, never vsnp3's install.

vSNP 3 keeps its reference-location list inside its conda install
(dependencies/reference_options_paths.txt), and the Reference Editor used to
edit that file. On shared installs it broke over and over: users cannot write
the software folder, a save made it 0600, conda updates replaced it, and on
Ames (2026-11-05) a listed folder the account could not open made
GET /api/references/paths a bare HTTP 500 while Add said "read-only for this
account". The user chose: the list is DATA, a site list beside the site's
reference data plus each user's own additions, and runs are handed the
reference folder (see test_reference_folder_arg.py).

Pinned here:
  - the effective list is the site list, then the personal list; a location on
    both is listed once, where it first appears;
  - Add goes to the site list when this account can write it, else to the
    personal list, with a note that says so and why; Remove takes it out of
    whichever list holds it, and refuses (403) a site entry this account
    cannot change, changing nothing;
  - the install's list seeds the site list once, minus the upstream author's
    paths that do not exist here, with a marker; a second start adds nothing
    and an entry an admin removed is not re-added; until then the install's
    entries are shown (source "install"); the install's list is never written;
  - a listed folder this account cannot open (no permission, a dangling link,
    a file) is a "cannot open" row with the reason, and neither the location
    list nor the reference list fails: no 500 from any OSError of the lists or
    their folders.

Run directly:  PYTHONPATH=backend <conda>/bin/python backend/app/test_ref_locations.py
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import main as M  # noqa: E402
from app import refs  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def call(fn, *args):
    """(result, None) or (None, (status, detail))."""
    try:
        return fn(*args), None
    except HTTPException as e:
        return None, (e.status_code, str(e.detail))


def make_ref(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.fasta").write_text(">c\nACGT\n", encoding="utf-8")
    return d


class Site:
    """A site tree, a vsnp3 install, and a per-user config held in memory, with
    main.load_config / save_config pointed at them."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        (tmp / "site" / "refs" / "vsnp3").mkdir(parents=True)
        self.site_list = tmp / "site" / "refs" / "vsnp3" / "locations" / "reference_locations.txt"
        self.vsnp3 = tmp / "vsnp3"
        (self.vsnp3 / "dependencies").mkdir(parents=True)
        self.install_list = self.vsnp3 / "dependencies" / "reference_options_paths.txt"
        self.personal: list = []
        M.load_config = self.load
        M.save_config = self.save

    def load(self):
        return {"vsnp3_path": str(self.vsnp3), "projects_root": str(self.tmp / "projects"),
                "reference_locations_file": str(self.site_list),
                "reference_locations": list(self.personal)}

    def save(self, cfg):
        self.personal = list(cfg.get("reference_locations") or [])

    def site_lines(self):
        try:
            return [x for x in self.site_list.read_text().split("\n") if x]
        except FileNotFoundError:
            return None


def add(path):
    return call(M.ref_path_add, M.RefPathRequest(path=str(path)))


def remove(path):
    return call(M.ref_path_remove, M.RefPathRequest(path=str(path)))


def rows(payload):
    return {r["path"]: r for r in payload["locations"]}


def test_seeding(tmp: Path) -> None:
    print("[the install's list seeds the site list once]")
    site = Site(tmp / "seed")
    refs_real = (tmp / "seed" / "lab_refs").resolve()
    make_ref(refs_real, "Mycobacterium_AF2122")
    real_upstream = (tmp / "seed" / "project" / "vsnp_dependencies").resolve()
    real_upstream.mkdir(parents=True)
    missing_upstream = str(tmp / "seed" / "no" / "such" / "vsnp_dependencies")
    orig = refs.UPSTREAM_SHIPPED_PATHS
    refs.UPSTREAM_SHIPPED_PATHS = frozenset({str(real_upstream), missing_upstream})
    try:
        site.install_list.write_text(f"{missing_upstream}\n{refs_real}\n{real_upstream}\n")
        before = (site.install_list.read_bytes(), site.install_list.stat().st_mtime_ns)

        got = M.ref_paths()
        check("before seeding, the install's entries are shown, marked as vSNP's own",
              [(r["path"], r["source"]) for r in got["locations"]]
              == [(str(refs_real), "install"), (str(real_upstream), "install")])
        check("...without the upstream author path that is not on this machine",
              missing_upstream not in got["paths"])
        check("...and the references in them are offered",
              [r["name"] for r in got["references"]] == ["Mycobacterium_AF2122"])

        out = refs.seed_site_list(site.load())
        check("seeding copies the entries into the site list",
              out["seeded"] and site.site_lines() == [str(refs_real), str(real_upstream)])
        check("...leaves out the missing upstream path and says so",
              out["left_out"] == [missing_upstream])
        marker = site.site_list.with_name(".reference_locations.txt.seeded")
        check("...writes a marker beside the site list", marker.is_file()
              and missing_upstream in marker.read_text())
        check("...and never writes the install's list",
              (site.install_list.read_bytes(), site.install_list.stat().st_mtime_ns) == before)
        got = M.ref_paths()
        check("after seeding, the locations come from the site list",
              [(r["path"], r["source"]) for r in got["locations"]]
              == [(str(refs_real), "site"), (str(real_upstream), "site")])

        check("a second start adds nothing", refs.seed_site_list(site.load())["seeded"] is False)
        site.site_list.write_text(f"{refs_real}\n")   # an admin removes one
        site.install_list.write_text(f"{refs_real}\n{real_upstream}\n/new/in/install\n")
        refs.seed_site_list(site.load())
        check("an entry an admin removed is not re-added, nor is a later install entry",
              site.site_lines() == [str(refs_real)])
        os.chmod(site.install_list, 0)
        got = M.ref_paths()
        check("once seeded, the install's list is not read (unreadable, and no note)",
              got["notes"] == [] and got["paths"] == [str(refs_real)])
        os.chmod(site.install_list, 0o644)
    finally:
        refs.UPSTREAM_SHIPPED_PATHS = orig

    print("[a site list created before the first start still gets the install's entries]")
    site = Site(tmp / "seed2")
    a = (tmp / "seed2" / "a").resolve()
    b = (tmp / "seed2" / "b").resolve()
    a.mkdir()
    b.mkdir()
    site.site_list.parent.mkdir()
    site.site_list.write_text(f"{a}\n")            # install_ood.sh's own entry
    site.install_list.write_text(f"{b}\n{a}\n")
    refs.seed_site_list(site.load())
    check("the install's entries are merged after the existing ones, no duplicates",
          site.site_lines() == [str(a), str(b)])

    print("[an account that cannot write the site list does not seed it]")
    site = Site(tmp / "seed3")
    site.install_list.write_text(f"{a}\n")
    os.chmod(site.site_list.parent.parent, 0o555)   # refs/vsnp3: locations/ cannot be made
    try:
        out = refs.seed_site_list(site.load())
        check("not seeded, with the reason", out["seeded"] is False and "cannot write" in (out["reason"] or ""))
        check("...no marker, so the next account that can will do it",
              not site.site_list.with_name(".reference_locations.txt.seeded").exists())
        got = M.ref_paths()
        check("...and the install's entries are still shown",
              [(r["path"], r["source"]) for r in got["locations"]] == [(str(a), "install")])
    finally:
        os.chmod(site.site_list.parent.parent, 0o755)


def test_site_and_personal(tmp: Path) -> None:
    print("[site list writable: Add and Remove change the site list]")
    site = Site(tmp / "sp")
    loc_a = (tmp / "sp" / "loc_a").resolve()
    loc_b = (tmp / "sp" / "loc_b").resolve()
    make_ref(loc_a, "Brucella_suis3")
    make_ref(loc_b, "Brucella_suis3")
    make_ref(loc_b, "mtbc0_v1.1")
    refs.seed_site_list(site.load())                 # empty install list: an empty site list
    check("the seeded site list exists and is empty", site.site_lines() == [])
    got = M.ref_paths()
    check("GET says an Add goes to the site list", got["add_to"] == "site" and got["site"]["writable"])
    res, err = add(loc_a)
    check("Add writes the site list", err is None and site.site_lines() == [str(loc_a)]
          and site.personal == [] and res["note"] is None)
    check("...and the row says so", rows(res)[str(loc_a)]["source"] == "site")

    print("[site list not writable: Add goes to the personal list, and says why]")
    os.chmod(site.site_list.parent, 0o555)
    try:
        got = M.ref_paths()
        check("GET says an Add goes to the personal list, with the reason up front",
              got["add_to"] == "personal" and "cannot write" in (got["site"]["reason"] or ""))
        check("...Add is still offered (writable)", got["writable"] is True)
        res, err = add(loc_b)
        check("Add succeeds into the personal list", err is None and site.personal == [str(loc_b)])
        check("...the site list is untouched", site.site_lines() == [str(loc_a)])
        check("...the note says personal, and why not the site list",
              res["note"] and "personal list" in res["note"] and "cannot write" in res["note"]
              and refs._account() in res["note"])
        check("site entries first, then personal",
              [(r["path"], r["source"]) for r in res["locations"]]
              == [(str(loc_a), "site"), (str(loc_b), "personal")])
        bs = next(r for r in res["references"] if r["name"] == "Brucella_suis3")
        check("the site copy of a duplicated name wins, the personal one is shadowed",
              bs["root"] == str(loc_a) and [s["root"] for s in bs["shadowed"]] == [str(loc_b)])
        check("a site row cannot be removed by this account; a personal one can",
              rows(res)[str(loc_a)]["removable"] is False and rows(res)[str(loc_b)]["removable"] is True)
        res, err = add(loc_a)
        check("adding a location that is already listed changes nothing and says where it is",
              err is None and "already" in (res["note"] or "") and "site list" in res["note"]
              and site.personal == [str(loc_b)])
        res, err = remove(loc_a)
        check("removing a site entry this account cannot change is a 403 with the reason",
              err is not None and err[0] == 403 and "cannot change" in err[1])
        check("...and nothing changed", site.site_lines() == [str(loc_a)] and site.personal == [str(loc_b)])
        res, err = remove(loc_b)
        check("removing a personal entry works", err is None and site.personal == []
              and str(loc_b) not in res["paths"])
        leftovers = [p.name for p in site.site_list.parent.iterdir() if p.name.startswith(".reference_locations.txt.")
                     and not p.name.endswith(".seeded")]
        check("no temp file was left behind", leftovers == [])
    finally:
        os.chmod(site.site_list.parent, 0o755)

    print("[a location on both lists]")
    site.personal = [str(loc_a)]
    got = M.ref_paths()
    check("is listed once, from the site list", [(r["path"], r["source"]) for r in got["locations"]]
          == [(str(loc_a), "site")])
    res, err = remove(loc_a)
    check("Remove takes it out of both", err is None and site.personal == [] and site.site_lines() == [])

    print("[no site list configured (a checkout outside bdtools)]")
    cfg = {"vsnp3_path": str(tmp / "nowhere"), "reference_locations": []}
    saved = []
    out = refs.add_reference_location(cfg, str(loc_a), saved.append)
    check("Add goes to the personal list", out["list"] == "personal" and cfg["reference_locations"] == [str(loc_a)])
    check("...nothing was created for the missing install", not (tmp / "nowhere").exists())


def test_cannot_open(tmp: Path) -> None:
    print("[a listed folder this account cannot open never fails the request]")
    site = Site(tmp / "co")
    good = (tmp / "co" / "good").resolve()
    make_ref(good, "Mycobacterium_AF2122")
    locked = (tmp / "co" / "locked").resolve()
    make_ref(locked, "Brucella_suis3")
    behind = (tmp / "co" / "gate").resolve()
    make_ref(behind / "inner", "Brucella_ovis")
    dangling = (tmp / "co" / "dangling").resolve()
    dangling.symlink_to(tmp / "co" / "gone")
    afile = (tmp / "co" / "afile")
    afile.write_text("x")
    missing = tmp / "co" / "missing"
    site.site_list.parent.mkdir()
    site.site_list.write_text("\n".join(map(str, [locked, behind / "inner", dangling, afile, missing, good])) + "\n")
    site.site_list.with_name(".reference_locations.txt.seeded").write_text("test\n")
    os.chmod(locked, 0)
    os.chmod(behind, 0)
    try:
        got, err = call(M.ref_paths)
        check("GET /api/references/paths answers (no 500)", err is None)
        r = rows(got) if got else {}
        check("a folder without permission is 'cannot open', with owner and mode",
              r.get(str(locked), {}).get("status") == "cannot_open"
              and "may not open it" in (r[str(locked)]["reason"] or "") and "mode 0000" in r[str(locked)]["reason"])
        check("a folder behind one that cannot be entered is 'cannot open'",
              r.get(str(behind / "inner"), {}).get("status") == "cannot_open")
        check("a dangling link is 'cannot open', naming its target",
              r.get(str(dangling), {}).get("status") == "cannot_open"
              and "gone" in (r[str(dangling)]["reason"] or ""))
        check("a file is 'cannot open'", r.get(str(afile), {}).get("status") == "cannot_open")
        check("a missing path is 'missing'", r.get(str(missing), {}).get("status") == "missing")
        check("the readable location still provides its reference",
              r.get(str(good), {}).get("provides") == 1
              and [x["name"] for x in got["references"]] == ["Mycobacterium_AF2122"])
        refs_list, err = call(M.references)
        check("GET /api/references answers too", err is None and [x["name"] for x in refs_list] == ["Mycobacterium_AF2122"])
        check("a run resolves past the unopenable folders",
              M._reference_dir_named(site.load(), "Mycobacterium_AF2122") == good / "Mycobacterium_AF2122")
        check("the GFF lookup does not fail either",
              refs.find_gff_for_fasta(tmp / "co" / "alignment_x" / "x.fasta", site.load()) is None)
        forms, err = call(M._serve_roots, "projX")
        check("the file server's roots are still built", err is None)
    finally:
        os.chmod(locked, 0o755)
        os.chmod(behind, 0o755)

    print("[a site list this account cannot read]")
    site.personal = [str(good)]
    os.chmod(site.site_list, 0)
    try:
        got, err = call(M.ref_paths)
        check("GET answers, it is not a 500 or a 403", err is None)
        check("...with a note naming the file, its owner and mode",
              got and got["notes"] and str(site.site_list) in got["notes"][0] and "mode 0000" in got["notes"][0]
              and "or root can change" in got["notes"][0])
        check("...the personal list still works", got and got["paths"] == [str(good)])
        check("...and Add goes to the personal list", got and got["add_to"] == "personal")
    finally:
        os.chmod(site.site_list, 0o644)


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("SKIP: running as root — permission bits do not refuse root")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ref_locations_")).resolve()
    saved = (M.load_config, M.save_config)
    try:
        test_seeding(tmp)
        test_site_and_personal(tmp)
        test_cannot_open(tmp)
    finally:
        M.load_config, M.save_config = saved
        for d, _dirs, _files in os.walk(tmp):
            try:
                os.chmod(d, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll reference location tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
