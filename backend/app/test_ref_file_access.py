"""Reference Editor file access when the reference tree is built of SYMLINKS.

Reported from the USDA Ames HPC: in the Reference Editor, no file could be
viewed or downloaded — every button produced a bare `{"detail":"Path not
allowed"}` page — while the identical build was fine on another server. Step 1
and Step 2 downloads on the same machine worked throughout, so it was not the
OnDemand proxy and not the browser.

The difference was in the reference tree, not in the deployment. `_resolve_ref_file`
joined the bare filename onto the reference directory, re-resolved the result,
and demanded it still sit under that directory. A reference set that shares one
curated master spreadsheet between references keeps those entries as symlinks,
so the resolved path lands outside and every one of them was refused. The same
check sat in the upload path, so Replace was broken by the same layout — which
is why this file tests both.

`_serve_path_allowed` had already been widened for exactly this reason (igv.js
fetching a GFF through a directory of symlinks); the Reference Editor never was.

Replace then had a second bug on the same layout, which this file also pins.
It installed the upload by RENAME, which put a new file at the LINK's path: the
reference silently stopped sharing (the master kept the old content, and every
sibling reference kept reading it), and because a rename means a new inode, the
file arrived owned by the uploader with the tree's mode and ACL gone. An edit
now goes THROUGH the link into the master, and the master is written in place.

Run directly:  <conda>/bin/python backend/app/test_ref_file_access.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import main as M  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def use_ref(ref_dir: Path) -> None:
    """Point the reference lookup at one directory, whatever is registered."""
    M.load_config = lambda: {"vsnp3_path": "/nonexistent"}
    M.list_references = lambda _p: [
        {"name": "R", "path": str(ref_dir), "root": str(ref_dir.parent), "shadowed": []}
    ]


def resolve(name: str):
    """(path, None) or (None, (status, detail))."""
    try:
        return M._resolve_ref_file("R", name), None
    except HTTPException as e:
        return None, (e.status_code, str(e.detail))


class StubUpload:
    """The two attributes ref_upload_file uses from an UploadFile."""

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data
        self._at = 0

    async def read(self, n: int) -> bytes:
        chunk = self._data[self._at:self._at + n]
        self._at += len(chunk)
        return chunk


XLSX = b"PK\x03\x04" + b"replacement payload"


def upload(name: str, data: bytes = XLSX):
    try:
        return asyncio.run(
            M.ref_upload_file("R", file=StubUpload(name, data), rationale="test")
        ), None
    except HTTPException as e:
        return None, (e.status_code, str(e.detail))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ref_access_"))
    # Audit writes must not escape into the real shared audit log.
    M._T39_SHARED_AUDIT_PATH = tmp / "audit" / "reference-changes.jsonl"

    print("[a plain reference directory still works]")
    plain = tmp / "plain/Mycobacterium_AF2122"
    plain.mkdir(parents=True)
    (plain / "Mbovis_define_filter.xlsx").write_bytes(b"PK\x03\x04original")
    use_ref(plain)
    got, err = resolve("Mbovis_define_filter.xlsx")
    check("a real file resolves", got == plain / "Mbovis_define_filter.xlsx")

    print("[the Ames layout: the spreadsheet is a symlink to a curated master]")
    master = tmp / "curated"
    master.mkdir()
    (master / "Mbovis_define_filter.xlsx").write_bytes(b"PK\x03\x04master")
    linked = tmp / "linked/Mycobacterium_AF2122"
    linked.mkdir(parents=True)
    os.symlink(master / "Mbovis_define_filter.xlsx",
               linked / "Mbovis_define_filter.xlsx")
    (linked / "NC_002945v4.fasta").write_text(">x\nACGT\n")
    use_ref(linked)
    got, err = resolve("Mbovis_define_filter.xlsx")
    check("a symlinked spreadsheet resolves (was 400 Path not allowed)",
          got is not None and got.read_bytes() == b"PK\x03\x04master")

    print("[a symlink pointing at nothing says so, instead of 'File not found']")
    os.symlink(tmp / "not-mounted/Mbovis_remove_from_analysis.xlsx",
               linked / "Mbovis_remove_from_analysis.xlsx")
    got, err = resolve("Mbovis_remove_from_analysis.xlsx")
    check("dangling symlink is a 404", err is not None and err[0] == 404)
    check("...naming the link target", err is not None and "not-mounted" in err[1])

    print("[confinement: only a direct child of the reference dir]")
    for bad in ("../curated/Mbovis_define_filter.xlsx", "/etc/passwd",
                "..", ".", ".history", "sub/file.xlsx", ""):
        got, err = resolve(bad)
        check(f"refused: {bad!r}", got is None and err[0] == 400)

    print("[the listing explains a file that will not open]")
    listed = M.ref_files("R")
    by_name = {f["name"]: f for f in listed["files"]}
    check("the symlinked define_filter is listed",
          "Mbovis_define_filter.xlsx" in by_name)
    check("...and marked as a symlink",
          "symlink_to" in by_name.get("Mbovis_define_filter.xlsx", {}))
    check("a dangling link is listed as not existing",
          by_name.get("Mbovis_remove_from_analysis.xlsx", {}).get("exists") is False)
    check("the listing says whether the tree is writable",
          listed.get("ref_dir_writable") is True)

    print("[Replace, through a symlink, writes the shared master and keeps the link]")
    master_file = master / "Mbovis_define_filter.xlsx"
    master_ino = master_file.stat().st_ino
    res, err = upload("Mbovis_define_filter.xlsx")
    check("upload accepted (was 400 Path not allowed)", err is None)
    if res:
        check("the master holds the uploaded bytes",
              master_file.read_bytes() == XLSX)
        check("...written THROUGH the existing file, so its inode (and any ACL "
              "or ownership on it) survived", master_file.stat().st_ino == master_ino)
        check("the reference entry is still a link",
              (linked / "Mbovis_define_filter.xlsx").is_symlink())
        check("...still pointing at the master",
              os.path.realpath(linked / "Mbovis_define_filter.xlsx")
              == os.path.realpath(master_file))
        archived = Path(res["archived_old"])
        check("the previous version was archived by CONTENT",
              archived.is_file() and not archived.is_symlink()
              and archived.read_bytes() == b"PK\x03\x04master")
        check("...beside the master, where every reference sharing it can find it",
              archived.parent == Path(os.path.realpath(master)) / ".history")
        check("the record names the shared file the bytes went into",
              os.path.realpath(res.get("master", "")) == os.path.realpath(master_file))

    print("[Replace on a read-only reference tree refuses in words]")
    ro = tmp / "readonly/Mycobacterium_AF2122"
    ro.mkdir(parents=True)
    (ro / "Mbovis_define_filter.xlsx").write_bytes(b"PK\x03\x04ro")
    os.chmod(ro, 0o555)
    use_ref(ro)
    try:
        res, err = upload("Mbovis_define_filter.xlsx")
        check("refused with 403, not a 500 traceback", err is not None and err[0] == 403)
        check("...naming the directory", err is not None and str(ro) in err[1])
        check("the file is unchanged",
              (ro / "Mbovis_define_filter.xlsx").read_bytes() == b"PK\x03\x04ro")
    finally:
        os.chmod(ro, 0o755)

    print("[Replace still refuses a filename outside the whitelist]")
    use_ref(plain)
    res, err = upload("NC_002945v4.fasta", b"PK\x03\x04")
    check("a fasta cannot be replaced via this endpoint",
          err is not None and err[0] == 400)

    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
