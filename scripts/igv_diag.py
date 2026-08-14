#!/usr/bin/env python3
"""Why won't a SNP-table cell open its alignment in IGV?

Checks, on disk, every assumption the viewer makes between a SNP cell and a
rendered read pileup. Standard library only; run it on the machine holding the
project.

    python3 igv_diag.py <project_dir> [sample]
"""
import glob
import gzip
import os
import re
import sys
import struct
import zipfile

def hdr(t): print(f"\n=== {t}")
def ok(m):  print(f"  OK    {m}")
def bad(m): print(f"  FAIL  {m}")
def note(m):print(f"        {m}")

def bam_contigs(path):
    """Contig names from a BAM header, without pysam."""
    try:
        with gzip.open(path, "rb") as fh:
            magic = fh.read(4)
            if magic != b"BAM\x01":
                return None
            l_text = struct.unpack("<i", fh.read(4))[0]
            fh.read(l_text)
            n_ref = struct.unpack("<i", fh.read(4))[0]
            out = []
            for _ in range(n_ref):
                l_name = struct.unpack("<i", fh.read(4))[0]
                out.append(fh.read(l_name).rstrip(b"\0").decode("utf-8", "replace"))
                fh.read(4)
            return out
    except Exception as e:
        note(f"could not read BAM header: {type(e).__name__}: {e}")
        return None

def table_loci(xlsx, limit=400):
    """Position headers (CHROM:POS) from row 1 of the first sheet."""
    try:
        with zipfile.ZipFile(xlsx) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                shared = [re.sub(r"<[^>]+>", "", m) for m in
                          re.findall(r"<si>(.*?)</si>", sx, re.S)]
            name = next((n for n in z.namelist()
                         if re.fullmatch(r"xl/worksheets/sheet1\.xml", n)), None)
            if not name:
                return []
            xml = z.read(name).decode("utf-8", "replace")
            row1 = re.search(r'<row[^>]*r="1"[^>]*>(.*?)</row>', xml, re.S)
            if not row1:
                return []
            out = []
            for c in re.findall(r"<c\b[^>]*>.*?</c>", row1.group(1), re.S):
                t = re.search(r'\bt="(\w+)"', c)
                v = re.search(r"<v>(.*?)</v>", c, re.S)
                if not v:
                    continue
                val = v.group(1)
                if t and t.group(1) == "s":
                    idx = int(val)
                    val = shared[idx] if idx < len(shared) else ""
                if re.fullmatch(r"\S+:\d+", val.strip()):
                    out.append(val.strip())
                if len(out) >= limit:
                    break
            return out
    except Exception as e:
        note(f"could not read table: {type(e).__name__}: {e}")
        return []

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    proj = os.path.abspath(sys.argv[1].rstrip("/"))
    want = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"project: {proj}")

    step1 = os.path.join(proj, "step1")
    if not os.path.isdir(step1):
        bad(f"no step1/ under {proj}")
        return 1
    samples = sorted(d for d in os.listdir(step1)
                     if os.path.isdir(os.path.join(step1, d)) and not d.startswith((".", "_")))
    if not samples:
        bad("step1/ has no sample directories")
        return 1
    sample = want or samples[0]
    if sample not in samples:
        bad(f"{sample} not in step1/ (have e.g. {samples[:3]})")
        return 1
    note(f"{len(samples)} step1 samples; inspecting {sample}")

    # --- the BAM the viewer will load -------------------------------------
    hdr("1. alignment directory")
    bams = sorted(glob.glob(os.path.join(step1, sample, "**", f"{sample}_nodup.bam"),
                            recursive=True), key=lambda p: os.stat(p).st_mtime)
    if not bams:
        bad(f"no {sample}_nodup.bam anywhere under step1/{sample}/ "
            "-> the viewer reports 'no BAM or VCF' and loads nothing")
        return 1
    bam = bams[-1]
    align = os.path.dirname(bam)
    ok(f"BAM: {bam}")
    if not os.path.exists(bam + ".bai"):
        bad(f"missing index {os.path.basename(bam)}.bai -> igv.js cannot read the BAM")
    else:
        ok("BAM index present")
    for f in sorted(os.listdir(align)):
        p = os.path.join(align, f)
        link = f" -> {os.readlink(p)}" if os.path.islink(p) else ""
        note(f"{f}{link}  ({os.stat(p).st_size if os.path.exists(p) else '?'} bytes)")

    # --- the reference the viewer will pick -------------------------------
    hdr("2. reference FASTA (the viewer takes the FIRST *.fasta, alphabetically)")
    fastas = sorted(glob.glob(os.path.join(align, "*.fasta")))
    if not fastas:
        bad("no *.fasta in the alignment dir -> the viewer reports 'no reference'")
        return 1
    chosen = fastas[0]
    if len(fastas) > 1:
        bad(f"{len(fastas)} FASTA files present — the viewer picks only the first")
        for f in fastas:
            note(("  chosen -> " if f == chosen else "           ") + os.path.basename(f))
    else:
        ok(f"exactly one: {os.path.basename(chosen)}")

    fai = chosen + ".fai"
    if not os.path.exists(fai):
        bad(f"missing {os.path.basename(fai)} — the viewer assumes it exists and "
            "never checks; without it igv.js cannot build the genome "
            "('IGV failed to load')")
        fai_contigs = []
    else:
        ok(f"index present: {os.path.basename(fai)}")
        with open(fai) as fh:
            fai_contigs = [ln.split("\t")[0] for ln in fh if ln.strip()]
    fa_contigs = []
    with open(chosen) as fh:
        for ln in fh:
            if ln.startswith(">"):
                fa_contigs.append(ln[1:].split()[0])
    note(f"{len(fa_contigs)} contigs in the FASTA, {len(fai_contigs)} in the .fai")
    if len(fa_contigs) > 1:
        note("multi-contig reference: igv.js would open on the whole-genome view, "
             "which cannot draw reads — v0.4.54 disables that")

    # --- do the BAM and the reference agree? ------------------------------
    hdr("3. BAM header vs reference")
    bc = bam_contigs(bam)
    if bc is None:
        note("skipped")
    elif set(bc) == set(fa_contigs):
        ok(f"identical contig sets ({len(bc)})")
    else:
        bad("BAM and reference disagree on contigs")
        for n in sorted(set(bc) - set(fa_contigs))[:6]:
            note(f"in BAM only : {n}")
        for n in sorted(set(fa_contigs) - set(bc))[:6]:
            note(f"in FASTA only: {n}")

    # --- the decisive check ----------------------------------------------
    hdr("4. do the SNP table's loci exist in that reference?")
    tables = sorted(glob.glob(os.path.join(proj, "step2", "**", "*_table-*.xlsx"),
                              recursive=True), key=lambda p: os.stat(p).st_mtime)
    if not tables:
        bad("no step2 *_table-*.xlsx found")
        return 1
    table = tables[-1]
    note(f"newest table: {table}")
    loci = table_loci(table)
    if not loci:
        bad("no CHROM:POS headers recognised in row 1 -> no cell can be clickable")
        return 1
    contigs = sorted({l.rsplit(":", 1)[0] for l in loci})
    ok(f"{len(loci)} position headers, {len(contigs)} distinct contigs")
    known = set(fai_contigs or fa_contigs)
    missing = [c for c in contigs if c not in known]
    if missing:
        bad(f"{len(missing)} of {len(contigs)} table contigs are NOT in the "
            "reference the viewer loads")
        for c in missing[:8]:
            note(f"missing: {c}")
        note("=> igv.js cannot resolve those loci; v0.4.54 reports "
             "'did not resolve to a contig in this reference'")
    else:
        ok("every table contig exists in the reference -> loci are resolvable")
    for c in contigs[:8]:
        note(f"table contig: {c}{'' if c in known else '   <-- NOT IN REFERENCE'}")

    hdr("5. sample labels vs step1 folder names")
    # A row label that resolves to no step1 folder gets no link at all.
    print(f"        step1 folders : {samples[:4]}{' …' if len(samples) > 4 else ''}")
    print("        (compare with the first column of the SNP table in the browser)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
