"""Unit tests for the Step 2 comparison-set reference audit.

A 9,400-sample mtbc0 project could not run Step 2, and nothing on screen said
why. Three separate defects stacked up:

  * The Run button's veto read Step 1's reference tally. Step 1 keeps every run
    a sample has ever had, so a sample re-run against the right reference leaves
    its old alignment_<ref>/ dir and its old stats row behind forever — the
    veto could never clear, however clean the comparison set became. What has to
    be single-reference is the comparison set, and that is what is audited here.

  * The veto had no on-screen cause at all. Its one explanation renders in a
    Step 1 branch that a project-level reference replaces, so on exactly the
    projects that hit it the message was unreachable.

  * Build chose each sample's VCF by mtime across all of its alignment_<ref>/
    dirs, so a stale reference won whenever it happened to be written last —
    and since the set never clobbers, that wrong VCF then survived every later
    Build. Collection now prefers the project's reference and refuses to add a
    sample it cannot compare at all.

Run directly:  python test_step2_reference_audit.py
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

FAILURES = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


def write_vcf(path: Path, ref: str, pad: str) -> Path:
    """A zero-coverage VCF whose header names `ref` and whose size is unique."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        fh.write(
            "##fileformat=VCFv4.2\n"
            f"##reference=file:///refs/{ref}.fasta\n"
            f"##padding={pad}\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
    return path


PROJ_REF = "mtbc0_v1.1"
OTHER_REF = "Mycobacterium_AF2122"


def build_refs(root: Path) -> Path:
    """A vsnp3 dependencies tree holding the two references in play.

    The mtbc0 reference dir is named mtbc0_v1.1 and its FASTA is
    MTBC0_v1.1.fasta — the real Ames layout, and the thing that lets the alias
    map recognise the truncated MTBC0_v1 that vsnp3 writes into sample dirs.
    """
    vsnp3 = root / "vsnp3"
    refs = root / "refs"
    for name, fasta in ((PROJ_REF, "MTBC0_v1.1.fasta"), (OTHER_REF, "NC_002945.4.fasta"),
                        ("Brucella_abortus1", "NC_006932.fasta"),
                        ("Brucella_abortus10", "NZ_CP007682.fasta")):
        d = refs / name
        d.mkdir(parents=True)
        (d / fasta).write_text(">chrom\nACGT\n")
    deps = vsnp3 / "dependencies"
    deps.mkdir(parents=True)
    (deps / "reference_options_paths.txt").write_text(str(refs) + "\n")
    return vsnp3


def build_project(root: Path):
    """A project holding every shape the audit has to tell apart."""
    proj = root / "projects" / "mtbc0"
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"reference": PROJ_REF}))
    step1 = proj / "step1"
    src = {}
    # Clean: one run, the project's reference.
    src["S1"] = write_vcf(step1 / "S1" / f"alignment_{PROJ_REF}" / "S1_zc.vcf.gz", PROJ_REF, "a")
    # Unusable: its only run is against another reference.
    src["S2"] = write_vcf(step1 / "S2" / f"alignment_{OTHER_REF}" / "S2_zc.vcf.gz", OTHER_REF, "bb")
    # Recoverable: runs against BOTH, and the wrong one is newer on disk — the
    # exact ordering that made newest-by-mtime collect the wrong VCF.
    src["S3_good"] = write_vcf(step1 / "S3" / f"alignment_{PROJ_REF}" / "S3_zc.vcf.gz", PROJ_REF, "ccc")
    time.sleep(0.02)
    src["S3_bad"] = write_vcf(step1 / "S3" / f"alignment_{OTHER_REF}" / "S3_zc.vcf.gz", OTHER_REF, "dddd")
    # A spelling variant of the project's reference is the SAME reference.
    src["S4"] = write_vcf(step1 / "S4" / "alignment_MTBC0_V1.1" / "S4_zc.vcf.gz", PROJ_REF, "eeeee")
    # Pre-GUI layout: reference is not in the directory name.
    src["S5"] = write_vcf(step1 / "S5" / "alignment" / "S5_zc.vcf.gz", PROJ_REF, "ffffff")
    # What vsnp3 ACTUALLY writes for this reference: MTBC0_v1.1.fasta is copied
    # in as MTBC0_v1.fasta, so the dir is alignment_MTBC0_v1 and the header
    # names the truncated file. This is the project's own reference and must
    # never be read as a foreign one — on the real mtbc0 project 8,882 of the
    # 9,393 samples look like this.
    src["S6"] = write_vcf(step1 / "S6" / "alignment_MTBC0_v1" / "S6_zc.vcf.gz", "MTBC0_v1", "hhhhhhhh")

    db = proj / "step2" / "vcf_database"
    db.mkdir(parents=True)
    shutil.copy2(src["S1"], db / "S1_zc.vcf.gz")
    shutil.copy2(src["S2"], db / "S2_zc.vcf.gz")
    shutil.copy2(src["S3_bad"], db / "S3_zc.vcf.gz")   # the stale wrong copy
    shutil.copy2(src["S4"], db / "S4_zc.vcf.gz")
    shutil.copy2(src["S6"], db / "S6_zc.vcf.gz")
    # Imported / historical: wrong reference, no Step 1 source to swap in.
    write_vcf(db / "S9_zc.vcf.gz", OTHER_REF, "ggggggg")
    return proj, db, src


def samples(entries):
    return [d["sample"] for d in entries]


def test_reference_matching(cfg):
    """Name-level matching, the layer everything else rests on."""
    print("_same_reference")
    am = m._reference_alias_map(Path(cfg["vsnp3_path"]))
    for name, want, why in [
        ("MTBC0_v1", True, "vsnp3's truncated alignment-dir name"),
        ("MTBC0_v1.1", True, "the untruncated FASTA stem"),
        (PROJ_REF, True, "the project's own spelling"),
        ("MTBC0_V1.1", True, "a case variant"),
        ("/home/j/2025-04-01/mb/mtbc0/dir10/S/MTBC0_v1.fasta", True,
         "a full ##reference= header path"),
        (OTHER_REF, False, "a genuinely different reference"),
        ("NC_002945.4", False, "that reference by its accession"),
    ]:
        check(m._same_reference(name, PROJ_REF, am), want, f"{name} — {why}")
    # The loose prefix rule merges these two, and they are separate references.
    # Only reachable when a name matches no configured reference, which is why
    # the both-configured case has to stop short of it.
    check(m._same_reference("Brucella_abortus1", "Brucella_abortus10", am), False,
          "two configured references differing by a trailing digit stay distinct")


def test_audit(cfg, proj, db):
    print("_step2_reference_audit")
    a = m._step2_reference_audit(cfg, proj)
    check(a["project_reference"], PROJ_REF, "reads the project's reference")
    check(samples(a["recoverable"]), ["S3"], "a sample with both runs is recoverable")
    check(samples(a["removable"]), ["S2"], "a sample with no correct run is removable")
    check(samples(a["orphans"]), ["S9"], "a wrong-reference VCF with no Step 1 source is an orphan")
    check(a["mixed"], True, "the set is reported mixed")
    check(a["unknown"], 0, "every VCF's reference was determined")
    # S4's dir is spelled MTBC0_V1.1. Counting that as a second reference would
    # veto Step 2 with an empty list of offenders — an unfixable block, which is
    # the whole failure mode this audit exists to end.
    check("S4" in a["unusable"], False, "a spelling variant is not a second reference")
    # The regression that mattered most on the real project: without the alias
    # resolution these 8,882-equivalent samples are all reported unusable, and
    # the offered repair would delete every one of their good VCFs.
    check("S6" in a["unusable"], False, "vsnp3's truncated MTBC0_v1 is not a second reference")
    check(sorted(samples(a["orphans"]) + samples(a["removable"])), ["S2", "S9"],
          "and it is not offered for dropping either")
    check(a["db_references"], [OTHER_REF, PROJ_REF], "the project's own spelling is the one shown")
    check((db / m._VCF_REF_CACHE_BASENAME).exists(), True, "the stat-signature cache is written")
    warm = m._step2_reference_audit(cfg, proj)
    check(samples(warm["recoverable"]), ["S3"], "the warm-cache audit agrees with the cold one")
    return a


def test_recollect(proj, db, src):
    print("reference_audit/fix — recollect")
    r = m.step2_reference_audit_fix("mtbc0", m.ReferenceAuditFix(action="recollect", samples=["S3"]))
    check(r["recollected"], ["S3"], "S3 re-collected")
    check(r["skipped"], [], "nothing skipped")
    check(
        (db / "S3_zc.vcf.gz").stat().st_size,
        src["S3_good"].stat().st_size,
        "the copy in the set is now the project-reference one",
    )
    check(
        src["S3_bad"].exists() and src["S3_good"].exists(),
        True,
        "both step1 alignment dirs are left untouched",
    )


def test_drop(proj, db, src):
    print("reference_audit/fix — drop")
    r = m.step2_reference_audit_fix("mtbc0", m.ReferenceAuditFix(action="drop", samples=["S2", "S9"]))
    check(sorted(r["dropped"]), ["S2", "S9"], "both dropped")
    check((db / "S2_zc.vcf.gz").exists() or (db / "S9_zc.vcf.gz").exists(), False, "the files are gone")
    check(src["S2"].exists(), True, "dropping from the set leaves step1 alone")
    check(r["audit"]["mixed"], False, "the set is single-reference again, so Run is allowed")
    # A page held open across someone else's repair must not delete a good VCF.
    r = m.step2_reference_audit_fix("mtbc0", m.ReferenceAuditFix(action="drop", samples=["S1"]))
    check((r["dropped"], r["skipped"]), ([], ["S1"]), "refuses a sample the audit does not list")
    check((db / "S1_zc.vcf.gz").exists(), True, "the correct VCF survives that attempt")


def test_collection(cfg, proj, db, src):
    print("step2_setup — collection can no longer reintroduce the problem")
    (db / "S3_zc.vcf.gz").unlink()          # force a fresh collection
    out = m.step2_setup("mtbc0")
    check(
        (db / "S3_zc.vcf.gz").stat().st_size,
        src["S3_good"].stat().st_size,
        "Build prefers the project's reference over the newer wrong-reference VCF",
    )
    check((db / "S2_zc.vcf.gz").exists(), False, "Build does not re-add a sample it cannot compare")
    check(out["ref_skipped"], 1, "and reports how many it left out")
    check((db / "S6_zc.vcf.gz").exists(), True,
          "and still collects the truncated-name run, which IS the project's reference")
    check(
        (db / "S5_zc.vcf.gz").exists(),
        True,
        "a pre-GUI plain alignment/ sample still collects (its reference is not in the dir name)",
    )
    a = m._step2_reference_audit(cfg, proj)
    check(a["mixed"], False, "a Build no longer reintroduces a wrong-reference VCF")
    # The worklist has to outlive the cleanup: S2's VCF is gone from the set,
    # but S2 still has to be re-run or deleted, and that list is what the user
    # copies to the command line.
    check(a["unusable"], ["S2"], "the worklist survives the drop")
    check(a["removable"], [], "with nothing left to drop")


def test_stale_worklist(cfg, proj):
    """A worklist from the string-comparing version must not be believed.

    v0.4.87's Build recorded every truncated-name sample as unusable. Reading
    that file back would show the user 8,882 samples to re-run that are in fact
    running on the right reference.
    """
    print("stale worklist")
    step2 = m.vcf_db_dir(proj / "step2")
    (step2 / m._REF_SKIPPED_BASENAME).write_text(json.dumps({
        "reference": PROJ_REF, "samples": ["S6", "S1"], "written_at": "2026-09-02T10:00:00",
    }))
    a = m._step2_reference_audit(cfg, proj)
    check([x for x in a["unusable"] if x in ("S1", "S6")], [],
          "an unstamped worklist is ignored, not shown")
    (step2 / m._REF_SKIPPED_BASENAME).write_text(json.dumps({
        "reference": PROJ_REF, "logic": m._REF_SKIPPED_LOGIC, "samples": ["S2"],
        "written_at": "2026-09-02T10:00:00",
    }))
    a = m._step2_reference_audit(cfg, proj)
    check("S2" in a["unusable"], True, "a current worklist is still honoured")
    (step2 / m._REF_SKIPPED_BASENAME).unlink()


def test_worklist_goes_stale(cfg, proj):
    """The list must stop naming samples that have stopped needing attention.

    The reported sequence: the panel lists a sample, the user removes it from
    the project, and the list still names it — even after a Build. Only Build
    rewrote the record, so Re-check re-read the same stale names and there was
    no way to clear them.
    """
    print("worklist goes stale")
    step2 = m.vcf_db_dir(proj / "step2")
    step1 = proj / "step1"
    rec = lambda names: (step2 / m._REF_SKIPPED_BASENAME).write_text(json.dumps({
        "reference": PROJ_REF, "logic": m._REF_SKIPPED_LOGIC,
        "samples": names, "written_at": "2026-09-02T10:00:00",
    }))

    # S2 still sits there with only its AF2122 run: it genuinely needs action.
    rec(["S2"])
    check("S2" in m._step2_reference_audit(cfg, proj)["unusable"], True,
          "a sample that still has no correct run stays listed")

    # Removed from the project — the list has nothing left to say about it.
    shutil.move(str(step1 / "S2"), str(step1 / "_S2_removed"))
    a = m._step2_reference_audit(cfg, proj)
    check(a["unusable"], [], "a removed sample drops off the list by itself")
    check(json.loads((step2 / m._REF_SKIPPED_BASENAME).read_text())["samples"], [],
          "and the pruning is written back, so the file stops asserting it")
    shutil.move(str(step1 / "_S2_removed"), str(step1 / "S2"))

    # Re-run against the right reference: also nothing left to say.
    rec(["S2"])
    fixed = write_vcf(step1 / "S2" / f"alignment_{PROJ_REF}" / "S2_zc.vcf.gz", PROJ_REF, "zz")
    check(m._step2_reference_audit(cfg, proj)["unusable"], [],
          "a sample re-run against the project reference drops off too")
    shutil.rmtree(fixed.parent)

    # And the explicit discard.
    rec(["S2"])
    r = m.step2_reference_audit_fix("mtbc0", m.ReferenceAuditFix(action="forget"))
    check(r["audit"]["unusable"], [], "'forget' discards the list")
    check((step2 / m._REF_SKIPPED_BASENAME).exists(), False, "and removes the record")
    check((step1 / "S2").is_dir(), True, "without touching the sample")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ref_audit_"))
    try:
        proj, db, src = build_project(tmp)
        cfg = {"vsnp3_path": str(build_refs(tmp)), "projects_root": str(tmp / "projects")}
        test_reference_matching(cfg)
        test_audit(cfg, proj, db)
        test_stale_worklist(cfg, proj)
        # The fix endpoints resolve the project through the config the same way
        # every other endpoint does; this harness has no config file.
        m.load_config = lambda: cfg
        m._project_dir_for = lambda c, p: proj
        test_recollect(proj, db, src)
        test_drop(proj, db, src)
        test_collection(cfg, proj, db, src)
        test_worklist_goes_stale(cfg, proj)
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
