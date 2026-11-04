"""Reference Locations when this account may not change the list.

Reported from the USDA SciComp OnDemand deployment, 2026-11-04: clicking Add
under Reference Locations produced only "Could not add reference location
(HTTP 500)". The list (dependencies/reference_options_paths.txt) lives inside
the shared vsnp3 install, and a save is a temp file in dependencies/ renamed
over it, so it needs write access to that FOLDER. An account the site has not
given that access (or a compute node that mounts /software read-only) got a
PermissionError / EROFS that nothing caught.

Pinned here: the refusal is a 403 that names the file, the folder, the account
and the folder's owner, and says the list is shared so the site's admins decide
who changes it; GET says `writable: false` with that reason before anyone
types a path; nothing is created, widened or rewritten on the way (the file
keeps its content and mode, no temp file is left behind, no per-user list
appears); Download New Reference refuses a folder it would have to add before
it creates anything; and a folder already in the list still downloads.

The same report's traceback was a READ: Add reads the list before rewriting it,
and the list itself was unreadable to the account. GET and the reference
dropdowns failed the same way, and the panel then said "No reference locations
configured." Pinned too: every reader raises RegistryUnreadable, which the app
answers with a 403 naming the file (or the folder that cannot be entered), its
owner and mode, and that only the owner or root can change it. That is told
apart from an unwritable folder, and project files are still served.

The list there was -rw-------: both writers (this backend's save and
install_ood.sh step 4) put a fresh 0600 temp file in place. The user decided
that a save keeps the list's existing permissions, so other users keep their
access after an edit. Pinned last: 0664 stays 0664 and 0600 stays 0600
(never widened). The old list's stats and ACLs reach the temp file through
copystat, the group is asked for, and the edit gets a fresh mtime. A first list
gets 0644 less the umask. The installer's helper, run under /bin/bash, does the same.

Run directly:  <conda>/bin/python backend/app/test_ref_paths_write_access.py
"""
from __future__ import annotations

import asyncio
import errno
import json
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


def add(path):
    return call(M.ref_path_add, M.RefPathRequest(path=str(path)))


def remove(path):
    return call(M.ref_path_remove, M.RefPathRequest(path=str(path)))


def download(output_dir):
    return call(M.ref_download, M.RefDownloadRequest(
        accession="NC_000962.3", output_dir=str(output_dir)))


def says_who_and_where(detail: str, deps: Path) -> bool:
    return (str(deps) in detail
            and "reference_options_paths.txt" in detail
            and refs._account() in detail
            and f"owner: {refs._owner(deps)}" in detail
            and "shared by every user of this vSNP install" in detail
            and "site admins" in detail)


def says_unreadable(detail: str, blocked: Path, mode: str) -> bool:
    return ("cannot read" in detail
            and str(blocked) in detail
            and f"owner: {refs._owner(blocked)}" in detail
            and f"mode {mode}" in detail
            and refs._account() in detail
            and "or root can change" in detail
            and "cannot write" not in detail)


def mode_of(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def installer_keep_perms() -> str:
    """deploy/install_ood.sh's _rop_keep_perms, as a standalone bash snippet."""
    src = (Path(__file__).resolve().parents[2] / "deploy" / "install_ood.sh").read_text()
    start = src.index("_rop_keep_perms() {")
    return src[start:src.index("\n}\n", start) + 3]


def run_installer_keep_perms(old: Path, new: Path, umask: str = "022") -> int:
    script = f'umask {umask}\n{installer_keep_perms()}\n_rop_keep_perms "$1" "$2"\n'
    return subprocess.run(["/bin/bash", "-c", script, "bash", str(old), str(new)],
                          capture_output=True, text=True).returncode


def check_saves_keep_permissions(tmp: Path, vsnp3: Path, deps: Path, rop: Path,
                                 root_a: Path, root_b: Path) -> None:
    rop.write_text(f"{root_a}\n", encoding="utf-8")
    old_umask = os.umask(0o022)
    try:
        print("[a save keeps the list's permissions (the user's decision, 2026-11-04)]")
        os.chmod(rop, 0o664)
        add(root_b)
        check("a shared 0664 list stays 0664 after Add", mode_of(rop) == 0o664)
        remove(root_b)
        check("...and after Remove", mode_of(rop) == 0o664)
        os.chmod(rop, 0o600)
        res, err = add(root_b)
        check("an owner-only 0600 list stays 0600, never widened",
              err is None and mode_of(rop) == 0o600)
        remove(root_b)

        print("[...by copystat from the old list onto the temp file, before the rename]")
        os.chmod(rop, 0o640)
        os.utime(rop, (1_000_000_000, 1_000_000_000))
        old_gid = rop.stat().st_gid
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
        check("copystat ran once, from the old list to a temp file in dependencies/",
              len(calls) == 1 and calls[0][0] == str(rop)
              and Path(calls[0][1]).parent == deps
              and Path(calls[0][1]).name.startswith(".rop-") and calls[0][2])
        check("...so the extended attributes (ACLs) are copied from the old list too",
              xattr_calls == [(str(rop), calls[0][1])] if calls else False)
        check("...the group is asked for, and a refusal (EPERM) does not fail the save",
              err is None and chowns and all(c[2] == old_gid for c in chowns))
        check("...the mode came across (0640)", mode_of(rop) == 0o640)
        check("...and the edit carries a fresh mtime, not the old one",
              rop.stat().st_mtime > 1_000_000_000 + 86400)
        check("...and the new path is in the list", str(root_b) in rop.read_text())
        remove(root_b)

        print("[a first list gets 0644 less the umask]")
        rop.unlink()
        add(root_b)
        check("new list under umask 022 is 0644", mode_of(rop) == 0o644)
        rop.unlink()
        os.umask(0o027)
        add(root_b)
        check("new list under umask 027 is 0640", mode_of(rop) == 0o640)
    finally:
        os.umask(old_umask)

    print("[install_ood.sh step 4 keeps the list's permissions too (/bin/bash)]")
    old, new = tmp / "inst_old.txt", tmp / "inst_new.txt"
    old.write_text("a\n")
    for want in (0o664, 0o600, 0o640):
        os.chmod(old, want)
        new.write_text("a\nb\n")
        os.chmod(new, 0o600)
        rc = run_installer_keep_perms(old, new)
        check(f"old {want:04o} -> new {mode_of(new):04o}, contents untouched",
              rc == 0 and mode_of(new) == want and new.read_text() == "a\nb\n")
    old.unlink()
    for umask, want in (("022", 0o644), ("027", 0o640)):
        os.chmod(new, 0o600)
        rc = run_installer_keep_perms(old, new, umask)
        check(f"no old list, umask {umask} -> {want:04o}", rc == 0 and mode_of(new) == want)


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("SKIP: running as root — permission bits do not refuse root")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="ref_paths_access_"))
    vsnp3 = tmp / "vsnp3"
    deps = vsnp3 / "dependencies"
    deps.mkdir(parents=True)
    rop = deps / "reference_options_paths.txt"
    root_a = (tmp / "refs_a").resolve()
    root_b = (tmp / "refs_b").resolve()
    for r in (root_a, root_b):
        (r / "Mycobacterium_AF2122").mkdir(parents=True)
        (r / "Mycobacterium_AF2122" / "ref.fasta").write_text(">x\nACGT\n")
    rop.write_text(f"{root_a}\n", encoding="utf-8")
    os.chmod(rop, 0o644)

    M.load_config = lambda: {"vsnp3_path": str(vsnp3)}
    started = []
    M.job_manager.start_job = lambda **kw: started.append(kw) or "job-test"
    M.wrap_cmd = lambda cfg, cmd: cmd
    M.build_env = lambda cfg: {}

    print("[a writable install: GET says so, Add works]")
    got = M.ref_paths()
    check("writable is true", got["writable"] is True)
    check("no read-only reason", got["read_only_reason"] is None)
    res, err = add(root_b)
    check("Add succeeds", err is None and str(root_b) in res["paths"])
    check("the response carries writable too", res is not None and res["writable"] is True)
    res, err = remove(root_b)
    check("Remove succeeds", err is None and str(root_b) not in res["paths"])
    check("...and the saves kept the list's 0644", mode_of(rop) == 0o644)

    print("[dependencies/ not writable by this account (chmod 0555)]")
    before = rop.read_bytes()
    mode_before = stat.S_IMODE(rop.stat().st_mode)
    os.chmod(deps, 0o555)
    try:
        got = M.ref_paths()
        check("GET says writable: false", got["writable"] is False)
        reason = got["read_only_reason"] or ""
        check("...with a reason naming file, folder, account, owner and who decides",
              says_who_and_where(reason, deps))
        check("GET still lists the locations", got["paths"] == [str(root_a)])

        res, err = add(root_b)
        check("Add is refused with 403, not a bare 500", err is not None and err[0] == 403)
        check("...naming file, folder, account, owner and who decides",
              err is not None and says_who_and_where(err[1], deps))
        check("...and the location it was adding", err is not None and str(root_b) in err[1])

        res, err = remove(root_a)
        check("Remove is refused with 403", err is not None and err[0] == 403)
        check("...naming file, folder, account, owner and who decides",
              err is not None and says_who_and_where(err[1], deps))

        res, err = add(root_a)
        check("re-adding a listed location writes nothing, so it still succeeds",
              err is None and res["paths"] == [str(root_a)])

        check("the list is unchanged", rop.read_bytes() == before)
        check("its mode is unchanged", stat.S_IMODE(rop.stat().st_mode) == mode_before)
        check("no temp file left in dependencies/",
              not [p for p in deps.iterdir() if p.name.startswith(".rop-")])
        check("no per-user list was created anywhere under the install",
              sorted(p.name for p in deps.iterdir()) == ["reference_options_paths.txt"])

        print("[Download New Reference into a folder that is not a location]")
        new_out = tmp / "new_refs"
        res, err = download(new_out)
        check("refused with 403 before anything is created",
              err is not None and err[0] == 403 and not new_out.exists())
        check("...saying why and what to choose instead",
              err is not None and says_who_and_where(err[1], deps)
              and "already a reference location" in err[1])
        check("no job was started", not started)

        print("[Download New Reference into a folder already in the list]")
        res, err = download(root_a)
        check("goes ahead: nothing about the list has to change",
              err is None and res["job_id"] == "job-test")
        check("the list is still unchanged", rop.read_bytes() == before)
    finally:
        os.chmod(deps, 0o755)

    print("[no dependencies/ yet, and the install folder is not writable]")
    bare = tmp / "vsnp3_bare"
    bare.mkdir()
    os.chmod(bare, 0o555)
    M.load_config = lambda: {"vsnp3_path": str(bare)}
    try:
        got = M.ref_paths()
        check("GET says writable: false", got["writable"] is False)
        check("...naming the install folder and what a change would create",
              str(bare) in (got["read_only_reason"] or "")
              and "would create dependencies/reference_options_paths.txt"
              in (got["read_only_reason"] or ""))
        res, err = add(root_b)
        check("Add is refused with 403", err is not None and err[0] == 403)
        check("nothing was created", not (bare / "dependencies").exists())
    finally:
        os.chmod(bare, 0o755)

    M.load_config = lambda: {"vsnp3_path": str(vsnp3)}

    print("[a read-only mount (compute nodes often mount /software that way)]")
    real_ro = refs._mounted_read_only
    refs._mounted_read_only = lambda p: True
    try:
        got = M.ref_paths()
        check("GET says writable: false", got["writable"] is False)
        check("...and says the file system is read-only on this host",
              "mounted read-only on" in (got["read_only_reason"] or "")
              and str(deps) in (got["read_only_reason"] or ""))
    finally:
        refs._mounted_read_only = real_ro

    real_add = M.add_reference_path

    def raising(code):
        def _add(vsnp3_path, new_path):
            raise OSError(code, os.strerror(code), str(rop))
        return _add

    print("[EROFS from the write itself, though os.access said yes]")
    M.add_reference_path = raising(errno.EROFS)
    try:
        res, err = add(root_b)
        check("403, not a bare 500", err is not None and err[0] == 403)
        check("...naming file, folder, account, owner and who decides",
              err is not None and says_who_and_where(err[1], deps))
        check("...and the OS error", err is not None and os.strerror(errno.EROFS) in err[1])
    finally:
        M.add_reference_path = real_add

    print("[any other OSError (disk full) is explained too, as a 500]")
    M.add_reference_path = raising(errno.ENOSPC)
    try:
        res, err = add(root_b)
        check("500 with a detail", err is not None and err[0] == 500)
        check("...naming the file, folder and the OS error",
              err is not None and str(rop) in err[1] and str(deps) in err[1]
              and os.strerror(errno.ENOSPC) in err[1])
    finally:
        M.add_reference_path = real_add

    print("[no vsnp3 install at all keeps its own 400]")
    M.load_config = lambda: {"vsnp3_path": str(tmp / "nowhere" / "vsnp3")}
    res, err = add(root_b)
    check("400 that says to fix vsnp3_path", err is not None and err[0] == 400
          and "vsnp3_path" in err[1])
    got = M.ref_paths()
    check("GET says writable: false with the same advice",
          got["writable"] is False and "vsnp3_path" in (got["read_only_reason"] or ""))

    M.load_config = lambda: {"vsnp3_path": str(vsnp3),
                             "projects_root": str(tmp / "projects")}
    rop.write_text(f"{root_a}\n", encoding="utf-8")
    os.chmod(rop, 0o644)

    print("[the list itself cannot be read (mode 0000): the SciComp traceback]")
    os.chmod(rop, 0o000)
    try:
        access = refs.registry_write_access(vsnp3)
        check("registry_write_access says readable: false, writable: false",
              access["readable"] is False and access["writable"] is False)
        check("...with the unreadable reason, not the unwritable one",
              says_unreadable(access["reason"], rop, "0000"))

        for label, fn in (("GET /api/references/paths", M.ref_paths),
                          ("GET /api/references", M.references)):
            try:
                fn()
                check(f"{label} refuses", False)
            except refs.RegistryUnreadable as exc:
                check(f"{label} raises RegistryUnreadable naming file, owner, mode",
                      says_unreadable(exc.reason, rop, "0000"))

        check("the app routes RegistryUnreadable to its handler",
              M.app.exception_handlers.get(refs.RegistryUnreadable) is M._registry_unreadable)
        resp = asyncio.run(M._registry_unreadable(None, refs.RegistryUnreadable(
            rop, PermissionError(errno.EACCES, os.strerror(errno.EACCES)))))
        body = json.loads(resp.body)
        check("...which answers 403, not a bare 500", resp.status_code == 403)
        check("...with the reason and a marker the UI can tell apart",
              body.get("registry") == "unreadable"
              and says_unreadable(body.get("detail", ""), rop, "0000"))

        res, err = add(root_b)
        check("Add: 403 that says the list cannot be read",
              err is not None and err[0] == 403 and says_unreadable(err[1], rop, "0000")
              and str(root_b) in err[1])
        res, err = remove(root_a)
        check("Remove: 403 that says the list cannot be read",
              err is not None and err[0] == 403 and says_unreadable(err[1], rop, "0000"))

        new_out = tmp / "new_refs_unreadable"
        try:
            download(new_out)
            check("Download New Reference refuses", False)
        except refs.RegistryUnreadable:
            check("Download New Reference refuses before creating anything",
                  not new_out.exists())

        M._SERVE_ROOTS_MEMO.clear()
        forms = M._serve_roots("projX")
        check("project files are still served: only the reference roots drop out",
              len(forms) == 1 and Path(os.path.normpath(tmp / "projects" / "projX")) in forms[0])

        check("the list's mode is unchanged", stat.S_IMODE(rop.stat().st_mode) == 0)
    finally:
        os.chmod(rop, 0o644)

    print("[the folder holding the list cannot be entered (dependencies/ mode 0000)]")
    os.chmod(deps, 0o000)
    try:
        access = refs.registry_write_access(vsnp3)
        check("readable: false, naming the folder with its owner and mode",
              access["readable"] is False
              and "the folder that holds" in (access["reason"] or "")
              and says_unreadable(access["reason"], deps, "0000"))
        try:
            M.ref_paths()
            check("GET refuses", False)
        except refs.RegistryUnreadable as exc:
            check("GET raises RegistryUnreadable naming the folder",
                  says_unreadable(exc.reason, deps, "0000"))
    finally:
        os.chmod(deps, 0o755)

    print("[an unwritable folder with a readable list is not called unreadable]")
    os.chmod(deps, 0o555)
    try:
        res, err = add(root_b)
        check("Add: 403 that says the folder cannot be written, not read",
              err is not None and err[0] == 403 and "cannot write" in err[1]
              and "cannot read" not in err[1])
    finally:
        os.chmod(deps, 0o755)

    check_saves_keep_permissions(tmp, vsnp3, deps, rop, root_a, root_b)

    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
