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
from app import projects as pj
from app import step1_index as si
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


def old_sample_names(step1_dir: Path):
    try:
        with os.scandir(step1_dir) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.name.startswith(("_", ".")):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        has_fastq = False
        try:
            with os.scandir(entry.path) as files:
                for f in files:
                    if f.name.endswith(".fastq.gz") and not f.name.startswith("."):
                        has_fastq = True
                        break
        except OSError:
            has_fastq = False
        if has_fastq:
            out.append(entry.name)
    return out


def old_scan_step1(step1_dir: Path, seen: set) -> int:
    if not step1_dir.is_dir():
        return 0
    sample_dirs = []
    try:
        with os.scandir(step1_dir) as it:
            for entry in it:
                if entry.name.startswith(("_", ".")):
                    continue
                if entry.is_dir():
                    sample_dirs.append((entry.path, entry.is_symlink()))
    except OSError:
        return 0
    step1_dev = pj._dir_device(step1_dir)
    for sample_path, is_link in sample_dirs:
        dev = pj._dir_device(Path(sample_path)) if is_link else step1_dev
        try:
            with os.scandir(sample_path) as it:
                for entry in it:
                    if pj._is_read_file(entry.name):
                        pj._add_read_identity(entry, dev, seen)
        except OSError:
            pass
    return len(sample_dirs)


def old_group(d: Path, project: str):
    """step2_outputs' per-group loop body as it was: three globs, two listings,
    a stat per file."""
    def _safe_name(value):
        return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)
    fasta_path = m._find_group_fasta(d)
    fasta_count, fasta_columns, fasta_has_outgroup = m._fasta_dims(fasta_path)
    sample_count = fasta_count - 1 if fasta_has_outgroup else fasta_count
    labeled_bases = {
        f.name.removesuffix("_labeled.tre") for f in d.iterdir()
        if f.is_file() and f.name.endswith("_labeled.tre")
    }
    files = []
    for f in sorted(d.iterdir()):
        if f.is_file():
            if f.name.endswith(".tre") and not f.name.endswith("_labeled.tre"):
                if f.name.removesuffix(".tre") in labeled_bases:
                    continue
            ext = f.suffix.lstrip(".")
            files.append({"label": f.name, "path": str(f), "type": ext or "file",
                          "download_name": f"{_safe_name(project)}__{_safe_name(d.name)}__{f.name}"})
        elif f.is_dir() and f.name == "posthoc":
            for pf in sorted(f.iterdir()):
                if pf.is_file():
                    ext = pf.suffix.lstrip(".")
                    files.append({"label": f"posthoc/{pf.name}", "path": str(pf), "type": ext or "file",
                                  "download_name": f"{_safe_name(project)}__{_safe_name(d.name)}__posthoc__{pf.name}"})
    if not files:
        return None
    return {"name": d.name, "files": files, "posthoc_possible": fasta_count >= 3,
            "posthoc_reason": "" if fasta_count >= 3 else "Requires a FASTA with at least 3 sequences",
            "posthoc_sequence_count": fasta_count, "sample_count": sample_count, "snp_count": fasta_columns}


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
    touch(root / "dl" / "M_R1.fastq.gz"); touch(root / "dl" / "M_R2.fastq.gz")
    touch(s1 / "M" / "M_2025_stats.xlsx")
    os.symlink(root / "dl" / "M_R1.fastq.gz", s1 / "M" / "M_R1.fastq.gz")    # reads linked from download/
    os.symlink(root / "dl" / "M_R2.fastq.gz", s1 / "M" / "M_R2.fastq.gz")
    touch(s1 / "N" / "N_unmapped_R1.fastq.gz")                               # not a read
    os.symlink("/nonexistent/N_R1.fastq.gz", s1 / "N" / "N_R1.fastq.gz")      # a dangling read
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
    print("sample browser, Results scan discovery, sample names, card reads: from the index")
    si.invalidate(s1)
    check(m._step1_browser_samples(s1), old_browser_samples(s1), "the sample browser list is unchanged")
    check(qc_scan._stats_files(str(s1), False), old_stats_files(str(s1), False),
          "the Results scan finds the same workbooks, with the same stats")
    check(qc_scan._stats_files(str(s1), True), old_stats_files(str(s1), True),
          "and the same with top-level workbooks included")
    check(si.stats_sigs(s1), old_stats_files(str(s1), False),
          "the index hands the scan the same workbooks and stats")
    check(m._step1_sample_names(s1), old_sample_names(s1), "the sample names are unchanged")
    seen_old, seen_new = set(), set()
    check((pj._scan_step1(s1, seen_new), seen_new), (old_scan_step1(s1, seen_old), seen_old),
          "the card's sample count and read identities are unchanged")
    check(any(isinstance(x, str) for x in seen_new), True, "a dangling read still counts, by its path")
    check(si.listing(s1) is si.listing(s1), True, "one listing serves the requests of one moment")


def awkward_group(root: Path) -> Path:
    g = root / "run" / "G1"
    touch(g / "G1-2026.fasta", b">root\nACGT\n>a\nACGT\n>b\nACGT\n")
    touch(g / ".hidden.fasta", b">x\nA\n")                     # pathlib's glob sees dotfiles
    touch(g / "z.fa"); touch(g / "weird.fna")                   # lose to the .fasta
    touch(g / "G1-2026.tre"); touch(g / "G1-2026_labeled.tre") # labeled hides its base
    touch(g / "other.tre")                                     # no labeled sibling: shown
    os.symlink("/nonexistent/dead.tre", g / "dead.tre")        # dangling: not a file
    touch(root / "elsewhere.xlsx")
    os.symlink(root / "elsewhere.xlsx", g / "link.xlsx")       # a link to a file: a file
    touch(g / "posthoc" / "kdp.png"); (g / "posthoc" / "deep").mkdir()
    touch(g / "posthoc" / "stats.json", b'{"status": "error", "message": "boom"}')
    (g / "notposthoc").mkdir(); touch(g / "notposthoc" / "x.txt")
    touch(g / "snp_matrix.csv"); touch(g / "closest_neighbor.png")
    touch(g / ".snp_analysis.lock", b"job-that-no-longer-exists")
    return g


def test_group_listing(root: Path):
    print("Step 2 group folders: one listing instead of sixteen calls")
    g = awkward_group(root / "grp")
    # The old loop is the oracle; step2_outputs' _group is reached through the
    # endpoint, so the comparison runs the endpoint on a run holding the group.
    proj = root / "grp"
    (proj / "step2").mkdir()
    os.rename(root / "grp" / "run", proj / "step2" / "2026-01-01_00-00-00")
    (proj / "project.json").write_text(json.dumps({"name": "grp", "reference": "REF"}))
    m._project_dir_for = lambda c, p: proj
    d = proj / "step2" / "2026-01-01_00-00-00" / "G1"
    expected = old_group(d, "grp")
    with Calls() as calls:
        got = m.step2_outputs("grp", "2026-01-01_00-00-00")["groups"]
    check(got, [expected], "the group's files, fasta and shape are unchanged")
    check(calls.n <= 12, True, f"and cost a handful of calls, not one per file ({calls.n})")
    tool = m.posthoc_get_tool("snp_analysis")
    # The post-hoc state, from a listing and from the disk, on two copies of
    # the folder (clearing a stale lock is a side effect).
    twin = proj / "twin"
    shutil.copytree(d, twin, symlinks=True)
    lock = m._posthoc_lock_path(d, tool.tool_id)
    m._posthoc_clear_stale_lock(lock)
    old_state = m._posthoc_group_state(d, tool, lock)
    gd = m._GroupFiles(twin)
    lock2 = m._posthoc_lock_path(twin, tool.tool_id)
    with Calls() as calls:
        m._posthoc_clear_stale_lock(lock2, gd)
        new_state = m._posthoc_group_state(twin, tool, lock2, gd)
    fix = lambda st: json.loads(json.dumps(st).replace(str(twin), str(d)))
    check(fix(new_state), old_state, "the post-hoc state is unchanged (legacy posthoc/ included)")
    check(lock2.exists(), False, "and the stale lock is still cleared")
    check(calls.n <= 6, True, f"from one listing of the folder ({calls.n} calls)")


def test_endpoints(root: Path, s1: Path):
    print("endpoints on the awkward project")
    proj = s1.parent
    (proj / "project.json").write_text(json.dumps({"name": "p", "reference": "REF"}))
    cfg = {"vsnp3_path": str(root / "vsnp3"), "projects_root": str(root)}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda c, p: proj
    check(m.step1_edits("p"), old_edits(s1), "the edits list is unchanged")
    status = m.step1_status("p")["samples"]
    check([s["sample"] for s in status], ["A", "B", "C", "D", "E", "F", "G", "H", "L", "M", "N"],
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
    old = time.time() - 60   # past the racy-timestamp guard, so the index records them
    for p in sorted(s1.rglob("*"), key=lambda p: -len(p.parts)):
        os.utime(p, (old, old), follow_symlinks=False)
    si.invalidate(s1)
    with Calls() as cold:
        m.step1_status("budget")
    with Calls() as warm:
        m.step1_status("budget")
    # The index is built once per project, ever: one listing a sample.
    with Calls() as build:
        si.facts(s1)
    with Calls() as edits:
        m.step1_edits("budget")
    with Calls() as browser:
        m.step1_samples("budget")
    with Calls() as names:
        m._step1_sample_names(s1)
    with Calls() as card:
        pj._scan_step1(s1, set())
    print(f"       per sample: status never seen {cold.n / n:.1f}, warm {warm.n / n:.1f}, "
          f"index build {build.n / n:.1f}, edits {edits.n / n:.1f}, browser {browser.n / n:.1f}, "
          f"names {names.n / n:.1f}, card {card.n / n:.1f}")
    # Was ~11, ~1, ~6 and 3 (2 on R2 naming) calls per sample; then 5, 1, 1, 1.
    check(cold.n <= 5 * n + 20, True, "status, never seen: at most 5 calls a sample")
    check(warm.n <= 20, True, "status, revisit within the listing's life: no call a sample")
    check(build.n <= 1 * n + 20, True, "building the index: 1 listing a sample")
    # The listing made for the status call, and the index, answer the rest.
    check(edits.n <= 20, True, "edits: no call per sample")
    check(browser.n <= 20, True, "sample browser: no call per sample")
    check(names.n <= 20, True, "sample names: no call per sample")
    check(card.n <= 20, True, "project card: no call per sample")


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
        si._LISTINGS.clear()

    restart()
    with Calls() as calls, Opens(s1) as opens:
        again = m.step1_status("restart")["samples"]
    check(again, first, "a fresh backend reports exactly what the last one did")
    check(opens.n <= 2, True, f"without opening the samples' files ({opens.n} opens)")
    # Counted here: the stat of each sample dir, the one key.
    check(calls.n <= 1 * n + 20, True, f"one stat a sample ({calls.n} calls)")

    # A change made while no backend was watching must not be answered from
    # disk. A finished run moves the directory's mtime: a successful one by
    # writing a new stats workbook, and the batch touches the directory after
    # writing exit_code so a failed re-run is seen too (test below).
    shutil.rmtree(s1 / "S005" / "alignment_REF")
    touch(s1 / "S007" / ".provenance" / "exit_code", b"1\n")
    os.utime(s1 / "S007")
    restart()
    by = {e["sample"]: e for e in m.step1_status("restart")["samples"]}
    check(by["S005"]["has_outputs"], False, "deleted outputs are noticed (the dir's mtime moved)")
    check(by["S007"]["status"], "error", "a rewritten exit_code is noticed once the batch touches the dir")
    src = Path(m.__file__).read_text(encoding="utf-8")
    check('"  touch . 2>/dev/null || true",' in src, True, "and the batch does touch it after exit_code")

    # A damaged file is ignored, not trusted.
    (s1 / m._STEP1_STATUS_DISK_BASENAME).write_text(json.dumps(
        {"version": m._STEP1_STATUS_DISK_VERSION, "samples": {"S001": [1, {"status": 5}]}}))
    restart()
    by = {e["sample"]: e for e in m.step1_status("restart")["samples"]}
    check(by["S001"]["status"], "complete", "a damaged entry is recomputed")


def test_index_survives_restart(root: Path):
    """A fresh backend answers the sample-directory questions from the index,
    with one stat per sample, and notices what changed while it was away."""
    print("the Step 1 index across a backend restart")
    proj = root / "index"
    s1 = proj / "step1"
    n = 40
    for i in range(n):
        d = s1 / f"S{i:03d}"
        touch(d / f"S{i:03d}_1.fastq.gz")
        if i % 3:
            touch(d / f"S{i:03d}_2.fastq.gz")
        touch(d / f"S{i:03d}_2026-01-01_stats.xlsx", b"x" * (10 + i))
        touch(d / "alignment_REF" / f"S{i:03d}_zc.vcf")
        touch(d / ".provenance" / "exit_code", b"0\n")
    old = time.time() - 60
    for p in sorted(s1.rglob("*"), key=lambda p: -len(p.parts)):
        os.utime(p, (old, old), follow_symlinks=False)
    (proj / "step2" / "vcf_database").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"name": "index", "reference": "REF"}))
    m._project_dir_for = lambda c, p: proj

    def answers():
        seen: set = set()
        return (m._step1_sample_names(s1), m._step1_browser_samples(s1), m.step1_edits("index"),
                (pj._scan_step1(s1, seen), seen), si.stats_sigs(s1))

    def restart():
        si._LISTINGS.clear()
        si._FACTS_MEMO.clear()

    si.invalidate(s1)
    first = answers()
    check((s1 / si.INDEX_BASENAME).exists(), True, "the answers are written beside the samples")
    check(len(first[0]), n, "every sample has reads")
    check(sum(1 for b in first[1] if b["is_pair"]), n - len(range(0, n, 3)), "pairs are told from singles")
    restart()
    with Calls() as calls:
        again = answers()
    check(again, first, "a fresh backend gives exactly the answers the last one did")
    check(calls.n <= n + 20, True, f"for one stat a sample and no listing of any of them ({calls.n} calls)")

    # Changes made while no backend was watching: each moves its directory's mtime.
    touch(s1 / "S005" / "vcf_edits" / "S005_patchlog.jsonl")
    os.unlink(s1 / "S007" / "S007_2026-01-01_stats.xlsx")
    touch(s1 / "S009" / "S009_2.fastq.gz")
    shutil.rmtree(s1 / "S011")
    touch(s1 / "S099" / "S099_1.fastq.gz")
    restart()
    names, browser, edits, (count, _seen), sigs = answers()
    check("S005" in edits, True, "an edit folder that appeared is reported")
    check(any(str(s1 / "S007") in k for k in sigs), False, "a stats workbook that went is gone from the scan")
    check(next(b["is_pair"] for b in browser if b["sample"] == "S009"), True, "a read that arrived makes a pair")
    check("S011" in names, False, "a removed sample is gone")
    check(("S099" in names, count), (True, n), "an added sample is counted")

    # A damaged index is ignored, not trusted; an index written by another
    # version likewise.
    (s1 / si.INDEX_BASENAME).write_text(json.dumps({"version": si._INDEX_VERSION,
                                                      "samples": {"S001": [1, {"fq": [], "stats": {}, "edits": True}]}}))
    restart()
    check("S001" in m._step1_sample_names(s1), True, "a damaged entry is recomputed")
    (s1 / si.INDEX_BASENAME).write_text("not json")
    restart()
    check(answers()[0], names, "an unreadable index is rebuilt")


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
        test_group_listing(tmp)
        test_index_survives_restart(tmp)
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
