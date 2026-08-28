"""Unit tests for the v0.4.83 Step 2 accounting work.

Two numbers on the Build panel were saying something they did not mean, and one
thing it never said at all:

  * "duplicates across DBs" counted a sample as duplicated if it came from two
    or more SOURCES — and the project's own step1/ counted as a source. On the
    mtbc0 test project that reported 57 (the project's Step 1 accessions, all of
    which are also panel accessions) under a label promising the cross-database
    overlap, which is 17. The two facts are now counted and shown separately.

  * Build never overwrites, so when the same accession arrives from a reference
    panel and from this project's Step 1 run, whichever landed first is the one
    compared. That is invisible and harmless when both files call the same
    variants (a panel VCF and a local rerun of the same accession differ in
    fileDate, freebayes version and paths — header only), and a silent
    substitution of somebody else's genotype when the RECORDS differ. Only the
    second case is now reported.

Run directly:  python test_step2_overlap_and_collisions.py
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import tempfile
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


HEADER = [
    "##fileformat=VCFv4.2",
    "##fileDate={date}",
    "##source=freeBayes v{ver}",
    "##reference={path}/MTBC0_v1.fasta",
]
COLUMNS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample"


def vcf_text(records, date="20260828", ver="1.3.10", path="/here"):
    lines = [h.format(date=date, ver=ver, path=path) for h in HEADER]
    lines.append(COLUMNS)
    lines.extend(records)
    return "\n".join(lines) + "\n"


REC_A = ["MTBC0\t1849\t.\tG\tA\t3200\t.\tAC=2\tGT\t1/1"]
REC_B = ["MTBC0\t1849\t.\tG\tT\t3200\t.\tAC=2\tGT\t1/1"]


def test_variant_digest(tmp: Path):
    print("_vcf_variant_digest")
    mine = tmp / "mine.vcf"
    theirs = tmp / "theirs.vcf"
    other = tmp / "other.vcf"
    # Same calls, produced on another machine in another year by another build.
    mine.write_text(vcf_text(REC_A))
    theirs.write_text(vcf_text(REC_A, date="20240827", ver="1.3.6", path="/cluster/run21"))
    other.write_text(vcf_text(REC_B))
    check(mine.read_bytes() == theirs.read_bytes(), False, "the two files do differ byte for byte")
    check(
        m._vcf_variant_digest(mine) == m._vcf_variant_digest(theirs),
        True,
        "header-only differences are not a collision",
    )
    check(
        m._vcf_variant_digest(mine) == m._vcf_variant_digest(other),
        False,
        "a changed variant record is a collision",
    )
    gz = tmp / "mine.vcf.gz"
    with gzip.open(gz, "wt") as fh:
        fh.write(vcf_text(REC_A, date="19990101"))
    check(m._vcf_variant_digest(gz), m._vcf_variant_digest(mine), "gzipped VCF hashes the same records")
    check(m._vcf_variant_digest(tmp / "nope.vcf"), None, "unreadable file is unknown, not 'differs'")


def make_project(root: Path, name: str) -> Path:
    p = root / name
    for sub in ("step1", "step2/vcf_database", "download", "quarantine"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    (p / "project.json").write_text(json.dumps({"reference": "MTBC0_v1"}))
    return p


def test_overlap_split(tmp: Path):
    print("step2/vcf_count overlap split")
    root = tmp / "projects"
    proj = make_project(root, "mtbc0_test")
    db = proj / "step2" / "vcf_database"
    # Mirrors the real mtbc0_v1.1 shape in miniature: minimum_tree is wholly
    # contained in representative, and the project's own Step 1 run used the
    # same public accessions the representative panel is built from.
    minimum_tree = {"ERR181314", "ERR553376"}
    representative = {"ERR181314", "ERR553376", "ERR015582", "ERR027295"}
    synthetic = {"CP014617"}
    for stem in representative | synthetic:
        (db / f"{stem}_zc.vcf").write_text(vcf_text(REC_A))
    for stem in ("ERR181314", "ERR553376", "ERR015582", "ERR027295"):
        s1 = proj / "step1" / stem
        s1.mkdir(parents=True, exist_ok=True)
        # A Step 1 sample dir is one holding reads — see _step1_sample_names.
        (s1 / f"{stem}_R1.fastq.gz").write_bytes(b"")

    real_cfg, real_panels, real_block = m.load_config, m._reference_panels_by_name, m._reference_blocklist_names
    m.load_config = lambda: {"projects_root": str(root)}
    m._reference_panels_by_name = lambda cfg, ref: [
        ("minimum_tree", minimum_tree),
        ("representative", representative),
        ("synthetic", synthetic),
    ]
    m._reference_blocklist_names = lambda cfg, ref: []
    try:
        data = m.step2_vcf_count("mtbc0_test")
    finally:
        m.load_config, m._reference_panels_by_name, m._reference_blocklist_names = real_cfg, real_panels, real_block

    check(data["count"], 5, "5 unique samples from 7 panel files")
    check(
        {c["name"]: c["count"] for c in data["composition"]},
        {"vcf_database": 0, "minimum_tree": 2, "representative": 2, "synthetic": 1},
        "each sample bucketed under the first panel that claims it",
    )
    check(data["duplicates_across_dbs"], 2, "2 samples carried by two databases")
    check(data["duplicates_with_project"], 4, "4 panel samples also ran through this project's Step 1")
    check(data["duplicates"], 4, "legacy combined figure unchanged for older clients")


def test_build_collisions(tmp: Path):
    print("Build reports only the clashes that change the calls")
    root = tmp / "projects2"
    proj = make_project(root, "clash")
    db = proj / "step2" / "vcf_database"
    panel = tmp / "panel"
    panel.mkdir(parents=True, exist_ok=True)

    # Already in the set: this project's own Step 1 output for three samples.
    for stem in ("SAME", "DIFFERENT", "ONLYMINE"):
        recs = REC_A
        (db / f"{stem}_zc.vcf").write_text(vcf_text(recs))
        s1 = proj / "step1" / stem
        s1.mkdir(parents=True, exist_ok=True)
        (s1 / f"{stem}_zc.vcf").write_text(vcf_text(recs))
        (s1 / f"{stem}_R1.fastq.gz").write_bytes(b"")
    # The panel offers its own copy of two of them, plus one new sample.
    (panel / "SAME_zc.vcf").write_text(vcf_text(REC_A, date="20240827", ver="1.3.6", path="/cluster"))
    (panel / "DIFFERENT_zc.vcf").write_text(vcf_text(REC_B, date="20240827", ver="1.3.6", path="/cluster"))
    (panel / "NEW_zc.vcf").write_text(vcf_text(REC_A))
    # A second panel carrying the same accession — the mtbc0 minimum_tree /
    # representative case. One sample, two rejected files, one warning.
    panel2 = tmp / "panel2"
    panel2.mkdir(parents=True, exist_ok=True)
    (panel2 / "DIFFERENT_zc.vcf").write_text(vcf_text(REC_B, date="20230101", ver="1.3.5", path="/elsewhere"))
    # The case worth naming: the panel's copy is the one already in the set, so
    # this project's own Step 1 call for that accession is what gets skipped.
    (db / "DBWINS_zc.vcf").write_text(vcf_text(REC_B, date="20240827", ver="1.3.6", path="/cluster"))
    (panel / "DBWINS_zc.vcf").write_text(vcf_text(REC_B, date="20240827", ver="1.3.6", path="/cluster"))
    s1 = proj / "step1" / "DBWINS"
    s1.mkdir(parents=True, exist_ok=True)
    (s1 / "DBWINS_zc.vcf").write_text(vcf_text(REC_A))
    (s1 / "DBWINS_R1.fastq.gz").write_bytes(b"")

    real_cfg = m.load_config
    real_alias, real_refs, real_ref, real_match = (
        m._reference_alias_map, m._detect_vcf_references, m._detect_vcf_reference, m._refs_match,
    )
    m.load_config = lambda: {"projects_root": str(root), "vsnp3_path": str(tmp)}
    m._reference_alias_map = lambda p: {}
    m._detect_vcf_references = lambda vcfs, am: {"MTBC0_v1"}
    m._detect_vcf_reference = lambda v, am: "MTBC0_v1"
    m._refs_match = lambda a, b, fuzzy: True
    try:
        res = m.project_import_vcfs(
            "clash",
            m.ImportVcfRequest(source_paths=[str(panel), str(panel2)], include_step1=True, reference="MTBC0_v1"),
        )
    finally:
        m.load_config = real_cfg
        m._reference_alias_map, m._detect_vcf_references, m._detect_vcf_reference, m._refs_match = (
            real_alias, real_refs, real_ref, real_match,
        )

    check(res["imported"], 1, "only the genuinely new panel sample is copied in")
    check(res["collision_samples"], ["DBWINS", "DIFFERENT"], "one row per sample, not per rejected file")
    check(res["collisions"], 2, "the header-only clash is not reported, the two real ones are")
    check(Path(res["collision_report"]).exists(), True, "collision report written")
    report = Path(res["collision_report"]).read_text()
    check("DIFFERENT" in report and "in_use" in report, True, "report names the sample and which copy is in use")
    rows = [ln for ln in report.strip().splitlines()[1:] if ln]
    check(len([r for r in rows if r.startswith('"DIFFERENT"')]), 2, "report lists BOTH rejected files for that one sample")
    check(
        all(any(str(src) in ln for ln in rows) for src in (panel, panel2)),
        True,
        "report names each rejected source folder",
    )
    check(
        (db / "DIFFERENT_zc.vcf").read_text() == vcf_text(REC_A),
        True,
        "the copy already in the set is left in place (Build never overwrites)",
    )
    check(
        sorted({ln.split(",")[0] + " -> " + ln.split(",")[1] for ln in rows}),
        ['"DBWINS" -> "reference database"', '"DIFFERENT" -> "this project\'s Step 1"'],
        "report says whose calls are actually being compared",
    )
    check(res["collision_db_wins"], ["DBWINS"], "the database-wins case is called out separately")
    # A second Build must not invent new collisions or lose the old one.
    m.load_config = lambda: {"projects_root": str(root), "vsnp3_path": str(tmp)}
    m._reference_alias_map = lambda p: {}
    m._detect_vcf_references = lambda vcfs, am: {"MTBC0_v1"}
    m._detect_vcf_reference = lambda v, am: "MTBC0_v1"
    m._refs_match = lambda a, b, fuzzy: True
    try:
        again = m.project_import_vcfs(
            "clash",
            m.ImportVcfRequest(source_paths=[str(panel), str(panel2)], include_step1=True, reference="MTBC0_v1"),
        )
    finally:
        m.load_config = real_cfg
        m._reference_alias_map, m._detect_vcf_references, m._detect_vcf_reference, m._refs_match = (
            real_alias, real_refs, real_ref, real_match,
        )
    check(again["imported"], 0, "rebuild adds nothing")
    check(again["collision_samples"], ["DBWINS", "DIFFERENT"], "rebuild reports the same collisions")


def test_collect_shadowing(tmp: Path):
    print("Collect says when the database already holds a different call")
    root = tmp / "projects3"
    proj = make_project(root, "shadow")
    db = proj / "step2" / "vcf_database"

    def step1_sample(name, records, in_db):
        d = proj / "step1" / name / "alignment_MTBC0_v1"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}_zc.vcf").write_text(vcf_text(records))
        prov = proj / "step1" / name / ".provenance"
        prov.mkdir(parents=True, exist_ok=True)
        (prov / "exit_code").write_text("0")
        if in_db is not None:
            (db / f"{name}_zc.vcf").write_text(in_db)

    # Already in the database from an earlier Build, with the panel's calls.
    step1_sample("SHADOWED", REC_A, vcf_text(REC_B, date="20240827", ver="1.3.6", path="/cluster"))
    # Already there, same calls, panel header — the ordinary case, stays quiet.
    step1_sample("HEADERONLY", REC_A, vcf_text(REC_A, date="20240827", ver="1.3.6", path="/cluster"))
    # Not in the database yet.
    step1_sample("FRESH", REC_A, None)

    real_cfg = m.load_config
    m.load_config = lambda: {"projects_root": str(root)}
    try:
        res = m.project_vcfs_collect("shadow", m.VcfsCollectRequest(force_samples=[]))
    finally:
        m.load_config = real_cfg

    check(res["auto_added"], ["FRESH"], "only the missing sample is copied in")
    check(sorted(res["already_present"]), ["HEADERONLY", "SHADOWED"], "the other two were already there")
    check(res["shadowed"], ["SHADOWED"], "only the differing one is reported")
    check(
        (db / "SHADOWED_zc.vcf").read_text().splitlines()[-1],
        REC_B[0],
        "Collect still does not overwrite what is already there",
    )


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vsnp_overlap_"))
    try:
        test_variant_digest(tmp)
        test_overlap_split(tmp)
        test_build_collisions(tmp)
        test_collect_shadowing(tmp)
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
