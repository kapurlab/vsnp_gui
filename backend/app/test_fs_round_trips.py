"""Project switching on shared storage: fewer round trips, made at once.

Switching to a 9,000-sample project took one to two minutes on the Ames HPC.
Every Step 1 / Step 2 request the switch fires walked the project one sample at
a time, and several walked each sample more than once: the status list globbed
each sample's alignment dirs four times, the edits list globbed and stat()ed
every sample to find the handful with a vcf_edits/, the sample browser listed
each dir twice on the common `_2.fastq.gz` naming. On a network filesystem each
of those is a round trip, and made in series they add up to minutes.

The rewrite keeps every answer and changes only how it is reached: one listing
per directory, per-sample work fanned out across a shared pool (fanout.py), and
work skipped where its result cannot be used. These tests hold both halves:

  * the answers are the old ones — each new path is checked against the code
    it replaced, kept here as an oracle, on layouts chosen to be awkward;
  * the round trips stay down — call budgets per sample, so a later change
    cannot quietly bring the per-sample walks back.

Run directly:  python test_fs_round_trips.py
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as m
from app import fanout
import qc_scan

FAILURES = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


class Calls:
    """Counts os.stat / os.scandir / os.listdir calls, in every thread."""

    def __init__(self):
        self.n = 0
        self._lock = threading.Lock()
        self._saved = {}

    def __enter__(self):
        for name in ("stat", "lstat", "scandir", "listdir"):
            real = getattr(os, name)
            self._saved[name] = real

            def counted(*a, _real=real, **k):
                with self._lock:
                    self.n += 1
                return _real(*a, **k)
            setattr(os, name, counted)
        return self

    def __exit__(self, *exc):
        for name, real in self._saved.items():
            setattr(os, name, real)


# --- oracles: the implementations these replaced, verbatim -----------------

def old_browser_samples(step1_dir: Path):
    out = []
    if not step1_dir.is_dir():
        return out
    for p in sorted(step1_dir.iterdir()):
        if not p.is_dir() or p.name.startswith(("_", ".")):
            continue
        has_r2 = any(p.glob("*_R2*.fastq.gz")) or any(p.glob("*_2*.fastq.gz"))
        out.append({"sample": p.name, "is_pair": bool(has_r2)})
    return out


def old_edits(step1_dir: Path):
    edits = {}
    for sample_dir in sorted(step1_dir.glob("*")):
        if not sample_dir.is_dir():
            continue
        sample = sample_dir.name
        vcf_candidates = sorted(
            m._align_glob(sample_dir, f"{sample}*zc.vcf*"), key=lambda p: p.stat().st_mtime
        )
        source_vcf = vcf_candidates[-1] if vcf_candidates else None
        patched_vcf = m._find_patched_vcf(sample_dir, sample, source_vcf)
        edit_log = m._edit_log_path(sample_dir, sample)
        if patched_vcf or edit_log.exists():
            edits[sample] = {
                "patched_vcf": str(patched_vcf) if patched_vcf else "",
                "edit_log": str(edit_log) if edit_log.exists() else "",
                "edited": bool(patched_vcf) and edit_log.exists()
            }
    return edits


def old_alias_map(vsnp3_path: Path):
    import re
    aliases = {}

    def _add(key, name):
        if not key:
            return
        existing = aliases.get(key)
        if existing is None:
            aliases[key] = name
            return
        key_lc = key.lower()
        if key_lc in name.lower() and key_lc not in existing.lower():
            aliases[key] = name

    for ref in m.list_references(vsnp3_path):
        name = ref.get("name")
        base = Path(ref.get("path", ""))
        if not name or not base.exists():
            continue
        for ext in (".fa", ".fasta", ".fna", ".fas"):
            for fasta in base.rglob(f"*{ext}"):
                stem = fasta.stem
                _add(stem, name)
                stripped = re.sub(r"\.\d+$", "", stem)
                if stripped != stem:
                    _add(stripped, name)
                root = fasta.name.split(".", 1)[0]
                if root and root != stem:
                    _add(root, name)
            if name in aliases.values():
                break
    return aliases


def old_stats_files(step1_dir: str, include_direct: bool):
    patterns = [os.path.join(step1_dir, "*", "*_stats.xlsx")]
    if include_direct:
        patterns.insert(0, os.path.join(step1_dir, "*_stats.xlsx"))
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    out = {}
    for f in sorted(set(files)):
        try:
            st = os.stat(f)
        except OSError:
            continue
        out[f] = [st.st_mtime_ns, st.st_size]
    return out


# --- an awkward step1 --------------------------------------------------------

def touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def awkward_step1(root: Path) -> Path:
    s1 = root / "step1"
    touch(s1 / "A" / "A_R1_001.fastq.gz"); touch(s1 / "A" / "A_R2_001.fastq.gz")
    touch(s1 / "A" / "alignment_REF" / "A_zc.vcf")
    touch(s1 / "A" / "alignment_REF" / "A_filtered_hapall_annotated.vcf")
    touch(s1 / "A" / "alignment_REF" / "A_nodup.bam")
    touch(s1 / "A" / "A_2025-01-01_stats.xlsx")
    touch(s1 / "B" / "B_1.fastq.gz"); touch(s1 / "B" / "B_2.fastq.gz")      # `_2` naming
    touch(s1 / "B" / "alignment" / "B_zc.vcf")                               # legacy layout
    touch(s1 / "C" / "C_r2.fastq.gz")                                        # lower case: no match
    touch(s1 / "D" / ".D_R2_.fastq.gz")                                      # dotfile: matches, as glob did
    touch(s1 / "E" / "alignment_bogus")                                      # an alignment_* FILE
    touch(s1 / "E" / "alignment_X" / "E_zc.vcf.gz")                          # only the .gz
    touch(s1 / "E" / "alignment_Y" / "E_zc.vcf")
    os.symlink("/nonexistent/log", s1 / "E" / "run_step1.log")               # dangling log
    touch(s1 / "F" / "run_step1.log")
    touch(s1 / "F" / "vcf_edits" / "F_patchlog.jsonl")                       # edit log only
    touch(s1 / "G" / "alignment_REF" / "G_zc.vcf")
    touch(s1 / "G" / "vcf_edits" / "G_zc.vcf.gz")                            # patched VCF
    touch(s1 / "G" / "vcf_edits" / "G_patchlog.jsonl")
    touch(s1 / "H" / "vcf_edits", b"a file, not a dir")
    touch(s1 / "H" / ".hidden_stats.xlsx")                                   # glob skips dotfiles
    touch(root / "outside" / "L" / "L_R2.fastq.gz")
    touch(root / "outside" / "L" / "alignment_REF" / "L_zc.vcf")
    os.symlink(root / "outside" / "L", s1 / "L")                             # symlinked sample
    (s1 / "_provenance").mkdir(); (s1 / ".hidden").mkdir()
    touch(s1 / "stray.txt"); touch(s1 / "top_stats.xlsx")
    return s1


def test_fanout():
    print("fan_out")
    check(fanout.fan_out(lambda x: x * 2, range(500)), [x * 2 for x in range(500)], "results keep their order")
    names = set()
    fanout.fan_out(lambda x: names.add(threading.current_thread().name), range(200))
    check(any(n.startswith("fs-fanout") for n in names), True, "the calls run on the shared pool")
    # A task that fans out again must not wait on the pool it is running in.
    nested = fanout.fan_out(lambda x: sum(fanout.fan_out(lambda y: y, range(x))), range(64))
    check(nested, [sum(range(x)) for x in range(64)], "a nested fan-out runs in place, no deadlock")
    try:
        fanout.fan_out(lambda x: 1 // (x - 7), range(20))
        check("no exception", "ZeroDivisionError", "an exception reaches the caller")
    except ZeroDivisionError:
        check(True, True, "an exception reaches the caller")


def test_align_listing(s1: Path):
    print("_AlignListing against _align_glob")
    patterns = ["*_filtered_hapall_annotated.vcf", "*_nodup.bam", "*_zc.vcf", "*_zc.vcf.gz", "*zc.vcf*"]
    for d in sorted(p for p in s1.iterdir() if p.is_dir()):
        listing = m._AlignListing(d)
        for pat in patterns:
            check(listing.glob(pat), m._align_glob(d, pat), f"{d.name}: {pat}")
        check(listing.exists("run_step1.log"), (d / "run_step1.log").exists(), f"{d.name}: run_step1.log exists")


def test_listings(s1: Path):
    print("sample browser, Results scan discovery")
    check(m._step1_browser_samples(s1), old_browser_samples(s1), "the sample browser list is unchanged")
    check(qc_scan._stats_files(str(s1), False), old_stats_files(str(s1), False),
          "the Results scan finds the same workbooks, with the same stats")
    check(qc_scan._stats_files(str(s1), True), old_stats_files(str(s1), True),
          "and the same with top-level workbooks included")


def test_endpoints(root: Path, s1: Path):
    print("endpoints on the awkward project")
    proj = s1.parent
    (proj / "project.json").write_text(json.dumps({"name": "p", "reference": "REF"}))
    cfg = {"vsnp3_path": str(root / "vsnp3"), "projects_root": str(root)}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda c, p: proj
    check(m.step1_edits("p"), old_edits(s1), "the edits list is unchanged")
    status = m.step1_status("p")["samples"]
    check([s["sample"] for s in status], ["A", "B", "C", "D", "E", "F", "G", "H", "L"],
          "the status list has every sample, in order, and nothing else")
    by = {s["sample"]: s for s in status}
    check(by["B"]["status"], "complete", "a legacy alignment/ sample reads as complete")
    check(by["E"]["has_log"], False, "a dangling run_step1.log is not a log")
    check(by["E"]["has_zc_vcf"], True, "a zc VCF under any alignment_* dir is found")
    check(by["F"]["status"], "unknown", "a log with no outputs and no job reads as unknown")


def test_alias_map(root: Path):
    print("reference alias map")
    refs = root / "refs"
    touch(refs / "mtbc0_v1.1" / "MTBC0_v1.1.fasta", b">c\nA\n")
    touch(refs / "mtbc0_v1.1" / "deep" / "er" / "MTBC0_v1.1_masked.fa", b">c\nA\n")
    touch(refs / "Brucella_abortus1" / "NC_006932.fna", b">c\nA\n")
    touch(refs / "Brucella_abortus1" / "NC_006932.fasta", b">c\nA\n")
    touch(refs / "Brucella_abortus10" / "NZ_CP007682.1.fasta", b">c\nA\n")
    touch(refs / "shared" / "x.fas", b">c\nA\n")
    os.symlink(refs / "shared", refs / "mtbc0_v1.1" / "linked")          # not descended, as rglob
    vsnp3 = root / "vsnp3"
    touch(vsnp3 / "dependencies" / "reference_options_paths.txt", f"{refs}\n".encode())
    check(m._build_reference_alias_map(vsnp3), old_alias_map(vsnp3), "the map is the one rglob built")
    first = m._reference_alias_map(vsnp3)
    check("NZ_CP007682" in first, True, "the memoised map is complete")
    time.sleep(0.01)
    touch(refs / "new_ref" / "NEW.fasta", b">c\nA\n")                    # changes the root's mtime
    check("NEW" in m._reference_alias_map(vsnp3), True, "a new reference is seen at once")


def test_budgets(root: Path):
    """Round trips per sample, measured, on a warm backend."""
    print("round-trip budgets")
    proj = root / "budget"
    s1 = proj / "step1"
    n = 60
    for i in range(n):
        d = s1 / f"S{i:03d}"
        touch(d / f"S{i:03d}_1.fastq.gz"); touch(d / f"S{i:03d}_2.fastq.gz")
        a = d / "alignment_REF"
        for f in ("_zc.vcf", "_filtered_hapall_annotated.vcf", "_nodup.bam"):
            touch(a / f"S{i:03d}{f}")
        touch(d / ".provenance" / "exit_code", b"0\n")
    (proj / "step2" / "vcf_database").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"name": "budget", "reference": "REF"}))
    m._project_dir_for = lambda c, p: proj
    with Calls() as cold:
        m.step1_status("budget")
    with Calls() as warm:
        m.step1_status("budget")
    with Calls() as edits:
        m.step1_edits("budget")
    with Calls() as browser:
        m.step1_samples("budget")
    print(f"       per sample: status cold {cold.n / n:.1f}, warm {warm.n / n:.1f}, "
          f"edits {edits.n / n:.1f}, browser {browser.n / n:.1f}")
    # Was ~11, ~1, ~6 and 3 (2 on R2 naming) calls per sample.
    check(cold.n <= 5 * n + 20, True, "status, first visit: at most 5 calls a sample")
    check(warm.n <= 1 * n + 20, True, "status, revisit: 1 call a sample")
    check(edits.n <= 1 * n + 20, True, "edits: 1 call a sample")
    check(browser.n <= 1 * n + 20, True, "sample browser: 1 call a sample")


class Opens:
    """Counts files opened under a directory, in every thread."""

    def __init__(self, under: Path):
        import builtins
        self._b = builtins
        self.under = str(under)
        self.n = 0
        self._lock = threading.Lock()

    def __enter__(self):
        real = self._real = self._b.open

        def counted(file, *a, **k):
            if isinstance(file, (str, Path)) and str(file).startswith(self.under):
                with self._lock:
                    self.n += 1
            return real(file, *a, **k)
        self._b.open = counted
        return self

    def __exit__(self, *exc):
        self._b.open = self._real


def test_fanout_errors():
    print("fan_out error handling")
    ran = []

    def work(x):
        if x == 3:
            raise ValueError("three")
        time.sleep(0.002)
        ran.append(x)
        return x
    try:
        fanout.fan_out(work, range(200))
        check("no error", "ValueError", "the first error reaches the caller")
    except ValueError:
        check(True, True, "the first error reaches the caller")
        settled = len(ran)
        time.sleep(0.05)
        check(len(ran), settled, "and nothing is still running once it does")


def test_status_survives_restart(root: Path):
    """A fresh backend (every OOD launch) reads what the last one learned."""
    print("Step 1 status across a backend restart")
    proj = root / "restart"
    s1 = proj / "step1"
    n = 40
    for i in range(n):
        d = s1 / f"S{i:03d}"
        touch(d / f"S{i:03d}_1.fastq.gz"); touch(d / f"S{i:03d}_2.fastq.gz")
        for f in ("_zc.vcf", "_filtered_hapall_annotated.vcf", "_nodup.bam"):
            touch(d / "alignment_REF" / f"S{i:03d}{f}")
        touch(d / ".provenance" / "exit_code", b"0\n")
    old = time.time() - 60   # past the racy-timestamp guard
    for p in sorted(s1.rglob("*"), key=lambda p: -len(p.parts)):
        os.utime(p, (old, old), follow_symlinks=False)
    (proj / "step2" / "vcf_database").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"name": "restart", "reference": "REF"}))
    m._project_dir_for = lambda c, p: proj
    first = m.step1_status("restart")["samples"]
    check((s1 / m._STEP1_STATUS_DISK_BASENAME).exists(), True, "the answers are written beside the samples")

    def restart():
        m._STEP1_STATUS_CACHE.clear()
        m._STEP1_STATUS_DISK_MEMO.clear()

    restart()
    with Calls() as calls, Opens(s1) as opens:
        again = m.step1_status("restart")["samples"]
    check(again, first, "a fresh backend reports exactly what the last one did")
    check(opens.n <= 2, True, f"without opening the samples' files ({opens.n} opens)")
    # Counted here: the exit_code stat. Not visible to this counter: the stat
    # of each sample dir's own entry, which is the second key.
    check(calls.n <= 2 * n + 10, True, "at most two stats a sample")

    # A change made while no backend was watching must not be answered from disk.
    shutil.rmtree(s1 / "S005" / "alignment_REF")
    touch(s1 / "S007" / ".provenance" / "exit_code", b"1\n")
    restart()
    by = {e["sample"]: e for e in m.step1_status("restart")["samples"]}
    check(by["S005"]["has_outputs"], False, "deleted outputs are noticed (the dir's mtime moved)")
    check(by["S007"]["status"], "error", "a rewritten exit_code is noticed")

    # A damaged file is ignored, not trusted.
    (s1 / m._STEP1_STATUS_DISK_BASENAME).write_text(json.dumps(
        {"version": m._STEP1_STATUS_DISK_VERSION, "samples": {"S001": [1, 2, {"status": 5}]}}))
    restart()
    by = {e["sample"]: e for e in m.step1_status("restart")["samples"]}
    check(by["S001"]["status"], "complete", "a damaged entry is recomputed")


def test_staging(root: Path):
    print("Step 2 staging")
    from app.step2_staging import stage_step2_vcfs
    db = root / "stage_db"
    for i in range(50):
        touch(db / f"V{i:02d}_zc.vcf", f"v{i}".encode())
    run = root / "stage_run"
    run.mkdir()
    copied, skipped, staged = stage_step2_vcfs(db, run, include_samples=[f"V{i:02d}" for i in range(50)])
    check((copied, skipped, len(staged)), (50, 0, 50), "every chosen VCF is staged")
    check(sorted(p.read_bytes() for p in run.iterdir()), sorted(f"v{i}".encode() for i in range(50)),
          "with its own content")


def test_import_reads(root: Path):
    """Each incoming VCF is read once for its header, once to be copied."""
    print("import-vcfs reads")
    proj = root / "imp"
    (proj / "step2" / "vcf_database").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"name": "imp", "reference": "REF"}))
    src = root / "imp_src"
    n = 30
    for i in range(n):
        touch(src / f"X{i:02d}_zc.vcf", b"##fileformat=VCFv4.2\n##reference=/r/REF.fasta\n#CHROM\n")
    cfg = {"vsnp3_path": str(root / "vsnp3"), "projects_root": str(root)}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda c, p: proj
    with Opens(src) as opens:
        r = m.project_import_vcfs("imp", m.ImportVcfRequest(source_paths=[str(src)], reference="REF"))
    check(r["imported"], n, "all of them import")
    check(opens.n, 2 * n, "two opens a VCF: the header and the copy (was three)")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="fs_round_trips_"))
    try:
        test_fanout()
        s1 = awkward_step1(tmp / "p")
        test_align_listing(s1)
        test_listings(s1)
        test_alias_map(tmp)
        test_endpoints(tmp, s1)
        test_budgets(tmp)
        test_fanout_errors()
        test_status_survives_restart(tmp)
        test_staging(tmp)
        test_import_reads(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("\nall passed")


if __name__ == "__main__":
    main()
