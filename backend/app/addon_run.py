"""Run one pathogen add-on hook for the Step 1 batch, an action, or a re-run.

The Step 1 batch script (run_step1.sh) calls this between its own steps for a
project that names a pathogen (see addons.py for the contract):

  choose_reference  before vsnp3: prints the reference folder for -t, or exits 3
  prepare_reads     before vsnp3: prints the reads vsnp3 should align (R1, R2)
  after_sample      after vsnp3 succeeded: the add-on's Results row
  after_run         once, after the last sample: the run summary and report

and the backend runs it as a job for the Pathogen panel:

  action <id>       an add-on action (a tree, a retype...) in its own run folder
  rerun             after_sample for every finished sample, then after_run

Running the hooks inside the batch script (not in a job of their own) keeps
everything the batch already guarantees: Stop kills them with the batch, the
409 guard and restart recovery see one batch, the parallel pool is shared, and
their output lands in the sample's run_step1.log under Live Logs.

What a failed hook means, by hook:
  choose_reference  the sample is not aligned; .provenance/addon_choose_failed
                    says why and Step 1 Results shows it (no guess at a reference)
  prepare_reads     vsnp3 aligns the original reads; the sample is marked review
  after_sample      the vsnp3 result stands; the sample is marked review
  after_run         noted in the batch log; nothing else changes

Sentinels (in the sample's .provenance/): addon_choose_failed (the reason) and
addon_done (after_sample has run for the current alignment). An add-on project's
sample counts as complete only with both exit_code 0 and addon_done.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import addons

CHOOSE_FAILED = "addon_choose_failed"
DONE = "addon_done"
PREPARE_FAILED = "addon_prepare_failed"
EXIT_NO_REFERENCE = 3


def _prov(sample_dir: Path) -> Path:
    p = sample_dir / ".provenance"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _note(log: Optional[Path], text: str) -> None:
    line = f"[pathogen add-on] {text}\n"
    sys.stderr.write(line)
    if log is not None:
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass


def _record(sample_dir: Path, addon: addons.Addon, key: str, value: Any) -> None:
    """Merge one entry into the sample's add-on provenance: .provenance/addon.json
    and, when the dispatch wrote one, the "addon" block of run_metadata.json."""
    prov = _prov(sample_dir) / "addon.json"
    rec = addons.read_json(prov) or {"addon": addons.addon_identity(addon), "hooks": {}}
    if key == "hook":
        rec.setdefault("hooks", {})[value["hook"]] = value
    else:
        rec[key] = value
    addons.write_json(prov, rec)
    meta = sample_dir / "run_metadata.json"
    data = addons.read_json(meta)
    if data is not None:
        data["addon"] = rec
        addons.write_json(meta, data)


def _hook_record(hook: str, res: Dict[str, Any]) -> Dict[str, Any]:
    return {"hook": hook, "returncode": res["returncode"], "command": res["command"],
            "started_at": res["started_at"], "finished_at": res["finished_at"]}


def _project_addon(project_dir: Path, log: Optional[Path]) -> Optional[addons.Addon]:
    aid = addons.project_pathogen(project_dir)
    addon = addons.get(aid)
    if addon is None:
        _note(log, f"project names pathogen {aid!r}, but no such add-on is installed")
        return None
    if not addon.ready:
        _note(log, f"the {addon.name} add-on cannot run here: " + "; ".join(addon.problems))
        return None
    return addon


def _base_context(hook: str, addon: addons.Addon, project_dir: Path, threads: int) -> Dict[str, Any]:
    return {
        "contract": addons.CONTRACT,
        "hook": hook,
        "addon": addons.addon_identity(addon),
        "library": str(addon.library) if addon.library else None,
        "threads": threads,
        "project": {
            "name": project_dir.name,
            "dir": str(project_dir),
            "scope": addons.project_scope(project_dir),
            "pathogen_dir": str(addons.pathogen_dir(project_dir, addon.id)),
        },
    }


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _check_reference(addon: addons.Addon, project_dir: Path, ref: Path) -> Optional[str]:
    """Why a returned reference folder may not be used, or None."""
    if not ref.is_dir():
        return f"{ref} is not a folder"
    if not addons.REF_NAME_RE.fullmatch(ref.name):
        return (f"reference folder name {ref.name!r} is not usable by vsnp3 (letters, digits, - and _ "
                "only; no dots, which vsnp3 cuts)")
    if not any(ref.glob("*.fasta")):
        return f"{ref} holds no .fasta"
    lib = addon.library
    allowed = [lib] if lib else []
    allowed.append(addons.pathogen_dir(project_dir, addon.id) / "refs")
    if not any(_inside(ref, a) for a in allowed):
        return (f"{ref} is outside the add-on's library and the project's pathogen/{addon.id}/refs; "
                "an add-on may only hand vsnp3 a reference it owns")
    return None


def _newest(paths: List[Path]) -> Optional[Path]:
    paths = [p for p in paths if p.is_file()]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def _step1_outputs(sample_dir: Path, sample: str) -> Dict[str, Any]:
    stats = _newest(list(sample_dir.glob(f"{sample}_*_stats.xlsx")) or list(sample_dir.glob("*_stats.xlsx")))
    aligned = sorted(p for p in sample_dir.glob("alignment_*") if p.is_dir())
    al = aligned[0] if aligned else None
    out: Dict[str, Any] = {"stats_workbook": str(stats) if stats else None,
                           "alignment_dir": str(al) if al else None,
                           "zc_vcf": None, "bam": None, "reference_fasta": None}
    if al is not None:
        z = _newest(list(al.glob("*_zc.vcf")))
        b = _newest([p for p in al.glob("*.bam") if "unmapped" not in p.name])
        f = sorted(al.glob("*.fasta"))
        out.update(zc_vcf=str(z) if z else None, bam=str(b) if b else None,
                   reference_fasta=str(f[0]) if f else None)
    return out


def _qc(sample_dir: Path, project_dir: Path, stats: Optional[str]) -> Dict[str, Any]:
    if not stats:
        return {"row": None, "verdict": None}
    from app import qc_scan, qc_verdict
    from app.config import load_config
    row = qc_scan._read_stats_row(stats) or {}
    clean = {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in row.items()}
    try:
        th = addons.merged_thresholds(load_config(), project_dir)
    except Exception:  # noqa: BLE001 — a verdict is context, not a precondition
        th = qc_verdict.merge_thresholds()
    return {"row": clean, "verdict": qc_verdict.compute_verdict(clean, th), "thresholds": th}


# --------------------------------------------------------------------------
# hooks
# --------------------------------------------------------------------------

def choose_reference(a: argparse.Namespace) -> int:
    project_dir, sample_dir = Path(a.project_dir), Path(a.sample_dir)
    log = sample_dir / "run_step1.log"
    prov = _prov(sample_dir)
    for f in (CHOOSE_FAILED, DONE, PREPARE_FAILED):
        (prov / f).unlink(missing_ok=True)

    def fail(reason: str) -> int:
        (prov / CHOOSE_FAILED).write_text(reason.strip() + "\n", encoding="utf-8")
        _note(log, f"no reference chosen for {a.sample}: {reason}")
        return EXIT_NO_REFERENCE

    addon = _project_addon(project_dir, log)
    if addon is None:
        return fail("the project's pathogen add-on is not installed or not ready (see the Pathogen panel)")
    out = addons.sample_out_dir(project_dir, addon.id, a.sample)
    # Whatever an earlier run of this sample left is stale once it re-aligns.
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    ctx = _base_context("choose_reference", addon, project_dir, a.threads)
    ctx["sample"] = {"name": a.sample, "step1_dir": str(sample_dir), "out_dir": str(out),
                     "reads": [r for r in (a.r1, a.r2) if r], "read_type": a.read_type}
    res = addons.run_script(addon, addon.hook_path("choose_reference"), ctx, out, log_path=log)
    _record(sample_dir, addon, "hook", _hook_record("choose_reference", res))
    ans = addons.read_json(out / "reference.json")
    if res["returncode"] != 0:
        why = (ans or {}).get("error") or f"the chooser exited {res['returncode']} (see run_step1.log)"
        return fail(str(why))
    if not ans or not ans.get("reference_dir"):
        return fail("the chooser wrote no reference.json with a reference_dir")
    ref = Path(ans["reference_dir"])
    problem = _check_reference(addon, project_dir, ref)
    if problem:
        return fail(problem)
    fasta = sorted(ref.glob("*.fasta"))[0]
    try:
        from app import provenance_writer
        state = provenance_writer.capture_reference_state_at(ref)
    except Exception as e:  # noqa: BLE001 — a hash is a record, not a precondition
        state = {"name": ref.name, "path": str(ref), "capture_error": str(e)}
    state.update({"fasta": str(fasta), "chosen_by": f"{addon.id} {addon.version}",
                  "details": {k: v for k, v in ans.items() if k != "reference_dir"}})
    _record(sample_dir, addon, "reference", state)
    _note(log, f"{addon.name}: reference for {a.sample} is {ref.name}")
    print(str(ref.resolve()))
    return 0


def prepare_reads(a: argparse.Namespace) -> int:
    project_dir, sample_dir = Path(a.project_dir), Path(a.sample_dir)
    log = sample_dir / "run_step1.log"
    reads = [r for r in (a.r1, a.r2) if r]

    def keep(reason: Optional[str]) -> int:
        if reason:
            (_prov(sample_dir) / PREPARE_FAILED).write_text(reason + "\n", encoding="utf-8")
            _note(log, f"read preparation skipped for {a.sample}: {reason}; aligning the reads as given")
        print(a.r1)
        print(a.r2 or "")
        return 0

    addon = _project_addon(project_dir, log)
    if addon is None or addon.hook_path("prepare_reads") is None:
        return keep(None)
    out = addons.sample_out_dir(project_dir, addon.id, a.sample)
    ctx = _base_context("prepare_reads", addon, project_dir, a.threads)
    ctx["sample"] = {"name": a.sample, "step1_dir": str(sample_dir), "out_dir": str(out),
                     "reads": reads, "read_type": a.read_type, "reference_dir": a.reference_dir}
    res = addons.run_script(addon, addon.hook_path("prepare_reads"), ctx, out, log_path=log)
    _record(sample_dir, addon, "hook", _hook_record("prepare_reads", res))
    if res["returncode"] != 0:
        return keep(f"prepare_reads exited {res['returncode']}")
    ans = addons.read_json(out / "reads.json")
    new = [str(x) for x in (ans or {}).get("reads") or []]
    if len(new) != len(reads):
        # A paired sample must stay paired: one clipped mate beside an
        # unclipped one no longer pairs read for read.
        return keep(f"prepare_reads returned {len(new)} read file(s) for {len(reads)}")
    missing = [x for x in new if not Path(x).is_file() or Path(x).stat().st_size == 0]
    if missing:
        return keep("prepare_reads returned missing or empty file(s): " + ", ".join(missing))
    _record(sample_dir, addon, "prepared_reads", [
        {"path": x, "bytes": Path(x).stat().st_size, "from": src} for x, src in zip(new, reads)])
    _note(log, f"{addon.name}: reads prepared for {a.sample}")
    print(new[0])
    print(new[1] if len(new) > 1 else "")
    return 0


def _after_sample(project_dir: Path, sample_dir: Path, sample: str, addon: addons.Addon,
                  threads: int, reference_dir: Optional[str], log: Optional[Path]) -> None:
    out = addons.sample_out_dir(project_dir, addon.id, sample)
    out.mkdir(parents=True, exist_ok=True)
    step1 = _step1_outputs(sample_dir, sample)
    qc = _qc(sample_dir, project_dir, step1["stats_workbook"])
    ref_rec = (addons.read_json(sample_dir / ".provenance" / "addon.json") or {}).get("reference") or {}
    ctx = _base_context("after_sample", addon, project_dir, threads)
    ctx["sample"] = {"name": sample, "step1_dir": str(sample_dir), "out_dir": str(out),
                     "reference_dir": reference_dir or ref_rec.get("path"),
                     "step1": step1, "qc": qc,
                     "prepare_failed": (sample_dir / ".provenance" / PREPARE_FAILED).is_file()}
    res = addons.run_script(addon, addon.hook_path("after_sample"), ctx, out, log_path=log)
    _record(sample_dir, addon, "hook", _hook_record("after_sample", res))
    result = addons.read_json(out / "result.json") if res["returncode"] == 0 else None
    if result is None:
        result = {"sample": sample, "status": "failed", "metrics": {}, "files": [],
                  "flags": {"level": "review", "reasons": [
                      f"{addon.name}: after_sample " + (f"exited {res['returncode']}" if res["returncode"]
                                                         else "wrote no result.json") + " (see run_step1.log)"]}}
    flags = result.get("flags") if isinstance(result.get("flags"), dict) else {}
    level = flags.get("level") if flags.get("level") in ("pass", "review", "fail") else "pass"
    reasons = [str(r) for r in (flags.get("reasons") or [])]
    pf = sample_dir / ".provenance" / PREPARE_FAILED
    if pf.is_file():
        reasons.append("reads were not prepared: " + pf.read_text(encoding="utf-8").strip())
        level = "review" if level == "pass" else level
    result["flags"] = {"level": level, "reasons": reasons}
    result["sample"] = sample
    stats = step1["stats_workbook"]
    try:
        st = Path(stats).stat() if stats else None
    except OSError:
        st = None
    # Tie the row to the alignment it describes: a later re-run that has not
    # reached after_sample yet must not show this one beside its new stats.
    result["_stamp"] = {"stats_workbook": stats, "mtime_ns": st.st_mtime_ns if st else None,
                        "size": st.st_size if st else None, "addon": f"{addon.id} {addon.version}",
                        "written_at": time.time()}
    addons.write_json(out / "result.json", result)
    (_prov(sample_dir) / DONE).write_text(f"{addon.id} {addon.version}\n", encoding="utf-8")


def after_sample(a: argparse.Namespace) -> int:
    project_dir, sample_dir = Path(a.project_dir), Path(a.sample_dir)
    log = sample_dir / "run_step1.log"
    addon = _project_addon(project_dir, log)
    if addon is None:
        return 0
    _after_sample(project_dir, sample_dir, a.sample, addon, a.threads, a.reference_dir, log)
    return 0


def _results(project_dir: Path, addon: addons.Addon) -> List[Dict[str, Any]]:
    base = addons.pathogen_dir(project_dir, addon.id) / "samples"
    rows = []
    try:
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            r = addons.read_json(d / "result.json")
            if r is not None:
                rows.append(r)
    except OSError:
        pass
    return rows


def _after_run(project_dir: Path, addon: addons.Addon, threads: int, log: Optional[Path]) -> int:
    if addon.hook_path("after_run") is None:
        return 0
    pdir = addons.pathogen_dir(project_dir, addon.id)
    ctx = _base_context("after_run", addon, project_dir, threads)
    ctx["samples"] = _results(project_dir, addon)
    ctx["step1_dir"] = str(project_dir / "step1")
    res = addons.run_script(addon, addon.hook_path("after_run"), ctx, pdir, log_path=log)
    if res["returncode"] != 0:
        _note(log, f"{addon.name}: after_run exited {res['returncode']}; the run report may be out of date")
    return 0


def after_run(a: argparse.Namespace) -> int:
    project_dir = Path(a.project_dir)
    log = Path(a.log) if a.log else None
    addon = _project_addon(project_dir, log)
    if addon is None:
        return 0
    return _after_run(project_dir, addon, a.threads, log)


def action(a: argparse.Namespace) -> int:
    """An add-on action, run as a backend job. Output goes to this process's
    stdout (the job log) and the action's files to its own run folder."""
    project_dir = Path(a.project_dir)
    addon = _project_addon(project_dir, None)
    if addon is None:
        return 2
    spec = next((x for x in addon.manifest.get("actions") or [] if x.get("id") == a.action), None)
    if spec is None:
        print(f"{addon.name} has no action {a.action!r}", flush=True)
        return 2
    run_dir = Path(a.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        params = json.loads(a.params or "{}")
    except ValueError:
        params = {}
    ctx = _base_context(f"action:{a.action}", addon, project_dir, a.threads)
    ctx.update(run_dir=str(run_dir), params=params, samples=_results(project_dir, addon),
               step1_dir=str(project_dir / "step1"))
    meta = {"kind": "addon_action", "addon": addons.addon_identity(addon), "action": a.action,
            "params": params, "status": "running", "started_at": time.time()}
    addons.write_json(run_dir / "run_metadata.json", meta)
    print(f"[{addon.id} {addon.version}] {spec.get('label') or a.action} -> {run_dir}", flush=True)
    res = addons.run_script(addon, addon.dir / spec["command"], ctx, run_dir, log_path=None)
    ans = addons.read_json(run_dir / "action.json") or {}
    ok = res["returncode"] == 0 and ans.get("status", "ok") in ("ok", "succeeded")
    meta.update(status="ok" if ok else "failed", finished_at=time.time(),
                returncode=res["returncode"], command=res["command"],
                summary=ans.get("summary", ""))
    addons.write_json(run_dir / "run_metadata.json", meta)
    print(f"[{addon.id}] {'finished' if ok else 'FAILED'}: {ans.get('summary', '')}", flush=True)
    return 0 if ok else 1


def rerun(a: argparse.Namespace) -> int:
    """after_sample for every sample whose vsnp3 run finished, then after_run —
    the add-on's steps again without re-aligning anything."""
    project_dir = Path(a.project_dir)
    addon = _project_addon(project_dir, None)
    if addon is None:
        return 2
    step1 = project_dir / "step1"
    done = 0
    for d in sorted(p for p in step1.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        ec = d / ".provenance" / "exit_code"
        try:
            ok = ec.read_text(encoding="utf-8").strip() == "0"
        except OSError:
            ok = False
        if not ok:
            continue
        if a.samples and d.name not in a.samples:
            continue
        print(f"[{addon.id}] after_sample {d.name}", flush=True)
        _after_sample(project_dir, d, d.name, addon, a.threads, None, d / "run_step1.log")
        try:
            os.utime(d)   # the Step 1 status cache is keyed on the sample folder's mtime
        except OSError:
            pass
        done += 1
    print(f"[{addon.id}] after_run over {done} sample(s)", flush=True)
    _after_run(project_dir, addon, a.threads, None)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.addon_run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, sample=True):
        p.add_argument("--project-dir", required=True)
        p.add_argument("--threads", type=int, default=4)
        if sample:
            p.add_argument("--sample-dir", required=True)
            p.add_argument("--sample", required=True)

    p = sub.add_parser("choose_reference"); common(p)
    p.add_argument("--r1", required=True); p.add_argument("--r2", default="")
    p.add_argument("--read-type", default="")
    p.set_defaults(fn=choose_reference)
    p = sub.add_parser("prepare_reads"); common(p)
    p.add_argument("--r1", required=True); p.add_argument("--r2", default="")
    p.add_argument("--read-type", default=""); p.add_argument("--reference-dir", default="")
    p.set_defaults(fn=prepare_reads)
    p = sub.add_parser("after_sample"); common(p)
    p.add_argument("--reference-dir", default="")
    p.set_defaults(fn=after_sample)
    p = sub.add_parser("after_run"); common(p, sample=False)
    p.add_argument("--log", default="")
    p.set_defaults(fn=after_run)
    p = sub.add_parser("action"); common(p, sample=False)
    p.add_argument("--action", required=True); p.add_argument("--params", default="{}")
    p.add_argument("--run-dir", required=True)
    p.set_defaults(fn=action)
    p = sub.add_parser("rerun"); common(p, sample=False)
    p.add_argument("--samples", nargs="*", default=[])
    p.set_defaults(fn=rerun)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
