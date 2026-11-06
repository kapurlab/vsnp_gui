"""A reference location in a home folder is its owner's, and nobody else's.

Ames, 2026-11-06 (v0.4.134): the one-time seed copied
/home/tstuber/git/gitlab/tb_rd_regions from vSNP's own list into the site
list. Reed's pane showed it as "cannot open", with a fix box telling an admin
to run `setfacl -m 'g:Domain Users:x' /home/tstuber`. The user: "I don't like
that my home directory is being reference. Of course other users shouldn't
see that, it goes without saying and needs to be updated."

Pinned here (two accounts, tstuber and reed, under one folder of homes):
  - Add of a path in a home folder (one's own or another's) goes to the
    personal list, with a note saying why; a shared path still goes to the
    site list;
  - seeding skips home-folder paths: one under the seeding account's home goes
    to its personal list, another account's is left out;
  - an existing site entry under tstuber's home moves to tstuber's personal
    list when tstuber starts vSNP and can write the site list, once, with a
    note; while tstuber cannot write it, tstuber still sees it;
  - reed sees nothing of it: no row, no "cannot open", not counted, no fix
    box, and a reference only there does not resolve for a run;
  - no fix box ever targets a home folder or anything under one (an
    unopenable folder, a parent blocking the way, a site list kept in a home);
    a folder outside the homes still gets its fix;
  - a site list that itself lives in a home folder (a laptop's bdtools site)
    keeps its home paths as site entries.

Run directly:  PYTHONPATH=backend <conda>/bin/python backend/app/test_ref_home_paths.py
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


def make_ref(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.fasta").write_text(">c\nACGT\n", encoding="utf-8")
    return d


class World:
    """/home/{tstuber,reed}, a shared /project with the site list, and one
    personal list per account kept in memory."""

    def __init__(self, tmp: Path, site_parent: Path = None):
        self.tmp = tmp
        self.homes = tmp / "home"
        self.tst = self.homes / "tstuber"
        self.reed = self.homes / "reed"
        self.project = tmp / "project"
        parent = site_parent or (self.project / "site" / "refs" / "vsnp3")
        parent.mkdir(parents=True, exist_ok=True)
        self.site_list = parent / "locations" / "reference_locations.txt"
        self.vsnp3 = tmp / "vsnp3"
        (self.vsnp3 / "dependencies").mkdir(parents=True, exist_ok=True)
        self.install_list = self.vsnp3 / "dependencies" / "reference_options_paths.txt"
        self.shared = make_ref(self.project / "shared_refs", "Mycobacterium_AF2122").parent
        self.tb = make_ref(self.tst / "git" / "gitlab" / "tb_rd_regions", "TB_only").parent
        self.reed_refs = make_ref(self.reed / "refs", "Reed_only").parent
        self.personal = {"tstuber": [], "reed": []}
        self.who = "tstuber"

    def as_(self, who: str):
        self.who = who
        own = self.tst if who == "tstuber" else self.reed
        refs._home_dirs = lambda: ([str(own)], [str(self.homes)])
        refs._moved_note = None
        M.load_config = self.load
        M.save_config = self.save
        return self

    def load(self):
        return {"vsnp3_path": str(self.vsnp3), "reference_locations_file": str(self.site_list),
                "reference_locations": list(self.personal[self.who])}

    def save(self, cfg):
        self.personal[self.who] = list(cfg.get("reference_locations") or [])

    def site_lines(self):
        try:
            return [x for x in self.site_list.read_text().split("\n") if x]
        except FileNotFoundError:
            return None

    def mark_seeded(self):
        self.site_list.parent.mkdir(exist_ok=True)
        self.site_list.with_name(".reference_locations.txt.seeded").write_text("t\n")


def fixes_in(payload):
    out = [payload.get("site_fix")] + [r.get("fix") for r in payload["locations"]]
    return [f for f in out if f]


def no_home_in_fixes(payload, homes: Path) -> bool:
    return all(str(homes) not in c for f in fixes_in(payload)
               for c in f["commands"] + f.get("nfs4_commands", []) + f.get("recheck", []))


def test_add(tmp: Path) -> None:
    print("[Add of a home-folder path goes to the personal list]")
    w = World(tmp / "add").as_("tstuber")
    w.mark_seeded()
    w.site_list.write_text("")
    out = refs.add_reference_location(w.load(), str(w.tb), w.save)
    check("own home -> personal", out["list"] == "personal" and w.personal["tstuber"] == [str(w.tb)])
    check("...with the note", "home folder stay on your personal list" in (out["note"] or ""))
    check("...the site list untouched", w.site_lines() == [])
    out = refs.add_reference_location(w.load(), str(w.reed_refs), w.save)
    check("another account's home -> personal too", out["list"] == "personal")
    out = refs.add_reference_location(w.load(), str(w.shared), w.save)
    check("a shared path still goes to the site list", out["list"] == "site" and w.site_lines() == [str(w.shared)])


def test_seed(tmp: Path) -> None:
    print("[seeding skips home folders; the seeder's own go to its personal list]")
    w = World(tmp / "seed").as_("tstuber")
    w.install_list.write_text(f"{w.shared}\n{w.tb}\n{w.reed_refs}\n")
    out = refs.seed_site_list(w.load(), w.save)
    check("the site list gets only the shared path", out["seeded"] and w.site_lines() == [str(w.shared)])
    check("tstuber's home path goes to tstuber's personal list",
          out["to_personal"] == [str(w.tb)] and w.personal["tstuber"] == [str(w.tb)])
    check("reed's home path is left out", str(w.reed_refs) in out["left_out"] and w.personal["reed"] == [])
    marker = w.site_list.with_name(".reference_locations.txt.seeded").read_text()
    check("the marker says both", str(w.tb) in marker and str(w.reed_refs) in marker)

    print("[before seeding, another account never sees a home path from vSNP's own list]")
    w2 = World(tmp / "seed2").as_("reed")
    w2.install_list.write_text(f"{w2.shared}\n{w2.tb}\n")
    os.chmod(w2.site_list.parent.parent, 0o555)     # reed cannot create the site list
    try:
        got = M._reference_paths_payload(w2.load())
        check("reed sees the shared entry only", got["paths"] == [str(w2.shared)])
    finally:
        os.chmod(w2.site_list.parent.parent, 0o755)


def test_existing_entry(tmp: Path) -> None:
    print("[an existing site entry under tstuber's home: reed sees nothing of it]")
    w = World(tmp / "ex")
    w.mark_seeded()
    w.site_list.write_text(f"{w.shared}\n{w.tb}\n")
    w.as_("reed")
    os.chmod(w.tst, 0)                               # reed cannot open tstuber's home
    try:
        got = M._reference_paths_payload(w.load())
        check("not in the paths", got["paths"] == [str(w.shared)])
        check("no row for it, so no 'cannot open'", [r["path"] for r in got["locations"]] == [str(w.shared)]
              and all(r["status"] == "ok" for r in got["locations"]))
        check("not counted: only the shared reference is offered",
              [r["name"] for r in got["references"]] == ["Mycobacterium_AF2122"])
        check("no fix box at all", fixes_in(got) == [])
        check("a run cannot resolve a reference that is only there",
              M._reference_dir_named(w.load(), "TB_only") is None)
        check("the file server does not serve from it",
              str(w.tb) not in [str(r) for r in refs.reference_roots(w.load())])
        check("and reed cannot move it", refs.move_own_home_entries(w.load(), w.save) == []
              and w.site_lines() == [str(w.shared), str(w.tb)])
    finally:
        os.chmod(w.tst, 0o755)

    print("[tstuber starts vSNP: the entry moves to tstuber's personal list, once]")
    w.as_("tstuber")
    os.chmod(w.site_list.parent, 0o555)
    try:
        check("while tstuber cannot write the site list, nothing moves",
              refs.move_own_home_entries(w.load(), w.save) == [])
        got = M._reference_paths_payload(w.load())
        check("...and tstuber still sees it, on the site list",
              {r["path"]: r["source"] for r in got["locations"]}.get(str(w.tb)) == "site")
    finally:
        os.chmod(w.site_list.parent, 0o755)
    moved = refs.move_own_home_entries(w.load(), w.save)
    check("moved", moved == [str(w.tb)] and w.site_lines() == [str(w.shared)]
          and w.personal["tstuber"] == [str(w.tb)])
    got = M._reference_paths_payload(w.load())
    check("the pane says so", got["moved_note"] and str(w.tb) in got["moved_note"]
          and "personal list" in got["moved_note"])
    check("...and shows it as personal", {r["path"]: r["source"] for r in got["locations"]}.get(str(w.tb)) == "personal")
    check("a second start moves nothing", refs.move_own_home_entries(w.load(), w.save) == [])


def test_fix_suggestions(tmp: Path) -> None:
    print("[no fix box ever targets a home folder]")
    w = World(tmp / "fx").as_("reed")
    w.mark_seeded()
    locked = w.reed / "locked_refs"
    make_ref(locked, "R")
    outside_gate = w.project / "gate"
    make_ref(outside_gate / "inner", "G")
    w.site_list.write_text("")
    w.personal["reed"] = [str(locked), str(w.tst / "notes"), str(outside_gate / "inner")]
    os.chmod(locked, 0)
    os.chmod(w.tst, 0)
    os.chmod(outside_gate, 0)
    try:
        got = M._reference_paths_payload(w.load())
        rows = {r["path"]: r for r in got["locations"]}
        check("reed's own unopenable home folder: a row, no fix",
              rows[str(locked)]["status"] == "cannot_open" and rows[str(locked)]["fix"] is None)
        check("a path behind tstuber's home (blocked there): no fix", rows[str(w.tst / "notes")]["fix"] is None)
        check("a folder outside the homes still gets its fix",
              rows[str(outside_gate / "inner")]["fix"] is not None)
        check("no command in the payload names a home path", no_home_in_fixes(got, w.homes))
        check("location_fix refuses the homes folder itself", refs.location_fix(w.load(), w.homes) is None)
    finally:
        for d in (locked, w.tst, outside_gate):
            os.chmod(d, 0o755)


def test_site_in_home(tmp: Path) -> None:
    print("[a site list kept in a home folder (a laptop's bdtools site)]")
    root = tmp / "laptop"
    w = World(root, site_parent=root / "home" / "tstuber" / ".local" / "share" / "site" / "refs" / "vsnp3")
    w.as_("tstuber")
    w.mark_seeded()
    w.site_list.write_text(f"{w.tb}\n")
    got = M._reference_paths_payload(w.load())
    check("its home paths stay site entries", [(r["path"], r["source"]) for r in got["locations"]]
          == [(str(w.tb), "site")])
    out = refs.add_reference_location(w.load(), str(w.tst / "git"), w.save)
    check("Add of a home path goes to it", out["list"] == "site")
    check("nothing is moved off it", refs.move_own_home_entries(w.load(), w.save) == [])
    shutil.rmtree(w.site_list.parent)
    check("no fix box suggests changing anything in that home, even with its folder missing",
          refs.site_list_fix(w.load()) is None)


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("SKIP: running as root — permission bits do not refuse root")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ref_home_paths_")).resolve()
    saved = (refs._home_dirs, M.load_config, M.save_config)
    try:
        test_add(tmp)
        test_seed(tmp)
        test_existing_entry(tmp)
        test_fix_suggestions(tmp)
        test_site_in_home(tmp)
    finally:
        refs._home_dirs, M.load_config, M.save_config = saved
        refs._moved_note = None
        for d, _dirs, _files in os.walk(tmp):
            try:
                os.chmod(d, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll home-folder tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
