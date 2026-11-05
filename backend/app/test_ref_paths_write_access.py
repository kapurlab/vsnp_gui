"""Changing the site's reference location list: refusals and permissions.

History. The list used to live inside the shared vsnp3 install
(dependencies/reference_options_paths.txt). On USDA SciComp, 2026-11-04, Add
under Reference Locations produced only "Could not add reference location
(HTTP 500)": ordinary accounts may not write the install, and the list itself
had become -rw------- because every save put a fresh 0600 temp file in place.
The user decided that a save keeps the list's existing permissions. Then the
list moved out of the install into the site's data area (see refs.py and
test_ref_locations.py), with each user's own additions beside it.

Pinned here, for the site list:
  - a save keeps its permissions: 0664 stays 0664 and 0600 stays 0600 (never
    widened); the old list's stats and ACLs reach the temp file through
    copystat, the group is asked for (EPERM tolerated), the edit gets a fresh
    mtime; a filesystem that refuses permission changes still saves;
  - a first list is readable by all, and group-writable when its folder is
    (the admins' setgid 2775 folder), less the umask (so 0664 under umask 002,
    0644 under 022);
  - a write the filesystem refuses although the access check said yes (an ACL
    deny) still adds the location, to the personal list, saying why; another
    OSError is an explained 500 that leaves the list as it was;
  - Download New Reference into a folder that is not yet a location adds it to
    the personal list when the site list cannot be written, instead of
    refusing, and says so;
  - install_ood.sh's helper, run under /bin/bash, keeps permissions the same
    way and gives a first list the same mode.

Run directly:  PYTHONPATH=backend <conda>/bin/python backend/app/test_ref_paths_write_access.py
"""
from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import main as M  # noqa: E402

FAILED = 0
TMP_PREFIX = ".reference_locations.txt."


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


def add(path):
    return call(M.ref_path_add, M.RefPathRequest(path=str(path)))


def remove(path):
    return call(M.ref_path_remove, M.RefPathRequest(path=str(path)))


def download(output_dir, name):
    return call(M.ref_download, M.RefDownloadRequest(
        accession="NC_000962.3", output_dir=str(output_dir), display_name=name))


def mode_of(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def installer_keep_perms() -> str:
    """deploy/install_ood.sh's _list_keep_perms, as a standalone bash snippet."""
    src = (Path(__file__).resolve().parents[2] / "deploy" / "install_ood.sh").read_text()
    start = src.index("_list_keep_perms() {")
    return src[start:src.index("\n}\n", start) + 3]


def run_installer_keep_perms(old: Path, new: Path, umask: str = "022") -> int:
    script = f'umask {umask}\n{installer_keep_perms()}\n_list_keep_perms "$1" "$2"\n'
    return subprocess.run(["/bin/bash", "-c", script, "bash", str(old), str(new)],
                          capture_output=True, text=True).returncode


class Site:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.folder = tmp / "site" / "refs" / "vsnp3" / "locations"
        self.folder.mkdir(parents=True)
        self.site_list = self.folder / "reference_locations.txt"
        self.vsnp3 = tmp / "vsnp3"
        (self.vsnp3 / "dependencies").mkdir(parents=True)
        (self.folder / ".reference_locations.txt.seeded").write_text("test\n")
        self.personal: list = []
        M.load_config = self.load
        M.save_config = self.save

    def load(self):
        return {"vsnp3_path": str(self.vsnp3), "projects_root": str(self.tmp / "projects"),
                "reference_locations_file": str(self.site_list),
                "reference_locations": list(self.personal)}

    def save(self, cfg):
        self.personal = list(cfg.get("reference_locations") or [])


def check_saves_keep_permissions(site: Site, root_a: Path, root_b: Path) -> None:
    lst = site.site_list
    lst.write_text(f"{root_a}\n", encoding="utf-8")
    old_umask = os.umask(0o022)
    try:
        print("[a save keeps the site list's permissions]")
        os.chmod(lst, 0o664)
        add(root_b)
        check("a shared 0664 list stays 0664 after Add", mode_of(lst) == 0o664
              and str(root_b) in lst.read_text())
        remove(root_b)
        check("...and after Remove", mode_of(lst) == 0o664 and str(root_b) not in lst.read_text())
        os.chmod(lst, 0o600)
        res, err = add(root_b)
        check("an owner-only 0600 list stays 0600, never widened",
              err is None and mode_of(lst) == 0o600)
        remove(root_b)

        print("[...by copystat from the old list onto the temp file, before the rename]")
        os.chmod(lst, 0o640)
        os.utime(lst, (1_000_000_000, 1_000_000_000))
        old_gid = lst.stat().st_gid
        calls, xattr_calls, chowns = [], [], []
        real_copystat, real_copyxattr, real_chown = shutil.copystat, shutil._copyxattr, os.chown

        def spy_copystat(src, dst, **kw):
            calls.append((src, dst, Path(dst).exists()))
            return real_copystat(src, dst, **kw)

        # macOS has no os.getxattr, so shutil's xattr step is a no-op here; record
        # what copystat hands it instead (on Linux it carries the ACLs).
        def fake_copyxattr(src, dst, *, follow_symlinks=True):
            xattr_calls.append((src, dst))

        def spy_chown(path, uid, gid, **kw):
            chowns.append((path, uid, gid))
            raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), path)

        shutil.copystat, shutil._copyxattr, os.chown = spy_copystat, fake_copyxattr, spy_chown
        try:
            res, err = add(root_b)
        finally:
            shutil.copystat, shutil._copyxattr, os.chown = real_copystat, real_copyxattr, real_chown
        check("copystat ran once, from the old list to a temp file beside it",
              len(calls) == 1 and calls[0][0] == str(lst)
              and Path(calls[0][1]).parent == site.folder
              and Path(calls[0][1]).name.startswith(TMP_PREFIX) and calls[0][2])
        check("...so the extended attributes (ACLs) are copied from the old list too",
              xattr_calls == [(str(lst), calls[0][1])] if calls else False)
        check("...the group is asked for, and a refusal (EPERM) does not fail the save",
              err is None and chowns and all(c[2] == old_gid for c in chowns))
        check("...the mode came across (0640)", mode_of(lst) == 0o640)
        check("...and the edit carries a fresh mtime, not the old one",
              lst.stat().st_mtime > 1_000_000_000 + 86400)
        check("...and the new path is in the list", str(root_b) in lst.read_text())
        remove(root_b)

        print("[a filesystem that refuses permission changes still saves]")
        os.chmod(lst, 0o664)
        real_copystat, real_chmod = shutil.copystat, os.chmod

        def refuse_copystat(src, dst, **kw):
            raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), dst)

        def refuse_chmod(path, mode, *a, **kw):
            if Path(str(path)).name.startswith(TMP_PREFIX):
                raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(path))
            return real_chmod(path, mode, *a, **kw)

        shutil.copystat = refuse_copystat
        try:
            res, err = add(root_b)
        finally:
            shutil.copystat = real_copystat
        check("copystat refused (an SMB mount): the save goes through, mode kept",
              err is None and str(root_b) in lst.read_text() and mode_of(lst) == 0o664)
        remove(root_b)
        shutil.copystat, os.chmod = refuse_copystat, refuse_chmod
        try:
            res, err = add(root_b)
        finally:
            shutil.copystat, os.chmod = real_copystat, real_chmod
        check("...and with chmod refused too, it is a plain save, not an error",
              err is None and str(root_b) in lst.read_text())
        remove(root_b)

        print("[a first list: readable by all, group-writable when its folder is]")
        for folder_mode, umask, want in ((0o2775, 0o002, 0o664), (0o2775, 0o022, 0o644),
                                         (0o2775, 0o027, 0o640), (0o755, 0o002, 0o644),
                                         (0o755, 0o027, 0o640)):
            lst.unlink()
            os.chmod(site.folder, folder_mode)
            os.umask(umask)
            add(root_b)
            check(f"folder {folder_mode:04o}, umask {umask:03o} -> list {want:04o}",
                  mode_of(lst) == want)
            remove(root_b)
        os.chmod(site.folder, 0o755)
    finally:
        os.umask(old_umask)


def check_refused_writes(site: Site, root_a: Path, root_b: Path) -> None:
    lst = site.site_list
    lst.write_text(f"{root_a}\n", encoding="utf-8")
    os.chmod(lst, 0o664)
    before = lst.read_bytes()
    print("[the access check said yes and the save was refused anyway (an ACL deny)]")
    real_replace = os.replace

    def refuse_replace(src, dst, *a, **kw):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(dst))

    os.replace = refuse_replace
    try:
        res, err = add(root_b)
    finally:
        os.replace = real_replace
    check("Add still succeeds, into the personal list", err is None and site.personal == [str(root_b)])
    check("...the note says the site list refused it, and what the OS said",
          res and "was refused saving the site list" in (res["note"] or "")
          and "Permission denied" in res["note"])
    check("...the site list is unchanged, no temp file left", lst.read_bytes() == before
          and not [p for p in site.folder.iterdir()
                   if p.name.startswith(TMP_PREFIX) and not p.name.endswith(".seeded")])
    site.personal = []

    print("[a save that fails for another reason (disk full)]")

    def full_replace(src, dst, *a, **kw):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(dst))

    os.replace = full_replace
    try:
        res, err = add(root_b)
    finally:
        os.replace = real_replace
    check("is a 500 that says what and where, not a bare one",
          err is not None and err[0] == 500 and str(lst) in err[1] and "No space left" in err[1])
    check("...the list is unchanged and nothing went to the personal list",
          lst.read_bytes() == before and site.personal == [])


def check_download(site: Site, root_a: Path) -> None:
    print("[Download New Reference when this account cannot write the site list]")
    started = []
    saved = (M.job_manager.start_job, M.wrap_cmd, M.build_env)
    M.job_manager.start_job = lambda **kw: started.append(kw) or "job-test"
    M.wrap_cmd = lambda cfg, cmd: cmd
    M.build_env = lambda cfg: {}
    site.site_list.write_text(f"{root_a}\n", encoding="utf-8")
    new_out = (site.tmp / "new_out").resolve()
    os.chmod(site.folder, 0o555)
    try:
        res, err = download(new_out, "NewRefA")
        check("a download into a new folder is not refused", err is None and started)
        check("...the folder is added to the personal list", site.personal == [str(new_out)])
        check("...and the response says so", bool(res) and "personal list" in (res.get("note") or ""))
        started.clear()
        res, err = download(root_a, "NewRefB")
        check("a download into a site location needs no change and says nothing",
              err is None and started and res.get("note") is None)
    finally:
        os.chmod(site.folder, 0o755)
        M.job_manager.start_job, M.wrap_cmd, M.build_env = saved


def check_installer_helper(tmp: Path) -> None:
    print("[install_ood.sh's _list_keep_perms does the same (/bin/bash)]")
    d = tmp / "inst"
    d.mkdir()
    old, new = d / "old.txt", d / "new.txt"
    old.write_text("a\n")
    for want in (0o664, 0o600, 0o640):
        os.chmod(old, want)
        new.write_text("a\nb\n")
        os.chmod(new, 0o600)
        rc = run_installer_keep_perms(old, new)
        check(f"old {want:04o} -> new {mode_of(new):04o}, contents untouched",
              rc == 0 and mode_of(new) == want and new.read_text() == "a\nb\n")
    old.unlink()
    for folder_mode, umask, want in ((0o2775, "002", 0o664), (0o2775, "022", 0o644),
                                     (0o2775, "027", 0o640), (0o755, "002", 0o644),
                                     (0o755, "027", 0o640)):
        os.chmod(d, folder_mode)
        os.chmod(new, 0o600)
        rc = run_installer_keep_perms(old, new, umask)
        check(f"no old list, folder {folder_mode:04o}, umask {umask} -> {want:04o}",
              rc == 0 and mode_of(new) == want)
    os.chmod(d, 0o755)


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("SKIP: running as root — permission bits do not refuse root")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ref_paths_access_")).resolve()
    saved = (M.load_config, M.save_config)
    try:
        root_a = tmp / "refs_a"
        root_b = tmp / "refs_b"
        for r in (root_a, root_b):
            (r / "Mycobacterium_AF2122").mkdir(parents=True)
            (r / "Mycobacterium_AF2122" / "ref.fasta").write_text(">x\nACGT\n")
        site = Site(tmp)
        check_saves_keep_permissions(site, root_a, root_b)
        check_refused_writes(site, root_a, root_b)
        check_download(site, root_a)
        check_installer_helper(tmp)
    finally:
        M.load_config, M.save_config = saved
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll reference list access tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
