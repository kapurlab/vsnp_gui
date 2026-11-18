"""Check a pathogen add-on against this vsnp_gui's add-on contract.

    python -m app.addon_check <addon dir> [--no-fixture] [--known-answer] [--json]

`bdtools addon-check <id>` runs this with vsnp_gui's own python, so the rules
an add-on is held to are the ones the code that runs it enforces — there is no
second copy of the contract to drift. Every add-on release must pass it.

What is checked, in order (a failure stops nothing after it unless it must):
  1. manifest     addon.json against contract 1 (addons.validate_manifest)
  2. environment  the add-on's env resolves and has a python
  3. library      the markers exist; every reference folder is a usable vsnp3
                  reference (name rule, a .fasta) whose name no other reference
                  location already holds; every vcf_db_folders/<ref> names one
                  of the library's references; the scopes file parses
  4. fixture      every hook, then every action, run through app.addon_run on
                  the add-on's tests/contract/fixture project, outputs checked
                  against the contract (skip with --no-fixture)
  5. known answer the add-on's tests/known_answer/run.py, if asked for: it
                  needs a real vsnp3 run and takes minutes
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app import addons

Result = Tuple[str, bool, str]   # (check, ok, detail)


def _load(addon_dir: Path) -> Tuple[addons.Addon, List[Result]]:
    out: List[Result] = []
    try:
        m = addons.read_manifest(addon_dir)
    except (OSError, ValueError) as e:
        a = addons.Addon(id=addon_dir.name, name=addon_dir.name, version="?", dir=addon_dir, manifest={})
        a.problems.append(f"addon.json unreadable: {e}")
        out.append(("manifest", False, str(e)))
        return a, out
    problems = addons.validate_manifest(m, addon_dir)
    a = addons.Addon(id=str(m.get("id") or addon_dir.name), name=str(m.get("name") or addon_dir.name),
                     version=str(m.get("version") or "?"), dir=addon_dir, manifest=m,
                     tool=str(m.get("tool") or addon_dir.name))
    out.append(("manifest", not problems, "; ".join(problems) or f"{a.name} {a.version}, contract {addons.CONTRACT}"))
    if problems:
        a.problems.extend(problems)
        return a, out
    addons._resolve(a)
    env_problems = [p for p in a.problems if p.startswith("no environment")]
    out.append(("environment", not env_problems,
                "; ".join(env_problems) or f"{a.env_dir} ({' '.join(a.arch_prefix) or 'native'})"))
    return a, out


def _library(a: addons.Addon) -> List[Result]:
    out: List[Result] = []
    lib = a.library
    lib_problems = [p for p in a.problems if p.startswith("reference library")]
    out.append(("library markers", not lib_problems, "; ".join(lib_problems) or str(lib)))
    if lib is None or lib_problems:
        return out
    libm = a.manifest.get("library") or {}
    ro = lib / libm.get("reference_options", "reference_options")
    refs: List[str] = []
    bad: List[str] = []
    if ro.is_dir():
        for d in sorted(x for x in ro.iterdir() if x.is_dir() and not x.name.startswith(".")):
            refs.append(d.name)
            if not addons.REF_NAME_RE.fullmatch(d.name):
                bad.append(f"{d.name}: not a usable reference name (letters, digits, - and _; no dots)")
            elif not any(d.glob("*.fasta")):
                bad.append(f"{d.name}: no .fasta")
            elif not (d / f"{d.name}.fasta").is_file():
                bad.append(f"{d.name}: the FASTA must be named {d.name}.fasta (vsnp3 names its "
                           f"alignment folder after the FASTA's stem)")
    out.append(("reference folders", bool(refs) and not bad,
                "; ".join(bad) if bad else (f"{len(refs)} reference(s)" if refs else f"none under {ro}")))
    # Names another reference location already holds would be shadowed: the
    # site's folder wins, and Step 2 or IGV would open it instead.
    try:
        from app import refs as _refs
        from app.config import load_config
        others = {r["name"]: r["path"] for r in _refs.list_references(load_config()) if not r.get("addon")}
        clash = [f"{n} (also {others[n]})" for n in refs if n in others]
        out.append(("reference names unique", not clash, "; ".join(clash) or "no clash with this site's references"))
    except Exception as e:  # noqa: BLE001
        out.append(("reference names unique", True, f"not checked here: {e}"))
    vdb = lib / libm.get("vcf_db_folders", "vcf_db_folders")
    if vdb.is_dir():
        orphan = [d.name for d in vdb.iterdir() if d.is_dir() and d.name not in refs]
        out.append(("Step 2 databases", not orphan,
                    ("for no library reference: " + ", ".join(sorted(orphan))) if orphan
                    else f"{sum(1 for d in vdb.iterdir() if d.is_dir())} reference(s) with databases"))
    sc = (a.manifest.get("scopes") or {}).get("file")
    if sc:
        data = addons.read_json(lib / sc)
        ok = isinstance(data, dict) and isinstance(data.get("options"), list)
        out.append(("scopes file", ok, str(lib / sc) if ok else f"{lib / sc} missing or not {{options: [...]}}"))
    return out


def _check_row(row: Dict[str, Any], columns: List[Dict[str, Any]]) -> List[str]:
    p = []
    if not isinstance(row.get("metrics"), dict):
        p.append("metrics is not an object")
    fl = row.get("flags") or {}
    if fl.get("level") not in ("pass", "review", "fail"):
        p.append(f"flags.level {fl.get('level')!r} is not pass/review/fail")
    keys = set((row.get("metrics") or {}).keys())
    missing = [c["key"] for c in columns if c["key"] not in keys]
    if missing and row.get("status") != "failed":
        p.append("columns with no metric: " + ", ".join(missing))
    for f in row.get("files") or []:
        if not isinstance(f, dict) or not f.get("path"):
            p.append(f"file entry {f!r} has no path")
        elif not Path(f["path"]).is_file():
            p.append(f"file {f['path']} does not exist")
    return p


def _fixture(a: addons.Addon) -> List[Result]:
    src = a.dir / "tests" / "contract" / "fixture"
    if not src.is_dir():
        return [("fixture", False, f"no fixture project at {src}")]
    from app import addon_run
    out: List[Result] = []
    tmp = Path(tempfile.mkdtemp(prefix=f"addon_check_{a.id}_"))
    saved = addons.ROOTS_OVERRIDE
    try:
        proj = tmp / src.name
        shutil.copytree(src, proj, symlinks=False)
        meta = addons.read_json(proj / "project.json") or {"name": proj.name}
        meta["pathogen"] = a.id
        addons.write_json(proj / "project.json", meta)
        addons.ROOTS_OVERRIDE = [a.dir.parent]
        addons._CACHE["addons"] = None
        found = addons.get(a.id)
        if found is None or found.dir.resolve() != a.dir.resolve():
            return [("fixture", False, f"{a.dir} is not what discovery finds for id {a.id}")]
        samples = sorted(d for d in (proj / "step1").iterdir() if d.is_dir() and not d.name.startswith(("_", ".")))
        if not samples:
            return [("fixture", False, "the fixture has no step1/<sample> folder")]
        cols = a.manifest.get("columns") or []
        for sd in samples:
            reads = sorted(sd.glob("*.fastq.gz"))
            r1, r2 = (str(reads[0]) if reads else ""), (str(reads[1]) if len(reads) > 1 else "")
            buf_args = ["--project-dir", str(proj), "--sample-dir", str(sd), "--sample", sd.name, "--threads", "2"]
            import contextlib
            import io
            cap = io.StringIO()
            with contextlib.redirect_stdout(cap):
                rc = addon_run.main(["choose_reference", *buf_args, "--r1", r1, "--r2", r2, "--read-type",
                                     "paired" if r2 else "single"])
            ref_dir = cap.getvalue().strip().splitlines()[-1] if cap.getvalue().strip() else ""
            why = (sd / ".provenance" / addon_run.CHOOSE_FAILED)
            out.append((f"choose_reference {sd.name}", rc == 0 and bool(ref_dir),
                        ref_dir or (why.read_text().strip() if why.exists() else f"exit {rc}")))
            if rc != 0:
                continue
            if a.hook_path("prepare_reads") is not None:
                cap = io.StringIO()
                with contextlib.redirect_stdout(cap):
                    addon_run.main(["prepare_reads", *buf_args, "--r1", r1, "--r2", r2, "--reference-dir", ref_dir])
                lines = cap.getvalue().splitlines()
                pf = sd / ".provenance" / addon_run.PREPARE_FAILED
                out.append((f"prepare_reads {sd.name}", not pf.exists() and bool(lines and lines[0]),
                            pf.read_text().strip() if pf.exists() else (lines[0] if lines else "no output")))
            addon_run.main(["after_sample", *buf_args, "--reference-dir", ref_dir])
            res = addons.read_json(addons.sample_out_dir(proj, a.id, sd.name) / "result.json") or {}
            probs = _check_row(res, cols) if res else ["no result.json"]
            if res.get("status") == "failed":
                probs.insert(0, "; ".join((res.get("flags") or {}).get("reasons") or ["after_sample failed"]))
            out.append((f"after_sample {sd.name}", not probs, "; ".join(probs) or
                        f"{len(res.get('metrics') or {})} metric(s), {res.get('flags', {}).get('level')}"))
        addon_run.main(["after_run", "--project-dir", str(proj), "--threads", "2"])
        if a.hook_path("after_run") is not None:
            summ = addons.read_json(addons.pathogen_dir(proj, a.id) / "summary.json")
            out.append(("after_run", summ is not None, "summary.json written" if summ is not None else "no summary.json"))
        for act in a.manifest.get("actions") or []:
            params = {p["name"]: p.get("fixture") or p.get("default") or "" for p in act.get("params") or []
                      if isinstance(p, dict) and p.get("name")}
            run_dir = addons.runs_dir(proj, a.id) / f"check_{act['id']}"
            rc = addon_run.main(["action", "--project-dir", str(proj), "--action", act["id"],
                                 "--params", json.dumps(params), "--run-dir", str(run_dir), "--threads", "2"])
            ans = addons.read_json(run_dir / "action.json") or {}
            missing = [f.get("path") for f in ans.get("files") or [] if not Path(str(f.get("path"))).is_file()]
            out.append((f"action {act['id']}", rc == 0 and not missing,
                        ("missing files: " + ", ".join(missing)) if missing else (ans.get("summary") or f"exit {rc}")))
    finally:
        addons.ROOTS_OVERRIDE = saved
        addons._CACHE["addons"] = None
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _known_answer(a: addons.Addon) -> List[Result]:
    script = a.dir / "tests" / "known_answer" / "run.py"
    if not script.is_file():
        return [("known answer", True, "this add-on ships none")]
    cmd = addons.hook_command(a, script, Path("/dev/null"))[:-2]  # no --context
    proc = subprocess.run(cmd, cwd=str(script.parent), env=addons.hook_env(a), capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    return [("known answer", proc.returncode == 0, tail[0])]


def check(addon_dir: Path, fixture: bool = True, known_answer: bool = False) -> List[Result]:
    a, out = _load(addon_dir)
    if not out[0][1]:
        return out
    out += _library(a)
    ready = all(ok for _n, ok, _d in out)
    if fixture and ready:
        out += _fixture(a)
    elif fixture:
        out.append(("fixture", False, "not run: fix the checks above first"))
    if known_answer and ready:
        out += _known_answer(a)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.addon_check")
    ap.add_argument("addon_dir")
    ap.add_argument("--no-fixture", action="store_true")
    ap.add_argument("--known-answer", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    results = check(Path(a.addon_dir).resolve(), fixture=not a.no_fixture, known_answer=a.known_answer)
    bad = [r for r in results if not r[1]]
    if a.json:
        print(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], indent=1))
    else:
        for n, ok, d in results:
            print(f"  {'ok  ' if ok else 'FAIL'} {n}: {d}")
        print(f"\n{len(bad)} check(s) failed." if bad else "\nThe add-on meets the contract.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
