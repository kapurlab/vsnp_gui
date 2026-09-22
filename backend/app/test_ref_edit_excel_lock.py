"""A workbook open in Excel is warned about — once — and never refused.

Excel writes a `~$<name>` owner file beside a workbook it has open. The GUI
always skipped those so one was never mistaken for a real workbook; it never
READ one. An edit made while somebody has the workbook open lands on the last
SAVED copy, Excel neither notices nor re-reads, and the moment that person
saves, their in-memory copy goes over the top. The row is gone, nothing
errors, and the audit log is left asserting a change the file no longer has.

Refusing on the owner file's presence is not the answer. Excel abandons them
on a crash or a dropped share and the orphans last for years: on the Ames HPC
`Mycobacterium_AF2122` has carried `~$Mbovis_define_filter.xlsx` since
December 2022, and a reference metadata workbook carried one dated eight
months BEFORE its own last save. A refusal would have made both permanently
uneditable.

So the contract pinned here: one 409 naming who and when, a second call with
`ignore_lock` goes through, the override is recorded in the audit line, a lock
older than the workbook reads as a leftover, and no lock at all changes
nothing.

Run directly:  <conda>/bin/python backend/app/test_ref_edit_excel_lock.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import openpyxl  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import main as M  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def owner_file(path: Path, name: str, age_days: float) -> Path:
    """An Excel owner file the way Excel writes one: length byte, UTF-16LE name."""
    lock = path.parent / f"~${path.name}"
    body = bytes([len(name), 0]) + name.encode("utf-16-le")
    lock.write_bytes(body + b"\x00" * (165 - len(body)))
    when = time.time() - age_days * 86400
    os.utime(lock, (when, when))
    return lock


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="excel_lock_"))
    ref_dir = tmp / "HPAI"
    ref_dir.mkdir()
    meta = ref_dir / "HPAI_vSNP_metadata.xlsx"
    wb = openpyxl.Workbook()
    wb.active.cell(row=1, column=1, value="S1")
    wb.active.cell(row=1, column=2, value="display-1")
    wb.active.cell(row=1, column=3, value="a note")
    wb.save(meta)

    M.load_config = lambda: {"vsnp3_path": str(tmp / "vsnp3")}
    M.list_references = lambda _p: [{"name": "HPAI", "path": str(ref_dir)}]
    M._ref_dir_or_404 = lambda _n: ref_dir
    M._current_os_user = lambda: "tstuber"
    M._T39_SHARED_AUDIT_PATH = tmp / "audit/reference-changes.jsonl"

    def add(sample, ignore=False):
        return M.ref_add_metadata_rows("HPAI", M.MetadataAddRequest(
            rows=[{"original": sample, "display_name": sample + "-d"}],
            ignore_lock=ignore))

    print("[no owner file: nothing changes]")
    out = add("S2")
    check("the edit goes straight through", out["added"] == 1)
    entry = json.loads(Path(out["audit_log"]).read_text().splitlines()[-1])
    check("and the audit line says nothing about a lock",
          "edited_over_excel_lock" not in entry)

    print("[a fresh owner file: one 409, naming who and when]")
    owner_file(meta, "Sebastian.Obryon", age_days=0.01)
    try:
        add("S3")
        blocked = None
    except HTTPException as exc:
        blocked = exc
    check("the first attempt is a 409, not a silent write",
          blocked is not None and blocked.status_code == 409)
    check("...naming the person Excel recorded",
          blocked is not None and "Sebastian.Obryon" in str(blocked.detail))
    check("...naming the owner file", 
          blocked is not None and "~$HPAI_vSNP_metadata.xlsx" in str(blocked.detail))
    check("...and saying what a live session would cost",
          blocked is not None and "discarded" in str(blocked.detail))
    check("nothing was written", "S3" not in [
        c.value for c in openpyxl.load_workbook(meta).worksheets[0]["A"]])

    print("[the second call, with the warning accepted, goes through]")
    out = add("S3", ignore=True)
    check("the row is added", out["added"] == 1)
    check("the note column is still there",
          openpyxl.load_workbook(meta).worksheets[0]["C1"].value == "a note")
    entry = json.loads(Path(out["audit_log"]).read_text().splitlines()[-1])
    check("the audit line records that the warning was overridden",
          entry.get("edited_over_excel_lock", {}).get("owner") == "Sebastian.Obryon")
    check("...with the lock's own timestamp, so the claim can be checked later",
          "modified" in entry.get("edited_over_excel_lock", {}))

    print("[an orphan reads as an orphan — the Ames case]")
    # Older than the workbook's own last save, so it cannot be the session
    # that wrote it. AF2122 has carried one of these since 2022.
    owner_file(meta, "klantz", age_days=1400)
    info = M._excel_lock_info(meta)
    check("it is reported as stale", info["looks_stale"] is True)
    check("...with its age in days", info["age_days"] >= 1399)
    try:
        add("S4")
        stale_detail = ""
    except HTTPException as exc:
        stale_detail = str(exc.detail)
    check("the warning still appears (presence is never silently ignored)",
          stale_detail != "")
    check("...but it reads as a leftover rather than a live session",
          "leftover" in stale_detail and "probably safe" in stale_detail)
    check("and it is still only a warning — the edit goes through when accepted",
          add("S4", ignore=True)["added"] == 1)

    print("[a live session that has already saved is NOT a leftover]")
    # The first cut of this judged staleness by "lock older than the workbook",
    # which sounds right and is not: Excel stamps the owner file when the
    # workbook is OPENED and the workbook on every SAVE, so any live session
    # that has saved once has the older lock. That heuristic called a real
    # session a leftover and told the user it was probably safe to write over.
    fresh = owner_file(meta, "Sebastian.Obryon", age_days=0.02)
    add("S6", ignore=True)                      # workbook now newer than the lock
    check("the lock is still newer than 7 days, so it is not called stale",
          M._excel_lock_info(meta)["looks_stale"] is False
          and fresh.stat().st_mtime < meta.stat().st_mtime)
    try:
        add("S7")
        live_detail = ""
    except HTTPException as exc:
        live_detail = str(exc.detail)
    check("...and the warning says what a live session would cost",
          "discarded" in live_detail and "leftover" not in live_detail)

    print("[the file listing carries the lock, so Replace can warn first]")
    listed = {f["name"]: f for f in M.ref_files("HPAI")["files"]}
    check("the workbook is listed with its lock",
          listed["HPAI_vSNP_metadata.xlsx"]["excel_lock"]["owner"] == "Sebastian.Obryon")
    check("the owner file itself is not listed as a workbook",
          "~$HPAI_vSNP_metadata.xlsx" not in listed)

    print("[an unreadable owner file still warns, naming nobody rather than failing]")
    (ref_dir / "~$HPAI_vSNP_metadata.xlsx").write_bytes(b"\xff\xfe\xff")
    info = M._excel_lock_info(meta)
    check("the lock is still reported", info is not None)
    try:
        add("S5")
        garbled = ""
    except HTTPException as exc:
        garbled = str(exc.detail)
    check("...and the warning still names the file", "~$" in garbled)

    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
