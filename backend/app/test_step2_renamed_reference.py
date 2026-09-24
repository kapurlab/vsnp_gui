"""A renamed copy of the reference FASTA is the same reference.

The Ames owl project's reference is owl_25-003495-001, whose FASTA is
owl_25-003495-001.fasta. 3,204 of its samples had been called in earlier runs
against 25-003495-001.fasta: the same eight segments, the same names, the same
lengths, saved under another file name. The GUI decided a VCF's reference by the
file name alone (the ##reference= header, the alignment_<name>/ dir), so every
one of them was reported as a foreign reference: Step 2 was disabled, Build left
them out, and the page offered to drop them from the comparison set.

What decides whether positions can be compared is the coordinate system, and a
VCF states that too: one ##contig=<ID=..,length=..> line per sequence. These
tests pin the rule that a VCF named for another FASTA whose contigs are EXACTLY
the reference's is that reference, and that anything short of exact, and
anything unstated, still is not. They also pin the cost: each file is read at
most once, and a warm audit reads nothing at all.

Run directly:  python test_step2_renamed_reference.py
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as m
from app import ref_contigs

FAILURES = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


PROJ_REF = "owl_25-003495-001"
RENAMED = "25-003495-001"          # what the older runs' FASTA was called
OTHER_REF = "chicken_24-000001-001"

# The real owl segment names and lengths, straight from the VCF headers.
SEGMENTS = [("PB2", 2280), ("PB1", 2274), ("PA", 2151), ("HA", 1704),
            ("NP", 1497), ("NA", 1410), ("M", 982), ("NS", 838)]
OWL = [(f"A/owl/CA/25-003495-001/2024_{s}", n) for s, n in SEGMENTS]
CHICKEN = [(f"A/chicken/PA/24-000001-001/2024_{s}", n + 3) for s, n in SEGMENTS]


def write_fasta(path: Path, contigs, width: int = 60) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for name, n in contigs:
            fh.write(f">{name} segment\n")
            seq = "ACGT" * (n // 4) + "ACGT"[: n % 4]
            for i in range(0, n, width):
                fh.write(seq[i:i + width] + "\n")


def write_vcf(path: Path, fasta_name: str, contigs, pad: str, gz: bool = True) -> Path:
    """A freebayes-shaped zero-coverage VCF header, uniquely sized by `pad`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (
        "##fileformat=VCFv4.2\n"
        "##fileDate=20250423\n"
        "##source=freeBayes v1.3.9\n"
        f"##reference=/home/someone/2025-04-23/dir01/{path.parent.parent.name}/{fasta_name}.fasta\n"
        + "".join(f"##contig=<ID={c},length={n}>\n" for c, n in (contigs or []))
        + "##phasing=none\n"
        f"##padding={pad}\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tunknown\n"
    )
    if gz:
        with gzip.open(path, "wt") as fh:
            fh.write(head)
    else:
        path.write_text(head)
    return path


def build_refs(root: Path) -> Path:
    vsnp3 = root / "vsnp3"
    refs = root / "refs"
    write_fasta(refs / PROJ_REF / f"{PROJ_REF}.fasta", OWL)
    write_fasta(refs / OTHER_REF / f"{OTHER_REF}.fasta", CHICKEN)
    deps = vsnp3 / "dependencies"
    deps.mkdir(parents=True)
    (deps / "reference_options_paths.txt").write_text(str(refs) + "\n")
    return vsnp3


def build_project(root: Path):
    proj = root / "projects" / "owl"
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"reference": PROJ_REF}))
    s1 = proj / "step1"
    src = {}
    # Called against the project's reference under its own name.
    src["OWN"] = write_vcf(s1 / "OWN" / f"alignment_{PROJ_REF}" / "OWN_zc.vcf.gz", PROJ_REF, OWL, "a")
    # The reported case: the same FASTA saved as 25-003495-001.fasta.
    src["REN"] = write_vcf(s1 / "REN" / f"alignment_{RENAMED}" / "REN_zc.vcf.gz", RENAMED, OWL, "bb")
    src["REN2"] = write_vcf(s1 / "REN2" / f"alignment_{RENAMED}" / "REN2_zc.vcf", RENAMED, OWL, "cc",
                            gz=False)
    # Same names, one length off: a different coordinate system.
    off = list(OWL)
    off[3] = (off[3][0], off[3][1] + 1)
    src["LEN"] = write_vcf(s1 / "LEN" / f"alignment_{RENAMED}" / "LEN_zc.vcf.gz", RENAMED, off, "ddd")
    # One segment short: not the same reference either.
    src["SUB"] = write_vcf(s1 / "SUB" / f"alignment_{RENAMED}" / "SUB_zc.vcf.gz", RENAMED, OWL[:-1], "eeee")
    # States no contigs at all: nothing to go on, so the name stands.
    src["NOC"] = write_vcf(s1 / "NOC" / f"alignment_{RENAMED}" / "NOC_zc.vcf.gz", RENAMED, None, "fffff")
    # A genuinely different reference.
    src["CHK"] = write_vcf(s1 / "CHK" / f"alignment_{OTHER_REF}" / "CHK_zc.vcf.gz", OTHER_REF, CHICKEN, "g")
    # Recoverable only through the renamed run: its copy in the set is the
    # chicken one, and its only other run is named 25-003495-001.
    src["MIX_ren"] = write_vcf(s1 / "MIX" / f"alignment_{RENAMED}" / "MIX_zc.vcf.gz", RENAMED, OWL, "hh")
    time.sleep(0.02)
    src["MIX_bad"] = write_vcf(s1 / "MIX" / f"alignment_{OTHER_REF}" / "MIX_zc.vcf.gz", OTHER_REF,
                               CHICKEN, "iii")

    db = proj / "step2" / "vcf_database"
    db.mkdir(parents=True)
    for name in ("OWN", "REN", "LEN", "SUB", "NOC", "CHK"):
        shutil.copy2(src[name], db / src[name].name)
    shutil.copy2(src["REN2"], db / "REN2_zc.vcf")
    shutil.copy2(src["MIX_bad"], db / "MIX_zc.vcf.gz")
    # Imported, no Step 1 source, renamed FASTA: the project's reference too.
    write_vcf(db / "IMP_zc.vcf.gz", RENAMED, OWL, "jjjjjjj")
    return proj, db, src


def samples(entries):
    return sorted(d["sample"] for d in entries)


class ReadCounter:
    """Counts VCF header reads made for contig checks."""

    def __init__(self):
        self.n = 0
        self._real = ref_contigs.vcf_contigs

    def __enter__(self):
        def counted(path):
            self.n += 1
            return self._real(path)
        ref_contigs.vcf_contigs = counted
        return self

    def __exit__(self, *exc):
        ref_contigs.vcf_contigs = self._real


def test_parsing(tmp: Path):
    print("contig parsing")
    v = write_vcf(tmp / "p" / "alignment_x" / "P_zc.vcf.gz", "x", OWL, "p")
    check(ref_contigs.vcf_contigs(v), tuple(sorted(OWL)), "a gzipped header's contigs, sorted")
    v2 = write_vcf(tmp / "p" / "alignment_x" / "P_zc.vcf", "x", OWL, "p", gz=False)
    check(ref_contigs.vcf_contigs(v2), tuple(sorted(OWL)), "a plain header's contigs")
    check(ref_contigs.vcf_contigs(tmp / "missing.vcf"), None, "an unreadable file says nothing")
    nolen = tmp / "nolen.vcf"
    nolen.write_text("##fileformat=VCFv4.2\n##contig=<ID=chr1>\n#CHROM\tPOS\n")
    check(ref_contigs.vcf_contigs(nolen), None, "a contig with no length is not a full statement")
    # The FASTA side: every *fasta in the reference dir is one reference, as
    # vsnp3's concat_fasta makes it; the name is the header's first word.
    two = tmp / "two"
    write_fasta(two / "chrI.fasta", [("NC_1", 100)])
    write_fasta(two / "chrII.fasta", [("NC_2", 77)], width=10)
    check(ref_contigs.reference_contigs(two), (("NC_1", 100), ("NC_2", 77)),
          "a two-file reference is one coordinate system, and line width does not matter")
    check(ref_contigs.reference_contigs(tmp / "nowhere"), None, "a missing reference dir says nothing")


def test_audit(cfg, proj, db):
    print("_step2_reference_audit")
    with ReadCounter() as cold:
        a = m._step2_reference_audit(cfg, proj)
    check(samples(a["removable"]), ["CHK", "LEN", "NOC", "SUB"],
          "wrong lengths, a missing segment, no contigs and another reference are still foreign")
    check(samples(a["recoverable"]), ["MIX"],
          "a sample whose only project-reference run is the renamed one can be fixed in place")
    check(samples(a["orphans"]), [], "an imported renamed-FASTA VCF is not an orphan")
    check("REN" in a["unusable"] or "REN2" in a["unusable"], False,
          "the renamed-FASTA runs are not listed as unusable")
    check(a["renamed"], [{"reference": RENAMED, "count": 3}],
          "and are reported as the project reference under another name (REN, REN2, IMP)")
    check(PROJ_REF in a["db_references"], True, "counted as the project reference")
    check(RENAMED in a["db_references"], True,
          "while the genuinely foreign ones still keep the name in the list")
    check(cold.n > 0, True, "a cold audit reads the misnamed VCFs")
    check((proj / "step2" / ref_contigs.CACHE_BASENAME).exists(), True, "and caches the verdicts")
    with ReadCounter() as warm:
        a2 = m._step2_reference_audit(cfg, proj)
    check(warm.n, 0, "a warm audit reads no VCF at all")
    check(samples(a2["removable"]), samples(a["removable"]), "and agrees with the cold one")
    return a


def test_clean_set(cfg, proj, db):
    print("a set that is clean apart from renamed-FASTA VCFs")
    moved = []
    for name in ("LEN_zc.vcf.gz", "SUB_zc.vcf.gz", "NOC_zc.vcf.gz", "CHK_zc.vcf.gz", "MIX_zc.vcf.gz"):
        shutil.move(str(db / name), str(db.parent / name))
        moved.append(name)
    a = m._step2_reference_audit(cfg, proj)
    check(a["mixed"], False, "Step 2 is not disabled")
    check(a["db_references"], [PROJ_REF], "the set is single-reference")
    for name in moved:
        shutil.move(str(db.parent / name), str(db / name))


def test_disabled_without_fasta(cfg, proj, root: Path):
    """No readable reference FASTA: nothing to compare contigs against, so the
    audit must behave exactly as it did by name alone."""
    print("reference FASTA unreachable")
    fasta = root / "refs" / PROJ_REF / f"{PROJ_REF}.fasta"
    hidden = fasta.with_name("hidden.fa.bak")
    fasta.rename(hidden)
    try:
        a = m._step2_reference_audit(cfg, proj)
        check("REN" in a["unusable"], True, "a renamed-FASTA run is foreign again, by name")
        check(a["renamed"], [], "and nothing is reported as renamed")
    finally:
        hidden.rename(fasta)


def test_worklist_recheck(cfg, proj):
    """A worklist written before this fix names the renamed samples; the
    recheck must clear them rather than keep asking for 3,204 re-runs."""
    print("worklist from the name-only version")
    step2 = m.vcf_db_dir(proj / "step2")
    (step2 / m._REF_SKIPPED_BASENAME).write_text(json.dumps({
        "reference": PROJ_REF, "logic": m._REF_SKIPPED_LOGIC,
        "samples": ["REN", "REN2", "CHK"], "written_at": "2026-09-22T10:00:00",
    }))
    for n in ("REN_zc.vcf.gz", "REN2_zc.vcf"):   # as if dropped from the set
        shutil.move(str(step2 / n), str(step2.parent / n))
    a = m._step2_reference_audit(cfg, proj)
    check("REN" in a["unusable"] or "REN2" in a["unusable"], False,
          "the renamed samples drop off the worklist")
    check("CHK" in a["unusable"], True, "a genuinely foreign one stays")
    check(json.loads((step2 / m._REF_SKIPPED_BASENAME).read_text())["samples"],
          ["CHK", "LEN", "NOC", "SUB"], "and the pruning is written back")


def test_build(cfg, proj, db, src):
    print("step2_setup")
    step2 = m.vcf_db_dir(proj / "step2")
    # REN and REN2 were dropped above, and dismissed before this fix.
    (step2 / m._REF_SKIPPED_BASENAME).write_text(json.dumps({
        "reference": PROJ_REF, "logic": m._REF_SKIPPED_LOGIC, "samples": ["CHK"],
        "ignored": ["REN", "REN2", "LEN"], "written_at": "2026-09-22T10:00:00",
    }))
    for n in ("LEN_zc.vcf.gz", "SUB_zc.vcf.gz", "NOC_zc.vcf.gz", "CHK_zc.vcf.gz"):
        (db / n).unlink()
    out = m.step2_setup("owl")
    check((db / "REN_zc.vcf.gz").exists() and (db / "REN2_zc.vcf").exists(), True,
          "Build collects the renamed-FASTA runs")
    check(sorted(n for n in ("LEN", "SUB", "NOC", "CHK")
                 if (db / f"{n}_zc.vcf.gz").exists()), [],
          "and still leaves out every genuinely different one")
    check(out["ref_skipped"], 4, "reporting exactly those four")
    rec = json.loads((step2 / m._REF_SKIPPED_BASENAME).read_text())
    check(rec["ignored"], ["LEN"], "a dismissal of a sample that now collects is dropped")
    check(rec["samples"], ["CHK", "NOC", "SUB"], "the worklist is the real mismatches")
    with ReadCounter() as again:
        m.step2_setup("owl")
    check(again.n, 0, "a second Build reads no VCF headers")


def test_recollect(proj, db, src):
    print("reference_audit/fix — recollect through a renamed run")
    r = m.step2_reference_audit_fix("owl", m.ReferenceAuditFix(action="recollect", samples=["MIX"]))
    check(r["recollected"], ["MIX"], "MIX re-collected")
    check((db / "MIX_zc.vcf.gz").stat().st_size, src["MIX_ren"].stat().st_size,
          "from its renamed-FASTA run, not the chicken one")
    check(r["audit"]["recoverable"], [], "and nothing is left to recover")


def test_import(cfg, proj, root: Path):
    print("import-vcfs")
    ext = root / "external"
    write_vcf(ext / "run" / "X1" / "X1_zc.vcf.gz", RENAMED, OWL, "k")
    write_vcf(ext / "run" / "X2" / "X2_zc.vcf.gz", RENAMED, off_by_one(), "ll")
    r = m.project_import_vcfs("owl", m.ImportVcfRequest(source_paths=[str(ext)], reference=PROJ_REF))
    db = m.vcf_db_dir(proj / "step2")
    check((db / "X1_zc.vcf.gz").exists(), True, "a renamed-FASTA VCF imports under the project reference")
    check((db / "X2_zc.vcf.gz").exists(), False, "one with different lengths is still refused")


def off_by_one():
    off = list(OWL)
    off[0] = (off[0][0], off[0][1] - 1)
    return off


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ref_renamed_"))
    try:
        test_parsing(tmp / "parse")
        proj, db, src = build_project(tmp)
        cfg = {"vsnp3_path": str(build_refs(tmp)), "projects_root": str(tmp / "projects")}
        m.load_config = lambda: cfg
        m._project_dir_for = lambda c, p: proj
        test_audit(cfg, proj, db)
        test_clean_set(cfg, proj, db)
        test_disabled_without_fasta(cfg, proj, tmp)
        test_worklist_recheck(cfg, proj)
        test_build(cfg, proj, db, src)
        test_recollect(proj, db, src)
        test_import(cfg, proj, tmp)
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
