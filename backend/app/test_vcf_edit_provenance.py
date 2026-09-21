"""What a VCF edit changes, and what it writes down.

The question this answers came from the field: "when a VCF is updated, how are
the QUAL and map-quality values also edited and logged?" — because vsnp3 calls
a SNP on those numbers, not on the ALT alone. The first answer was that they
were not touched; the author's reply was that they have to be, or the edit is
pointless: Step 2 re-applies the caller's verdict from QUAL/AC/MQ and a
heterozygous or low-quality record comes out as N, an ambiguity code or the
reference base whatever the ALT says.

So an edit now does three things, and this file pins each:

  1. ALT changes; DP, AD, FILTER and every other field are copied through.
  2. With pass_filters (the default), whichever of the three gates Step 2
     filters on — QUAL > 150, AC == 2, MQ >= 56 — the record FAILS is set to a
     documented constant (QUAL=999, vsnp3's own "asserted, not measured" value;
     MQ=60; AC=2 with AN and GT alongside). Gates it already passes are left as
     measured. Nothing is set to a plausible-looking invented number.
  3. The record is flagged CURATED, and the file's own header records the
     original values and exactly what was set — because Build copies this file
     into vcf_database/ under the source's name, where nothing else marks it.

The thresholds are mirrored from the installed vsnp3_version.py, and the last
section reads that file and fails if they drift.

Run directly:  <conda>/bin/python backend/app/test_vcf_edit_provenance.py
"""
from __future__ import annotations

import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import main as M  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


HEADER = [
    "##fileformat=VCFv4.2\n",
    '##FILTER=<ID=PASS,Description="All filters passed">\n',
    "##contig=<ID=NC_002945v4,length=4349904>\n",
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample1\n",
]
# One real-shaped record: QUAL 221, MQ 59.1, AC 2, DP 44, AD 0,44.
REC = ("NC_002945v4\t12345\t.\tC\tT\t221.4\tPASS\t"
       "AC=2;AN=2;DP=44;MQ=59.10\tGT:AD:DP:GQ:PL\t1/1:0,44:44:99:255,132,0\n")
OTHER = "NC_002945v4\t12346\t.\tG\t.\t33.0\t.\tDP=40;MQ=58.0\tGT:DP\t0/0:40\n"


def write_vcf(path: Path, records=(REC, OTHER)) -> Path:
    path.write_text("".join(HEADER) + "".join(records), encoding="utf-8")
    return path


def body(text: str):
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vcf_edit_"))
    src = write_vcf(tmp / "S1_zc.vcf")
    out = tmp / "S1_patched.vcf"

    before = M._scan_vcf_for_locus(src, "NC_002945v4", 12345)
    print("[the scan reports the evidence an edit does not touch]")
    check("QUAL", before["qual"] == "221.4")
    check("MQ", before["mq"] == "59.10")
    check("AC", before["ac"] == "2")
    check("DP / AD", before["dp"] == 44 and before["ad"] == [0, 44])

    hdr = M._vcf_edit_header_line(
        contig="NC_002945v4", pos=12345, new_alt="A", before=before,
        user="tester", reason='mixed base; IGV shows "A"', log_name="S1_patchlog.jsonl")
    meta = M._rewrite_vcf_with_alt(src, out, "NC_002945v4", 12345, "A",
                                   header_lines=[hdr])
    text = out.read_text(encoding="utf-8")

    print("[only the ALT column changes]")
    edited = body(text)[0].split("\t")
    original = REC.rstrip("\n").split("\t")
    check("ALT is the new allele", edited[4] == "A")
    check("QUAL is byte-identical", edited[5] == original[5])
    check("FILTER is byte-identical", edited[6] == original[6])
    check("INFO is byte-identical apart from the appended CURATED flag",
          edited[7] == original[7] + ";CURATED")
    check("...and the plan for a record that passes every gate is empty",
          meta["set"] == {} and meta["previous"] == {})
    check("FORMAT and the sample column are byte-identical",
          edited[8:] == original[8:])
    check("every other record is untouched", body(text)[1] == OTHER.rstrip("\n"))
    check("the log gets the old allele", meta["old_alt"] == "T" and meta["old_ref"] == "C")

    print("[the patched file says so in its own header]")
    lines = text.splitlines()
    prov = [l for l in lines if l.startswith("##vsnp3_gui_edit=")]
    check("one provenance line", len(prov) == 1)
    check("...before #CHROM, where a VCF header must be",
          lines.index(prov[0]) < lines.index(
              [l for l in lines if l.startswith("#CHROM")][0]))
    check("...naming the locus and both alleles",
          'Locus="NC_002945v4:12345"' in prov[0]
          and 'oldALT="T"' in prov[0] and 'newALT="A"' in prov[0])
    check("...and the measured quality fields",
          'origQUAL="221.4"' in prov[0] and 'origMQ="59.10"' in prov[0]
          and 'origAC="2"' in prov[0] and 'origAD="0,44"' in prov[0]
          and 'origGT="1/1"' in prov[0])
    check("...saying nothing beyond ALT was set", 'Set="none"' in prov[0])
    check("...and what the edit covers",
          'Scope="ALT, the CURATED flag, and the fields listed in Set;' in prov[0])
    # The reason is free text typed by a user; it must not be able to end the
    # header value early or add a line.
    reason_value = prov[0].split('Reason="')[1].split('"')[0]
    check("a reason containing quotes is escaped, not truncated",
          reason_value == "mixed base; IGV shows 'A'")
    check("...and angle brackets and newlines cannot escape the value",
          "\n" not in prov[0].rstrip("\n")
          and prov[0].rstrip("\n").count("<") == 1
          and prov[0].rstrip("\n").count(">") == 1)
    check("it points at the log beside the sample", 'Log="S1_patchlog.jsonl"' in prov[0])

    print("[a second edit accumulates; it does not overwrite the first]")
    out2 = tmp / "S1_patched2.vcf"
    hdr2 = M._vcf_edit_header_line(
        contig="NC_002945v4", pos=12345, new_alt="G",
        before=M._scan_vcf_for_locus(out, "NC_002945v4", 12345),
        user="tester", reason="second look", log_name="S1_patchlog.jsonl")
    M._rewrite_vcf_with_alt(out, out2, "NC_002945v4", 12345, "G", header_lines=[hdr2])
    text2 = out2.read_text(encoding="utf-8")
    prov2 = [l for l in text2.splitlines() if l.startswith("##vsnp3_gui_edit=")]
    check("both edits are recorded, in order", len(prov2) == 2)
    check("the second records what the first left behind",
          'oldALT="A"' in prov2[1] and 'newALT="G"' in prov2[1])
    check("QUAL still describes the original call, two edits later",
          'origQUAL="221.4"' in prov2[1])
    check("the CURATED flag is not appended twice",
          body(text2)[0].split("\t")[7].count("CURATED") == 1)

    print("[bcftools keeps the line through the compress/index the GUI does]")
    import shutil
    import subprocess
    bcftools = shutil.which("bcftools") or str(
        Path(__file__).resolve().parents[2] / "env/bin/bcftools")
    if Path(bcftools).exists():
        gz = tmp / "S1_patched.vcf.gz"
        subprocess.run([bcftools, "view", "-Oz", "-o", str(gz), str(out)],
                       check=True, capture_output=True)
        subprocess.run([bcftools, "index", "-t", str(gz)], check=True, capture_output=True)
        with gzip.open(gz, "rt") as fh:
            gz_text = fh.read()
        check("the provenance line survives bcftools view -Oz",
              "##vsnp3_gui_edit=" in gz_text)
        check("...and the record is still the edited one",
              body(gz_text)[0].split("\t")[4] == "A")
    else:
        print("      (bcftools not found — skipped)")

    print("[a file with no #CHROM line still gets its header]")
    odd = tmp / "odd.vcf"
    odd.write_text("##fileformat=VCFv4.2\n" + REC, encoding="utf-8")
    M._rewrite_vcf_with_alt(odd, tmp / "odd_out.vcf", "NC_002945v4", 12345, "A",
                            header_lines=["##vsnp3_gui_edit=<x>\n"])
    odd_lines = (tmp / "odd_out.vcf").read_text(encoding="utf-8").splitlines()
    check("written before the first data line",
          odd_lines[1] == "##vsnp3_gui_edit=<x>" and not odd_lines[1].startswith("NC_"))

    print("[editing a position with no variant is refused, as before]")
    try:
        M._rewrite_vcf_with_alt(src, tmp / "x.vcf", "NC_002945v4", 99999, "A")
        check("refused", False)
    except ValueError as e:
        check("refused, saying why", "matches the reference" in str(e))

    print("[a heterozygous call, curated: the gates it fails are set, no more]")
    # Real shapes from a freebayes _zc.vcf: AC=1, MQ below threshold, QUAL fine.
    HET = ("MTBC0\t104003\t.\tC\tT\t792.9\t.\t"
           "AB=0.8;ABP=46.0055;AC=1;AF=0.5;AN=2;AO=44;DP=55;MQ=39.7273;MQMR=51.625;TYPE=snp\t"
           "GT:DP:AD:RO:QR:AO:QA:GL\t0/1:55:8,44:8:189:44:1245:-93.8029,0,-1.12373\n")
    het = write_vcf(tmp / "het_zc.vcf", records=(HET,))
    before_het = M._scan_vcf_for_locus(het, "MTBC0", 104003)
    check("the scan reports AN and GT too",
          before_het["an"] == "2" and before_het["gt"] == "0/1" and before_het["ac"] == "1")
    plan = M._curation_plan(before_het)
    check("QUAL 792.9 already passes and is NOT in the plan", "QUAL" not in plan)
    check("MQ 39.7 fails and is set to 60", plan.get("MQ") == "60")
    check("AC=1 fails and is set to 2, with GT alongside",
          plan.get("AC") == "2" and plan.get("GT") == "1/1")
    check("AN is already 2 and is left alone", "AN" not in plan)
    hdr = M._vcf_edit_header_line(contig="MTBC0", pos=104003, new_alt="T", before=before_het,
                                  user="tester", reason="mixed_signal",
                                  log_name="het_patchlog.jsonl", plan=plan)
    out_het = tmp / "het_patched.vcf"
    meta = M._rewrite_vcf_with_alt(
        het, out_het, "MTBC0", 104003, "G", header_lines=[hdr],
        header_once=[(f"##INFO=<ID={M.CURATED_INFO_FLAG},", M.CURATED_INFO_HEADER)],
        plan=plan)
    rec = body(out_het.read_text())[0].split("\t")
    info = dict(kv.split("=", 1) for kv in rec[7].split(";") if "=" in kv)
    check("ALT is the curated allele", rec[4] == "G")
    check("QUAL is still the measured 792.9", rec[5] == "792.9")
    check("AC is now 2", info["AC"] == "2")
    check("MQ is now 60", info["MQ"] == "60")
    check("AN untouched", info["AN"] == "2")
    check("DP untouched", info["DP"] == "55")
    check("every other INFO key kept, in order",
          rec[7].startswith("AB=0.8;ABP=46.0055;AC=2;AF=0.5;AN=2;AO=44;DP=55;MQ=60;MQMR=51.625;TYPE=snp"))
    check("...and the CURATED flag is on the record", rec[7].endswith(";CURATED"))
    smp = dict(zip(rec[8].split(":"), rec[9].split(":")))
    check("GT is 1/1", smp["GT"] == "1/1")
    check("AD still counts reads for the original allele", smp["AD"] == "8,44")
    check("the rewrite reports what it set and what was there",
          meta["set"] == plan and meta["previous"] == {"AC": "1", "MQ": "39.7273", "GT": "0/1"})
    het_text = out_het.read_text()
    check("the ##INFO definition for CURATED is in the header, once",
          het_text.count(M.CURATED_INFO_HEADER) == 1)
    check("...before #CHROM",
          het_text.index(M.CURATED_INFO_HEADER) < het_text.index("#CHROM"))
    prov_het = [l for l in het_text.splitlines() if l.startswith("##vsnp3_gui_edit=")][0]
    check("the header line lists exactly what was set",
          'Set="MQ=60;AC=2;GT=1/1"' in prov_het)
    check("...beside the measured originals",
          'origQUAL="792.9"' in prov_het and 'origMQ="39.7273"' in prov_het
          and 'origAC="1"' in prov_het and 'origGT="0/1"' in prov_het)

    print("[it now clears Step 2's gates — the point of the exercise]")
    q, ac, mq = float(rec[5]), int(info["AC"]), float(info["MQ"])
    check("QUAL > 150", q > M.VSNP3_QUAL_THRESHOLD)
    check("AC == 2", ac == M.VSNP3_AC_HOMOZYGOUS)
    check("MQ >= 56", mq >= M.VSNP3_MQ_THRESHOLD)

    print("[a low-quality call: QUAL is set too, to vsnp3's own asserted value]")
    LOW = ("MTBC0\t104131\t.\tA\tG\t46.4252\t.\t"
           "AC=1;AF=0.5;AN=2;DP=12;MQ=30.8333;TYPE=snp\tGT:DP:AD\t0/1:12:6,6\n")
    low = write_vcf(tmp / "low_zc.vcf", records=(LOW,))
    plan_low = M._curation_plan(M._scan_vcf_for_locus(low, "MTBC0", 104131))
    check("QUAL 46 fails and is set to 999 (SYNTHESIZED_QUAL)",
          plan_low.get("QUAL") == "999")
    check("all three gates in the plan",
          set(plan_low) == {"QUAL", "MQ", "AC", "GT"})

    print("[pass_filters off: ALT and the flag, nothing else]")
    out_off = tmp / "het_off.vcf"
    meta_off = M._rewrite_vcf_with_alt(het, out_off, "MTBC0", 104003, "G",
                                       header_lines=[], plan={})
    rec_off = body(out_off.read_text())[0].split("\t")
    check("QUAL, INFO values and GT untouched",
          rec_off[5] == "792.9" and "AC=1;" in rec_off[7] and "MQ=39.7273" in rec_off[7]
          and rec_off[9].startswith("0/1:"))
    check("...but the record is still marked as hand-edited", rec_off[7].endswith(";CURATED"))
    check("and nothing is reported as set", meta_off["set"] == {})

    print("[a second edit on a curated file: everything already passes]")
    plan2 = M._curation_plan(M._scan_vcf_for_locus(out_het, "MTBC0", 104003))
    check("empty plan the second time round", plan2 == {})
    out_het2 = tmp / "het_patched2.vcf"
    M._rewrite_vcf_with_alt(
        out_het, out_het2, "MTBC0", 104003, "A",
        header_lines=[M._vcf_edit_header_line(contig="MTBC0", pos=104003, new_alt="A",
                                              before=M._scan_vcf_for_locus(out_het, "MTBC0", 104003),
                                              user="t", reason="r", log_name="l", plan=plan2)],
        header_once=[(f"##INFO=<ID={M.CURATED_INFO_FLAG},", M.CURATED_INFO_HEADER)],
        plan=plan2)
    t2 = out_het2.read_text()
    check("the ##INFO CURATED definition is still there exactly once",
          t2.count(M.CURATED_INFO_HEADER) == 1)
    check("two edit lines, in order",
          [l for l in t2.splitlines() if l.startswith("##vsnp3_gui_edit=")][1].count('newALT="A"') == 1)
    check("the flag appears once in INFO", body(t2)[0].split("\t")[7].count("CURATED") == 1)

    print("[the endpoint end to end: scan, plan, rewrite, bcftools, log]")
    import json
    import shutil
    bcf = shutil.which("bcftools") or str(Path(__file__).resolve().parents[2] / "env/bin/bcftools")
    if Path(bcf).exists():
        proj = tmp / "proj/p1"
        aln = proj / "step1/S1/alignment_MTBC0_v1"
        aln.mkdir(parents=True)
        (proj / "project.json").write_text('{"reference": "mtbc0_v1.1"}', encoding="utf-8")
        # Positions in order, as a real _zc.vcf has them.
        write_vcf(aln / "S1_zc.vcf", records=(REC, OTHER, HET, LOW))
        M.load_config = lambda: {"vsnp3_path": "/nonexistent", "projects_root": str(proj.parent)}
        M._project_dir_for = lambda _cfg, _name: proj
        M._resolve_bcftools = lambda _cfg: bcf

        res = M.vcf_edit("p1", M.VcfEditRequest(sample="S1", locus="MTBC0:104003", new_alt="G",
                                                reason="mixed_signal", user="tester"))
        patched = Path(res["patched_vcf"])
        check("a patched .vcf.gz was installed", patched.exists() and patched.suffix == ".gz")
        check("...with its index beside it", patched.with_suffix(".gz.tbi").exists())
        check("the response says what was set",
              res["entry"]["set"] == {"MQ": "60", "AC": "2", "GT": "1/1"})
        with gzip.open(patched, "rt") as fh:
            gz_text = fh.read()
        rec_gz = [l for l in body(gz_text) if l.split("\t")[1] == "104003"][0].split("\t")
        check("the record in the installed file is the curated one",
              rec_gz[4] == "G" and "AC=2" in rec_gz[7] and "MQ=60" in rec_gz[7]
              and rec_gz[7].endswith("CURATED") and rec_gz[9].startswith("1/1:"))
        check("...and the header carries the edit line and the INFO definition",
              "##vsnp3_gui_edit=" in gz_text and M.CURATED_INFO_HEADER in gz_text)
        log = Path(res["log"])
        entries = [json.loads(l) for l in log.read_text().splitlines()]
        check("one log line, with the measured values and what was set",
              len(entries) == 1 and entries[0]["set"] == {"MQ": "60", "AC": "2", "GT": "1/1"}
              and entries[0]["measured"]["mq"] == "39.7273"
              and entries[0]["measured"]["gt"] == "0/1")

        print("[a failed index leaves the previous edit, its index and the log alone]")
        before_bytes = patched.read_bytes()
        # Append an out-of-order record to the SOURCE so bcftools index refuses
        # the rewrite. (The patched file is read as the base for a second edit,
        # so break that instead: same effect, and it is the file being replaced.)
        with gzip.open(patched, "rt") as fh:
            lines = fh.read().splitlines(keepends=True)
        broken = aln.parent / "vcf_edits" / "S1_zc.vcf.gz"
        with gzip.open(broken, "wt") as fh:
            fh.writelines(lines + ["MTBC0\t50\t.\tA\tG\t500\t.\tAC=2;AN=2;DP=9;MQ=60\tGT\t1/1\n"])
        broken_bytes = broken.read_bytes()
        try:
            M.vcf_edit("p1", M.VcfEditRequest(sample="S1", locus="MTBC0:104131", new_alt="T",
                                              reason="other", user="tester"))
            check("refused", False)
        except HTTPException as e:
            check("refused with a 500 that says the edit was not applied",
                  e.status_code == 500 and "not applied" in str(e.detail))
        check("the patched file is byte-for-byte what it was",
              broken.read_bytes() == broken_bytes)
        check("its index is still there", broken.with_suffix(".gz.tbi").exists())
        check("no log line was written for the failed edit",
              len(log.read_text().splitlines()) == 1)
        check("no staging files were left behind",
              not [f for f in (aln.parent / "vcf_edits").iterdir() if f.name.startswith(".")])
    else:
        print("      (bcftools not found — skipped)")

    print("[the mirrored thresholds agree with the installed vsnp3]")
    import re as _re
    ver = Path(__file__).resolve().parents[2] / "env/bin/vsnp3_version.py"
    if ver.exists():
        src = ver.read_text(encoding="utf-8")
        def const(name):
            m = _re.search(rf"^{name}\s*=\s*(\d+)", src, _re.M)
            return int(m.group(1)) if m else None
        check("QUAL_THRESHOLD", const("QUAL_THRESHOLD") == M.VSNP3_QUAL_THRESHOLD)
        check("MQ_THRESHOLD", const("MQ_THRESHOLD") == M.VSNP3_MQ_THRESHOLD)
        check("AC_HOMOZYGOUS", const("AC_HOMOZYGOUS") == M.VSNP3_AC_HOMOZYGOUS)
        check("SYNTHESIZED_QUAL", const("SYNTHESIZED_QUAL") == M.VSNP3_SYNTHESIZED_QUAL)
    else:
        print("      (no vsnp3_version.py in env/bin — skipped)")

    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
