"""When the site list's permissions are wrong, say exactly what an admin runs.

The user, 2026-11-05: "If the permissions are not correct for the file
visibly show what needs to be done to fix." The Reference Editor shows a box
with the problem and the commands; this pins what the backend puts in it.

Pinned here, per case, with the real paths, account, owner, group and mode:
  - the site list's folder is missing: mkdir -p, chgrp to the site's group,
    chmod 2775;
  - the folder is not writable by this account: chgrp + chmod 2775; or, when
    the folder is already shared with a group this account is not in,
    usermod -aG that group, with a note that it counts from the next login;
  - the list cannot be read: chgrp + chmod 664 on the file;
  - the list is narrower than its folder (0600 in a group-shared folder):
    the same, although this account can read it;
  - a group that is one user's personal group is never offered: a visible
    <group> placeholder with a one-line note instead;
  - on NFSv4 (from /proc/mounts) the nfs4_setfacl form is given as well, with
    an inheritable (fd) group ACE on the folder and the domain from
    idmapd.conf (or a placeholder); a 0600 mode there is not called narrower
    when the ACL shares the file, or when the ACL cannot be read;
  - every fix carries the command that re-checks; nothing wrong -> no fix;
  - a listed folder this account cannot open names its owner and mode and
    gets the chmod or ACL that lets the site's users open it; a folder that
    cannot be passed through on the way is named instead;
  - GET /api/references/paths carries all of it, and paths with spaces are
    quoted so the commands paste as they are.

Run directly:  PYTHONPATH=backend <conda>/bin/python backend/app/test_ref_list_fix.py
"""
from __future__ import annotations

import grp
import os
import shlex
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


def group_of(p: Path) -> str:
    return grp.getgrgid(p.stat().st_gid).gr_name


class Site:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.parent = tmp / "site root" / "refs" / "vsnp3"     # a space, to prove the quoting
        self.parent.mkdir(parents=True)
        self.folder = self.parent / "locations"
        self.file = self.folder / "reference_locations.txt"

    def cfg(self):
        return {"vsnp3_path": str(self.tmp / "vsnp3"), "reference_locations_file": str(self.file),
                "reference_locations": []}

    def fix(self):
        return refs.site_list_fix(self.cfg())


def commands(fix):
    return [shlex.split(c) for c in fix["commands"]]


def test_cases(tmp: Path) -> None:
    site = Site(tmp / "cases")
    q = shlex.quote
    print("[the site list's folder is missing]")
    fix = site.fix()
    g = group_of(site.parent)
    check("one case, folder_missing", fix and fix["cases"] == ["folder_missing"])
    check("...the problem names the folder", fix and str(site.folder) in fix["problem"])
    check("...mkdir -p, chgrp to the parent's group, chmod 2775, all with the real path",
          fix and commands(fix) == [["mkdir", "-p", str(site.folder)],
                                    ["chgrp", g, str(site.folder)],
                                    ["chmod", "2775", str(site.folder)]])
    check("...the path with a space is quoted", fix and q(str(site.folder)) in fix["commands"][0]
          and "'" in fix["commands"][0])
    check("...and how to re-check", fix and fix["recheck"] == [f"ls -ld {q(str(site.folder))}"])
    check("not NFSv4 here: no nfs4_setfacl form", fix and fix["nfs4_commands"] == []
          and fix["filesystem"] == "posix")

    print("[everything right: no fix]")
    site.folder.mkdir()
    os.chmod(site.folder, 0o775)
    site.file.write_text("/x\n")
    os.chmod(site.file, 0o664)
    check("no fix", site.fix() is None)
    got = M._reference_paths_payload(site.cfg())
    check("...and the payload's site_fix is null", got["site_fix"] is None)

    print("[the folder is not writable by this account]")
    os.chmod(site.folder, 0o555)
    try:
        fix = site.fix()
        check("folder_not_writable", fix and fix["cases"] == ["folder_not_writable"])
        check("...names the account, owner, group and mode",
              fix and refs._account() in fix["problem"] and "mode 0555" in fix["problem"]
              and f"group {group_of(site.folder)}" in fix["problem"])
        check("...chgrp + chmod 2775 on the folder",
              fix and commands(fix) == [["chgrp", group_of(site.folder), str(site.folder)],
                                        ["chmod", "2775", str(site.folder)]])
        check("...re-check lists folder and file",
              fix and fix["recheck"] == [f"ls -ld {q(str(site.folder))} {q(str(site.file))}"])
        got = M._reference_paths_payload(site.cfg())
        check("the payload carries it, and Add goes to the personal list",
              got["site_fix"] and got["site_fix"]["cases"] == ["folder_not_writable"]
              and got["add_to"] == "personal")
    finally:
        os.chmod(site.folder, 0o775)

    print("[...because this account is not in the group the folder is shared with]")
    os.chmod(site.folder, 0o575)        # owner may not write; the group may
    saved = (refs._my_uid, refs._my_groups, refs._personal_group)
    refs._my_uid = lambda: -12345        # not the owner
    refs._my_groups = lambda: []         # in no group
    refs._personal_group = lambda name, gid: False
    try:
        fix = site.fix()
        fg = group_of(site.folder)
        check("the fix is to join that group",
              fix and commands(fix) == [["usermod", "-aG", fg, refs._account()]])
        check("...the problem says which group and that the account is not in it",
              fix and f"shared with group {fg}" in fix["problem"] and "not a member" in fix["problem"])
        check("...with a note that it counts from the next login",
              fix and any("next login" in n for n in fix["notes"]))
    finally:
        refs._my_uid, refs._my_groups, refs._personal_group = saved
        os.chmod(site.folder, 0o775)

    print("[the list cannot be read]")
    os.chmod(site.file, 0)
    try:
        fix = site.fix()
        check("list_unreadable", fix and "list_unreadable" in fix["cases"])
        check("...chgrp + chmod 664 on the file",
              fix and ["chgrp", group_of(site.folder), str(site.file)] in commands(fix)
              and ["chmod", "664", str(site.file)] in commands(fix))
        check("...naming its mode", fix and "mode 0000" in fix["problem"])
    finally:
        os.chmod(site.file, 0o664)

    print("[the folder cannot be entered]")
    os.chmod(site.folder, 0o644)
    try:
        fix = site.fix()
        check("list_unreadable, fixed on the folder",
              fix and fix["cases"] == ["list_unreadable"] and ["chmod", "2775", str(site.folder)] in commands(fix))
    finally:
        os.chmod(site.folder, 0o775)

    print("[the list is narrower than its folder]")
    os.chmod(site.file, 0o600)
    fix = site.fix()
    check("list_narrower although this account can read it",
          fix and fix["cases"] == ["list_narrower"] and os.access(site.file, os.R_OK))
    check("...says others cannot read or change it, with both modes",
          fix and "mode 0600" in fix["problem"] and "0775" in fix["problem"]
          and "read or change" in fix["problem"])
    check("...chgrp + chmod 664", fix and commands(fix) == [["chgrp", group_of(site.folder), str(site.file)],
                                                           ["chmod", "664", str(site.file)]])
    os.chmod(site.folder, 0o755)
    os.chmod(site.file, 0o644)
    check("a 0644 list in a 0755 folder is not narrower", site.fix() is None)
    os.chmod(site.folder, 0o775)
    os.chmod(site.file, 0o664)

    print("[a personal group is never offered]")
    os.chmod(site.folder, 0o555)
    saved = refs._personal_group
    refs._personal_group = lambda name, gid: True
    try:
        fix = site.fix()
        check("the commands carry a visible <group> placeholder",
              fix and ["chgrp", "<group>", str(site.folder)] in commands(fix))
        check("...with a one-line note saying why",
              fix and any(n.startswith("Replace <group>") and "personal group" in n for n in fix["notes"]))
    finally:
        refs._personal_group = saved
        os.chmod(site.folder, 0o775)


def test_nfs4(tmp: Path) -> None:
    print("[NFSv4: the nfs4_setfacl form too]")
    site = Site(tmp / "nfs")
    site.folder.mkdir()
    os.chmod(site.folder, 0o555)
    mounts = tmp / "mounts"
    mount_point = str((tmp / "nfs").resolve())
    mounts.write_text(f"/dev/disk1 / apfs rw 0 0\nnas:/project {mount_point} nfs4 rw,vers=4.1 0 0\n")
    idmap = tmp / "idmapd.conf"
    idmap.write_text("[General]\n# Domain = wrong\nDomain = ars.usda.gov\n")
    saved = (refs._PROC_MOUNTS, refs._IDMAPD_CONF, refs._nfs4_getfacl)
    refs._PROC_MOUNTS, refs._IDMAPD_CONF = str(mounts), str(idmap)
    try:
        fix = site.fix()
        g = group_of(site.folder)
        check("filesystem is nfs4", fix and fix["filesystem"] == "nfs4")
        check("...an inheritable (fd) group ACE on the folder, with the idmapd domain",
              fix and [shlex.split(c) for c in fix["nfs4_commands"]]
              == [["nfs4_setfacl", "-a", f"A:fdg:{g}@ars.usda.gov:RWX", str(site.folder)]])
        check("...the chmod form is still there", fix and ["chmod", "2775", str(site.folder)] in commands(fix))
        check("...re-check includes nfs4_getfacl", fix and any(r.startswith("nfs4_getfacl") for r in fix["recheck"]))
        idmap.write_text("[General]\n")
        fix = site.fix()
        check("no domain configured: a placeholder and a note",
              fix and "<nfs4-domain>" in fix["nfs4_commands"][0]
              and any("<nfs4-domain>" in n for n in fix["notes"]))
        os.chmod(site.folder, 0o775)
        site.file.write_text("/x\n")
        os.chmod(site.file, 0o600)
        refs._nfs4_getfacl = lambda p: "A::OWNER@:rwatTcCy\nA::GROUP@:rtcy\nA::EVERYONE@:rtcy\n"
        check("0600 with an ACL that shares the file is not narrower", site.fix() is None)
        refs._nfs4_getfacl = lambda p: None
        check("0600 with an ACL that cannot be read raises no false alarm", site.fix() is None)
        refs._nfs4_getfacl = lambda p: "A::OWNER@:rwatTcCy\n"
        fix = site.fix()
        check("0600 with an owner-only ACL is narrower, with the file ACE",
              fix and fix["cases"] == ["list_narrower"]
              and [shlex.split(c) for c in fix["nfs4_commands"]][0][2].startswith("A:g:"))
    finally:
        refs._PROC_MOUNTS, refs._IDMAPD_CONF, refs._nfs4_getfacl = saved
        os.chmod(site.folder, 0o775)


def test_locations(tmp: Path) -> None:
    print("[a listed folder this account cannot open]")
    site = Site(tmp / "loc")
    site.folder.mkdir()
    site.file.write_text("")
    os.chmod(site.file, 0o664)
    os.chmod(site.folder, 0o775)
    locked = (tmp / "loc" / "locked refs").resolve()
    (locked / "Brucella_x").mkdir(parents=True)
    gate = (tmp / "loc" / "gate").resolve()
    (gate / "inner" / "Ref").mkdir(parents=True)
    site.file.write_text(f"{locked}\n{gate / 'inner'}\n")
    site.file.with_name(".reference_locations.txt.seeded").write_text("t\n")
    os.chmod(locked, 0)
    os.chmod(gate, 0)
    try:
        got = M._reference_paths_payload(site.cfg())
        rows = {r["path"]: r for r in got["locations"]}
        fix = rows[str(locked)]["fix"]
        check("the row has a fix naming the owner and mode",
              fix and "mode 0000" in fix["problem"] and f"owner {refs._account()}" in fix["problem"])
        g = group_of(site.folder)
        same = group_of(locked) == g
        want = [["chmod", "-R", "g+rX", str(locked)]] if same else None
        check("...the chmod that lets the site's group open it",
              fix and (commands(fix) == want if same else "g+rX" in fix["commands"][-1]))
        fix = rows[str(gate / "inner")]["fix"]
        check("a folder behind one that cannot be passed through names that one",
              fix and str(gate) in fix["problem"] and "on the way to" in fix["problem"]
              and fix["commands"][-1].endswith(shlex.quote(str(gate))))
        check("the site list itself is fine: no site fix", got["site_fix"] is None)
    finally:
        os.chmod(locked, 0o755)
        os.chmod(gate, 0o755)
    got = M._reference_paths_payload(site.cfg())
    check("once they can be opened, no fixes", all(r["fix"] is None for r in got["locations"]))
    dangling = tmp / "loc" / "dangling"
    dangling.symlink_to(tmp / "loc" / "gone")
    check("a dangling link is not a permissions problem: no fix",
          refs.location_fix(site.cfg(), dangling) is None)


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("SKIP: running as root — permission bits do not refuse root")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ref_list_fix_")).resolve()
    try:
        test_cases(tmp)
        test_nfs4(tmp)
        test_locations(tmp)
    finally:
        for d, _dirs, _files in os.walk(tmp):
            try:
                os.chmod(d, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll site list fix tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
