"""Step 1 and Step 2 hand vsnp3 the reference FOLDER, found through this app's
reference locations, never a name for vsnp3 to look up in its install's list.

vSNP's -t takes a name or a folder: Ref_Options checks os.path.isdir() first
and reads a folder's files with metadata_gather(), the same function a name
match calls on the folder it finds. So `-t <folder>` gets the files `-t <name>`
got, from the folder the GUI showed, while the list inside the vsnp3 install
(unwritable on shared installs, reset by conda) is not consulted at all.
Explicit `-r <fasta> -b <gbk>` would not do: given -r, vsnp3_step1.py ignores
-t and the stats lose their Groups and Reference cells.

Pinned here:
  - Step 1's batch script passes `-t <first location holding the name>`, one
    argv entry even with a space in the path, the shadowed copy not chosen;
  - Step 2's command does the same;
  - a reference no location has is refused (400, saying what to do) before
    anything is written, staged or started;
  - Auto-detect (no reference) passes no -t, and is refused with the reason
    when vsnp3's own list, which it uses for that, cannot be read;
  - the stats Reference cell vsnp3 then writes ("<folder> Forced") reads back
    as the reference name, so reference_lock and Step 2's mismatch check see
    one reference, not a path.

Run directly:  PYTHONPATH=backend <conda>/bin/python backend/app/test_reference_folder_arg.py
"""
from __future__ import annotations

import gzip
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import main as m  # noqa: E402

FAILED = 0
REF = "Mycobacterium_AF2122"


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def call(fn, *args):
    try:
        return fn(*args), None
    except HTTPException as e:
        return None, (e.status_code, str(e.detail))


def make_ref(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.fasta").write_text(">NC_002945.4\nACGT\n", encoding="utf-8")
    (d / f"{name}.gbk").write_text("LOCUS x\n", encoding="utf-8")
    return d


def run_args(script: Path) -> list:
    """The argv the batch script builds for vsnp3_step1.py, evaluated by bash."""
    lines = script.read_text().split("\n")
    build = [ln for ln in lines if ln.strip().startswith("RUN_ARGS")]
    snippet = "R1=r1.fastq.gz; R2=r2.fastq.gz; NP=\n" + "\n".join(build) + '\nprintf "%s\\n" "${RUN_ARGS[@]}"\n'
    out = subprocess.run(["/bin/bash", "-c", snippet], capture_output=True, text=True)
    return out.stdout.split("\n")[:-1]


class Fixture:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.first = tmp / "ref locs"          # a space, to prove the quoting
        self.second = tmp / "second"
        self.folder = make_ref(self.first, REF)
        make_ref(self.second, REF)              # shadowed by the first
        make_ref(self.second, "Brucella_suis3")
        self.vsnp3 = tmp / "vsnp3"
        (self.vsnp3 / "bin").mkdir(parents=True)
        (self.vsnp3 / "bin" / "vsnp3_step2.py").write_text("#!/usr/bin/env python\n")
        (self.vsnp3 / "dependencies").mkdir()
        self.projects = tmp / "projects"
        self.cfg = {"vsnp3_path": str(self.vsnp3), "projects_root": str(self.projects),
                    "reference_locations": [str(self.first), str(self.second)],
                    "step1_max_parallel": 1}
        self.started = []

    def project(self, name: str) -> Path:
        p = self.projects / name
        s = p / "step1" / "S1"
        s.mkdir(parents=True)
        for r in ("R1", "R2"):
            (s / f"S1_{r}_001.fastq.gz").write_bytes(gzip.compress(os.urandom(80_000)))
        return p


def test_step1(fx: Fixture) -> None:
    print("[Step 1 hands vsnp3 the folder]")
    project = fx.project("p1")
    res, err = call(m.step1_run, "p1", m.Step1Request(reference=REF))
    script = project / "step1" / "run_step1.sh"
    check("the run starts", err is None and fx.started)
    args = run_args(script) if script.exists() else []
    check("RUN_ARGS carries -t <folder of the first location holding the name>",
          "-t" in args and args[args.index("-t") + 1] == str(fx.folder))
    check("...as one argument although the path has a space", str(fx.folder) in args)
    check("...not the name, and not the shadowed copy",
          REF not in args and str(fx.second / REF) not in args)
    check("project.json still records the reference NAME",
          m._project_reference(project) == REF)

    print("[a reference no location has is refused before anything happens]")
    fx.started.clear()
    project2 = fx.project("p2")
    res, err = call(m.step1_run, "p2", m.Step1Request(reference="Not_A_Reference"))
    check("400, naming the reference and where to add its folder",
          err is not None and err[0] == 400 and "Not_A_Reference" in err[1]
          and "Reference Locations" in err[1])
    check("...no script written, no job started",
          not (project2 / "step1" / "run_step1.sh").exists() and not fx.started)

    print("[Auto-detect: vsnp3 picks the reference itself, from its install's list]")
    project3 = fx.project("p3")
    res, err = call(m.step1_run, "p3", m.Step1Request(reference=None))
    check("refused when vsnp3's own list is missing, saying so",
          err is not None and err[0] == 400 and "Auto-detect" in err[1]
          and "reference_options_paths.txt" in err[1] and "does not exist" in err[1])
    rop = fx.vsnp3 / "dependencies" / "reference_options_paths.txt"
    rop.write_text(f"{fx.first}\n")
    os.chmod(rop, 0)
    try:
        res, err = call(m.step1_run, "p3", m.Step1Request(reference=None))
        check("refused when that list cannot be read, saying so",
              err is not None and err[0] == 400 and "cannot be read" in err[1])
    finally:
        os.chmod(rop, 0o644)
    fx.started.clear()
    res, err = call(m.step1_run, "p3", m.Step1Request(reference=None))
    args = run_args(project3 / "step1" / "run_step1.sh")
    check("with the list readable it runs, and passes no -t",
          err is None and fx.started and "-t" not in args)


def test_step2(fx: Fixture) -> None:
    print("[Step 2 hands vsnp3 the folder]")
    project = fx.projects / "p4"
    db = project / "step2" / "vcf_database"
    db.mkdir(parents=True)
    samples = ["TX-1", "TX-2", "TX-3", "TX-4"]
    for s in samples:
        (db / f"{s}_zc.vcf").write_text("##fileformat=VCFv4.2\n")
    commands = []
    saved = {k: getattr(m, k) for k in ("reference_lock", "_step2_reference_audit", "build_env")}
    saved_start, saved_dispatch = m.job_manager.start_job, m.provenance_writer.dispatch_step2
    m.reference_lock = lambda p: {"references": [REF]}
    m._step2_reference_audit = lambda cfg, pd_: {
        "project_reference": "", "recoverable": [], "removable": [], "orphans": [], "mixed": False}
    m.build_env = lambda cfg: {}
    m.job_manager.start_job = lambda name, command, cwd=None, **kw: commands.append(command) or "job-stub"
    m.provenance_writer.dispatch_step2 = lambda *a, **k: (None, None)
    try:
        res, err = call(m.step2_run, "p4", m.Step2Request(
            reference=REF, include=samples, build_exclude=[], step1_exclude=[]))
        argv = shlex.split(commands[0]) if commands else []
        check("the command carries -t <folder>",
              err is None and "-t" in argv and argv[argv.index("-t") + 1] == str(fx.folder))
        runs_before = sorted(p.name for p in (project / "step2").iterdir())
        m.reference_lock = lambda p: {"references": []}
        res, err = call(m.step2_run, "p4", m.Step2Request(
            reference="Not_A_Reference", include=samples, build_exclude=[], step1_exclude=[]))
        check("an unknown reference is refused (400) before any run folder is made",
              err is not None and err[0] == 400
              and sorted(p.name for p in (project / "step2").iterdir()) == runs_before)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        m.job_manager.start_job, m.provenance_writer.dispatch_step2 = saved_start, saved_dispatch


def test_stats_cell(fx: Fixture) -> None:
    print("[the stats Reference cell reads back as the name]")
    alias = {}
    for raw, want in ((f"{fx.folder}", REF),
                      (f"{fx.first}/mtbc0_v1.1", "mtbc0_v1.1"),
                      (f"{fx.folder}/", REF),
                      (REF, REF)):
        check(f"{raw!r} -> {want}", m._stats_reference_name(raw, alias) == want)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ref_folder_arg_")).resolve()
    fx = Fixture(tmp)
    saved = {k: getattr(m, k) for k in ("load_config", "wrap_cmd", "build_env")}
    saved_start = m.job_manager.start_job
    saved_prov = m.provenance_writer.dispatch_step1_batch
    m.load_config = lambda: dict(fx.cfg)
    m.wrap_cmd = lambda cfg, cmd: cmd
    m.build_env = lambda cfg: {}
    m.job_manager.start_job = lambda **kw: fx.started.append(kw) or "job-stub"
    m.provenance_writer.dispatch_step1_batch = lambda *a, **k: (None, {})
    try:
        test_step1(fx)
        test_step2(fx)
        test_stats_cell(fx)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        m.job_manager.start_job = saved_start
        m.provenance_writer.dispatch_step1_batch = saved_prov
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll reference folder tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
