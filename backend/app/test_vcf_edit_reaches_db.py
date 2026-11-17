"""A Step 1 VCF edit is compared by Step 2.

Step 2 compares step2/vcf_database/ and nothing else. An edit used to be
written only to step1/<sample>/vcf_edits/<sample>_zc.vcf.gz; Build then copied
that in as <sample>_zc.vcf.gz BESIDE the <sample>_zc.vcf already in the set,
and Run refused the sample for having two VCFs. When the entry already had the
.gz name, Build left it alone and the edit was never compared at all.

What this file pins:

  1. An edit goes straight into the sample's ONE database entry, under the
     name it already has, archived first and recorded (the patch log, and
     step2/.history/<db>/_audit.jsonl).
  2. Build and Collect never add a second file for an edited sample, and they
     bring in an edit the set does not hold yet (one made before this fix) by
     the same rule — folding the old bug's second file back into the first.
  3. An entry that is not this sample's own Step 1 VCF — a reference panel's
     copy of the accession, say — is never overwritten; the user is told, by
     the edit, by Build and by Collect.
  4. A legacy symlinked entry is replaced, never written through.
  5. The manifest and the per-file caches in step2/ do not keep answering for
     a file that changed or is gone.

Run directly:  <conda>/bin/python backend/app/test_vcf_edit_reaches_db.py
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as M  # noqa: E402
from app import ref_contigs, sample_groups  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


HEADER = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=MTBC0,length=4411532>\n"
    '##INFO=<ID=AC,Number=A,Type=Integer,Description="Allele count">\n'
    '##INFO=<ID=AN,Number=1,Type=Integer,Description="Total alleles">\n'
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
    '##INFO=<ID=MQ,Number=1,Type=Float,Description="Mapping quality">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
)
RECORDS = (
    "MTBC0\t44\t.\tC\tT\t221.4\t.\tAC=2;AN=2;DP=44;MQ=59.1\tGT:AD:DP\t1/1:0,44:44\n"
    "MTBC0\t100\t.\tG\tC\t300\t.\tAC=2;AN=2;DP=40;MQ=60\tGT:AD:DP\t1/1:0,40:40\n"
)
VCF = HEADER + RECORDS
# A reference database's copy of the same accession: the same calls under its
# own header, written somewhere else, years earlier.
PANEL_VCF = HEADER.replace("##fileformat=VCFv4.2\n",
                           "##fileformat=VCFv4.2\n##fileDate=20240827\n##source=freebayes 1.3.6\n") + RECORDS


def text_of(path: Path) -> str:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt") as fh:
        return fh.read()


def alt_at(path: Path, pos: int) -> str:
    for line in text_of(path).splitlines():
        if line and not line.startswith("#") and line.split("\t")[1] == str(pos):
            return line.split("\t")[4]
    return ""


def db_names(db: Path, sample: str):
    return sorted(e.filename for e in M.db_entries(db) if e.sample == sample)


class Project:
    def __init__(self, root: Path, name: str):
        self.name = name
        self.dir = root / name
        self.db = self.dir / "step2" / "vcf_database"
        self.db.mkdir(parents=True)
        self.step2 = self.dir / "step2"
        self.history = self.step2 / ".history" / "vcf_database"
        (self.dir / "project.json").write_text("{}", encoding="utf-8")

    def sample(self, s: str, text: str = VCF) -> Path:
        aln = self.dir / "step1" / s / "alignment_MTBC0_v1"
        aln.mkdir(parents=True)
        src = aln / f"{s}_zc.vcf"
        src.write_text(text.replace("\tsample\n", f"\t{s}\n"), encoding="utf-8")
        prov = self.dir / "step1" / s / ".provenance"
        prov.mkdir(parents=True)
        (prov / "exit_code").write_text("0")
        return src

    def edit(self, s: str, locus: str, alt: str):
        return M.vcf_edit(self.name, M.VcfEditRequest(
            sample=s, locus=locus, new_alt=alt, reason="mixed_signal", user="tester"))

    def build(self):
        return M.step2_setup(self.name)

    def collect(self):
        return M.project_vcfs_collect(self.name, M.VcfsCollectRequest(force_samples=[]))

    def seed_caches(self, names):
        """One row per database file in each step2/ cache, plus one that stays."""
        keep = "KEEP_zc.vcf"
        for basename, key in ((sample_groups.CACHE_BASENAME, lambda n: n),
                              (M._VCF_REF_CACHE_BASENAME, lambda n: n),
                              (ref_contigs.CACHE_BASENAME,
                               lambda n: f"step2/vcf_database/{n}")):
            files = {key(n): [1, 2, True] for n in [*names, keep]}
            (self.step2 / basename).write_text(json.dumps({"version": 1, "files": files}))

    def cache_keys(self):
        out = set()
        for basename in (sample_groups.CACHE_BASENAME, M._VCF_REF_CACHE_BASENAME,
                         ref_contigs.CACHE_BASENAME):
            out |= set(json.loads((self.step2 / basename).read_text())["files"])
        return out

    def manifest_names(self):
        lines = (self.db / ".vcf_source_manifest.csv").read_text().splitlines()[1:]
        return sorted(l.split(",", 1)[0] for l in lines)

    def audit(self):
        p = self.history / "_audit.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_edit_lands_in_the_entry(root: Path):
    print("[an edit goes into the sample's one database entry, archived and recorded]")
    p = Project(root, "p_edit")
    src = p.sample("ERR027295")
    src_bytes = src.read_bytes()
    p.build()
    check("Build put the Step 1 VCF in the set", db_names(p.db, "ERR027295") == ["ERR027295_zc.vcf"])
    p.seed_caches(["ERR027295_zc.vcf"])

    res = p.edit("ERR027295", "MTBC0:44", "A")
    db_file = p.db / "ERR027295_zc.vcf"
    check("the response says the set was updated", res["vcf_database"]["status"] == "updated")
    check("...and says so in words", "now holds the edit" in res["vcf_database"]["message"])
    check("still one entry, under the name it had", db_names(p.db, "ERR027295") == ["ERR027295_zc.vcf"])
    check("the entry holds the edited call", alt_at(db_file, 44) == "A")
    check("...flagged CURATED", "CURATED" in [l for l in text_of(db_file).splitlines()
                                             if l.startswith("MTBC0\t44\t")][0])
    check("...and is exactly the edited file's text, uncompressed like the entry it replaced",
          not db_file.read_bytes().startswith(b"\x1f\x8b")
          and text_of(db_file) == text_of(Path(res["patched_vcf"])))
    check("the Step 1 VCF itself is untouched", src.read_bytes() == src_bytes)
    archived = sorted(p.history.glob("*_ERR027295_zc.vcf"))
    check("the previous entry is archived under step2/.history/vcf_database/",
          len(archived) == 1 and archived[0].read_bytes() == src_bytes)
    audit = p.audit()
    check("one audit line naming who, what, where and from which file",
          len(audit) == 1 and audit[0]["user"] == "tester" and audit[0]["trigger"] == "vcf_edit"
          and audit[0]["locus"] == "MTBC0:44" and audit[0]["entry"] == "ERR027295_zc.vcf"
          and audit[0]["archived"][0]["archived"] == str(archived[0]))
    log = [json.loads(l) for l in Path(res["log"]).read_text().splitlines()]
    check("the patch log's entry records the same", log[-1]["vcf_database"]["status"] == "updated")
    check("the manifest still lists the entry", p.manifest_names() == ["ERR027295_zc.vcf"])
    keys = p.cache_keys()
    check("no cache row answers for the file as it was",
          not any("ERR027295" in k for k in keys) and "KEEP_zc.vcf" in keys)

    print("[a second edit carries the first with it]")
    res2 = p.edit("ERR027295", "MTBC0:100", "G")
    check("updated again", res2["vcf_database"]["status"] == "updated")
    check("the entry holds both edits", alt_at(db_file, 44) == "A" and alt_at(db_file, 100) == "G")
    check("each previous version is archived", len(list(p.history.glob("*_ERR027295_zc.vcf"))) == 2)

    print("[Build and Collect after an edit add nothing]")
    out = p.build()
    check("one entry for the sample", db_names(p.db, "ERR027295") == ["ERR027295_zc.vcf"])
    check("no sample has two VCFs", M.duplicate_samples(M.db_entries(p.db)) == {})
    check("nothing to apply, nothing refused", out["edits_applied"] == [] and out["edits_not_applied"] == [])
    col = p.collect()
    check("Collect: already present, not shadowed, nothing added",
          col["already_present"] == ["ERR027295"] and col["shadowed"] == []
          and col["auto_added"] == [] and db_names(p.db, "ERR027295") == ["ERR027295_zc.vcf"])
    check("the entry still holds both edits", alt_at(db_file, 44) == "A" and alt_at(db_file, 100) == "G")


def test_edit_before_the_fix(root: Path):
    print("[an edit the set does not hold yet: Build applies it in place]")
    p = Project(root, "p_old")
    src = p.sample("S4")
    res = p.edit("S4", "MTBC0:44", "A")
    check("not in the set yet, and the user is told Build adds it",
          res["vcf_database"]["status"] == "not_in_set" and "Build" in res["vcf_database"]["message"])
    check("the set is untouched", db_names(p.db, "S4") == [])
    # The state every project with an edit is in today: the Step 1 VCF in the
    # set, the edit only under vcf_edits/.
    shutil.copy2(src, p.db / "S4_zc.vcf")
    out = p.build()
    check("Build reports it applied the edit", out["edits_applied"] == ["S4"])
    check("ONE entry, the original name (the bug added S4_zc.vcf.gz beside it)",
          db_names(p.db, "S4") == ["S4_zc.vcf"])
    check("holding the edited call", alt_at(p.db / "S4_zc.vcf", 44) == "A")
    check("the previous entry archived, recorded as Build's",
          len(list(p.history.glob("*_S4_zc.vcf"))) == 1 and p.audit()[-1]["trigger"] == "build")
    out2 = p.build()
    check("a second Build has nothing left to do", out2["edits_applied"] == [] and out2["linked"] == 0)


def test_old_bug_second_file(root: Path):
    print("[the second file the old Build added is folded back into one]")
    p = Project(root, "p_dup")
    src = p.sample("S2")
    res = p.edit("S2", "MTBC0:44", "A")
    # What Build used to leave: the Step 1 VCF and the edited copy side by side.
    shutil.copy2(src, p.db / "S2_zc.vcf")
    shutil.copy2(res["patched_vcf"], p.db / "S2_zc.vcf.gz")
    (p.db / ".vcf_source_manifest.csv").write_text(
        "filename,source_type,source_path\nS2_zc.vcf,step1,x\nS2_zc.vcf.gz,step1,y\n")
    p.seed_caches(["S2_zc.vcf", "S2_zc.vcf.gz"])
    check("the sample starts with two VCFs", len(db_names(p.db, "S2")) == 2)
    col = p.collect()
    check("Collect folds them into one", db_names(p.db, "S2") == ["S2_zc.vcf"]
          and col["edits_applied"] == ["S2"])
    check("which holds the edit", alt_at(p.db / "S2_zc.vcf", 44) == "A")
    check("both former entries are archived",
          len(list(p.history.glob("*_S2_zc.vcf"))) == 1
          and len(list(p.history.glob("*_S2_zc.vcf.gz"))) == 1)
    check("the audit names the removed file", p.audit()[-1]["removed"] == ["S2_zc.vcf.gz"])
    check("the manifest no longer lists the removed file", p.manifest_names() == ["S2_zc.vcf"])
    check("no cache row for either former file",
          not any("S2" in k for k in p.cache_keys()))


def test_foreign_entry(root: Path):
    print("[an entry that is not this sample's Step 1 VCF is never overwritten]")
    p = Project(root, "p_panel")
    p.sample("S5")
    panel = p.db / "S5_zc.vcf"
    panel.write_text(PANEL_VCF.replace("\tsample\n", "\tS5\n"))
    panel_bytes = panel.read_bytes()
    res = p.edit("S5", "MTBC0:44", "A")
    db = res["vcf_database"]
    check("the edit says the set was NOT changed, and how to change it",
          db["status"] == "kept_other" and "NOT changed" in db["message"]
          and "delete S5_zc.vcf" in db["message"])
    check("the panel's copy is byte-for-byte what it was", panel.read_bytes() == panel_bytes)
    check("nothing archived, nothing recorded", not list(p.history.glob("*S5*")) and p.audit() == [])
    check("the edit itself is still saved in Step 1", Path(res["patched_vcf"]).exists())
    out = p.build()
    check("Build reports the edit it could not apply",
          [x["sample"] for x in out["edits_not_applied"]] == ["S5"]
          and "NOT changed" in out["edits_not_applied"][0]["message"])
    col = p.collect()
    check("Collect reports it as shadowed", col["shadowed"] == ["S5"])
    check("neither added a second file", db_names(p.db, "S5") == ["S5_zc.vcf"]
          and panel.read_bytes() == panel_bytes)


def test_legacy_link(root: Path):
    print("[a symlinked entry is replaced, never written through]")
    p = Project(root, "p_link")
    src = p.sample("S6")
    src_bytes = src.read_bytes()
    link = p.db / "S6_zc.vcf"
    link.symlink_to(src)
    (p.db / ".vcf_source_manifest.csv").write_text(
        f"filename,source_type,source_path\nS6_zc.vcf,step1,{src.resolve()}\n")
    res = p.edit("S6", "MTBC0:44", "A")
    check("updated", res["vcf_database"]["status"] == "updated")
    check("the entry is now a real file holding the edit",
          not link.is_symlink() and alt_at(link, 44) == "A")
    check("the Step 1 VCF the link pointed at is untouched", src.read_bytes() == src_bytes)
    line = (p.db / ".vcf_source_manifest.csv").read_text().splitlines()[1]
    check("the manifest points at the entry itself now",
          line.endswith(str(p.db.resolve() / "S6_zc.vcf")))


def test_not_in_set_then_build(root: Path):
    print("[a sample edited before it was ever built goes in once, edited]")
    p = Project(root, "p_new")
    p.sample("S3")
    p.edit("S3", "MTBC0:44", "A")
    p.build()
    names = db_names(p.db, "S3")
    check("exactly one file", len(names) == 1)
    check("holding the edit", names and alt_at(p.db / names[0], 44) == "A")


def main() -> int:
    bcf = shutil.which("bcftools") or str(Path(__file__).resolve().parents[2] / "env/bin/bcftools")
    if not Path(bcf).exists():
        print("bcftools not found — skipped")
        return 0
    root = Path(tempfile.mkdtemp(prefix="vcf_edit_db_"))
    saved = (M.load_config, M._project_dir_for, M._resolve_bcftools)
    M.load_config = lambda: {"vsnp3_path": "/nonexistent", "projects_root": str(root)}
    M._project_dir_for = lambda _cfg, name: root / name
    M._resolve_bcftools = lambda _cfg: bcf
    try:
        test_edit_lands_in_the_entry(root)
        test_edit_before_the_fix(root)
        test_old_bug_second_file(root)
        test_foreign_entry(root)
        test_legacy_link(root)
        test_not_in_set_then_build(root)
    finally:
        M.load_config, M._project_dir_for, M._resolve_bcftools = saved
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{'all passed' if not FAILED else f'{FAILED} FAILED'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
