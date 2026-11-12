"""The reference GFF as the IGV window reads it: declared GFF3, each CDS folded
into its gene, every gene named (igv_annotation.py).

Run directly:  python test_igv_annotation.py
Every reference GFF under ~/.local/share/bdtools/vsnp3-refs is checked too when
that directory exists.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from app import igv_annotation
from app import main as app_main

FAILURES = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


def features(text):
    """(type, start, attrs dict) for each feature line."""
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        out.append((cols[2], cols[3], dict(igv_annotation._parse_attrs(cols[8]))))
    return out


def row(seqid, ftype, start, end, strand, attrs):
    phase = "0" if ftype == "CDS" else "."
    return "\t".join([seqid, "EMBL", ftype, str(start), str(end), ".", strand, phase, attrs])


# NCBI's layout, as in NC_002945v4.gff: no gff-version line, a region line,
# gene + CDS pairs with no mRNA, a tRNA under its gene with an exon under it.
NCBI = "\n".join([
    "##sequence-region NC_002945.4 1 4349904",
    row("NC_002945.4", "region", 1, 4349904, "+", "ID=NC_002945.4:1..4349904;Name=Mycobacterium_bovis_AF2122/97;gbkey=Src"),
    row("NC_002945.4", "gene", 894148, 895449, "+", "ID=gene-BQ2027_MB0823;Name=pepC;gbkey=Gene;gene=pepC;locus_tag=BQ2027_MB0823"),
    row("NC_002945.4", "CDS", 894148, 895449, "+", "ID=cds-SIT99422.1;Parent=gene-BQ2027_MB0823;Name=SIT99422.1;Note=Mb0823%2C pepC;gene=pepC;product=PROBABLE AMINOPEPTIDASE PEPC;protein_id=SIT99422.1"),
    row("NC_002945.4", "gene", 895461, 895808, "+", "ID=gene-BQ2027_MB0824;Name=BQ2027_MB0824;gbkey=Gene;locus_tag=BQ2027_MB0824"),
    row("NC_002945.4", "CDS", 895461, 895808, "+", "ID=cds-SIT99423.1;Parent=gene-BQ2027_MB0824;Name=SIT99423.1;product=conserved protein;protein_id=SIT99423.1"),
    row("NC_002945.4", "gene", 1000, 1074, "+", "ID=gene-BQ2027_ILET;Name=ileT;gene=ileT;locus_tag=BQ2027_ILET"),
    row("NC_002945.4", "tRNA", 1000, 1074, "+", "ID=rna-BQ2027_ILET;Parent=gene-BQ2027_ILET;product=tRNA-Ile"),
    row("NC_002945.4", "exon", 1000, 1074, "+", "ID=exon-BQ2027_ILET-1;Parent=rna-BQ2027_ILET;product=tRNA-Ile"),
    row("NC_002945.4", "repeat_region", 2000, 3000, "+", "ID=id-BQ2027_REP-3;Note=REP-3"),
]) + "\n"

# Prokka's layout: gene lines without a Name, the name on the CDS, a FASTA tail.
PROKKA = "\n".join([
    "##gff-version 3",
    "##sequence-region contig_1 1 5000",
    row("contig_1", "gene", 10, 900, "+", "ID=ABC_00001_gene;locus_tag=ABC_00001"),
    row("contig_1", "CDS", 10, 900, "+", "ID=ABC_00001;Parent=ABC_00001_gene;Name=dnaA;gene=dnaA;locus_tag=ABC_00001;product=Chromosomal replication initiator protein DnaA"),
    row("contig_1", "gene", 1000, 1300, "-", "ID=ABC_00002_gene;locus_tag=ABC_00002"),
    row("contig_1", "CDS", 1000, 1300, "-", "ID=ABC_00002;Parent=ABC_00002_gene;locus_tag=ABC_00002;product=hypothetical protein"),
    "##FASTA",
    ">contig_1",
    "ACGTACGTACGT",
]) + "\n"

# What must stay: a CDS that is some feature's Parent, a CDS with two parents,
# a CDS under an mRNA.
KEEP = "\n".join([
    row("NC_045512.2", "gene", 266, 21555, "+", "ID=gene-GU280_gp01;Name=ORF1ab;gene=ORF1ab"),
    row("NC_045512.2", "CDS", 266, 13483, "+", "ID=cds-YP_009725295.1;Parent=gene-GU280_gp01;product=ORF1a polyprotein"),
    row("NC_045512.2", "mature_protein_region_of_CDS", 266, 805, "+", "ID=id-YP_009725295.1:1..180;Parent=cds-YP_009725295.1;product=leader protein"),
    row("NC_045512.2", "gene", 21563, 25384, "+", "ID=gene-GU280_gp02;Name=S;gene=S"),
    row("NC_045512.2", "gene", 21563, 25384, "+", "ID=gene-other;Name=other"),
    row("NC_045512.2", "CDS", 21563, 25384, "+", "ID=cds-two;Parent=gene-GU280_gp02,gene-other;product=two parents"),
    row("NC_045512.2", "mRNA", 26000, 26500, "+", "ID=rna-x;Parent=gene-GU280_gp02"),
    row("NC_045512.2", "CDS", 26000, 26500, "+", "ID=cds-under-mrna;Parent=rna-x"),
]) + "\n"


def test_ncbi():
    print("NCBI layout")
    out = igv_annotation.igv_gff3(NCBI)
    check(out.splitlines()[0], "##gff-version 3", "declared GFF3 first")
    check("##sequence-region NC_002945.4 1 4349904" in out, True, "other directives kept")
    f = features(out)
    types = [t for t, _, _ in f]
    check("region" in types, False, "region line dropped")
    check(types.count("CDS"), 0, "every CDS folded into its gene")
    genes = {a["ID"]: a for t, _, a in f if t == "gene"}
    check(genes["gene-BQ2027_MB0823"]["Name"], "pepC", "named gene keeps its gene name")
    check(genes["gene-BQ2027_MB0823"]["product"], "PROBABLE AMINOPEPTIDASE PEPC", "gene takes its CDS's product")
    check(genes["gene-BQ2027_MB0823"]["protein_id"], "SIT99422.1", "gene takes its CDS's protein_id")
    check(genes["gene-BQ2027_MB0823"]["Note"], "Mb0823%2C pepC", "Note carried still percent-encoded")
    check(genes["gene-BQ2027_MB0824"]["Name"], "BQ2027_MB0824", "unnamed gene labelled with its locus tag")
    check(genes["gene-BQ2027_MB0824"]["product"], "conserved protein", "unnamed gene still gets its product")
    check([t for t in types if t in ("tRNA", "exon", "repeat_region")], ["tRNA", "exon", "repeat_region"],
          "tRNA, its exon and other features pass through")
    trna = next(a for t, _, a in f if t == "tRNA")
    check(trna["Name"], "ileT", "an unnamed tRNA takes its gene's label")
    exon = next(a for t, _, a in f if t == "exon")
    check("Name" in exon, False, "its exon is left as written")
    first = next(l for l in out.splitlines() if "gene-BQ2027_MB0823" in l and "\tgene\t" in l)
    check(first.split("\t")[8].split(";")[:2], ["ID=gene-BQ2027_MB0823", "Name=pepC"], "Name sits right after ID")


def test_prokka():
    print("Prokka layout")
    out = igv_annotation.igv_gff3(PROKKA)
    check(out.count("##gff-version"), 1, "one gff-version line, not two")
    check("##FASTA" in out or ">contig_1" in out, False, "FASTA section dropped")
    genes = {a["ID"]: a for t, _, a in features(out) if t == "gene"}
    check(genes["ABC_00001_gene"]["Name"], "dnaA", "name taken from the CDS's gene")
    check(genes["ABC_00001_gene"]["product"], "Chromosomal replication initiator protein DnaA", "product from the CDS")
    check(genes["ABC_00002_gene"]["Name"], "ABC_00002", "locus tag when no gene name anywhere")


def test_keep():
    print("CDS lines that must stay")
    out = igv_annotation.igv_gff3(KEEP)
    ids = [a.get("ID") for t, _, a in features(out) if t == "CDS"]
    check(ids, ["cds-YP_009725295.1", "cds-two", "cds-under-mrna"], "a Parent CDS, a two-parent CDS, an mRNA's CDS stay")
    genes = {a["ID"]: a for t, _, a in features(out) if t == "gene"}
    check("product" in genes["gene-GU280_gp01"], False, "gene not given a product from a CDS that stayed")


def test_bytes_and_cache():
    print("file read, cache, missing file")
    tmp = Path(tempfile.mkdtemp(prefix="igv_annot_"))
    gff = tmp / "ref.gff"
    gff.write_text(NCBI)
    a = igv_annotation.igv_gff3_bytes(gff)
    check(a.decode().splitlines()[0], "##gff-version 3", "bytes are the rewrite")
    check(igv_annotation.igv_gff3_bytes(gff) is a, True, "second read served from the cache")
    gff.write_text(NCBI.replace("pepC", "pepX"))
    check(b"Name=pepX" in igv_annotation.igv_gff3_bytes(gff), True, "a changed file is read again")
    for bad in (tmp / "missing.gff", tmp):
        try:
            igv_annotation.igv_gff3_bytes(bad)
            check("no error", "FileNotFoundError", f"{'a directory' if bad == tmp else bad.name} raises")
        except FileNotFoundError:
            check(True, True, f"{'a directory' if bad == tmp else bad.name} raises FileNotFoundError")


def test_endpoint():
    print("endpoint: path rule as /serve")
    tmp = Path(tempfile.mkdtemp(prefix="igv_annot_ep_"))
    root = tmp / "refs"
    root.mkdir()
    (root / "ref.gff").write_text(NCBI)
    (tmp / "outside.gff").write_text(NCBI)
    saved = app_main._serve_roots
    app_main._serve_roots = lambda project: app_main._serve_root_forms([root])
    try:
        r = app_main.igv_annotation_file("p", path=str(root / "ref.gff"))
        check(r.status_code, 200, "allowed path served")
        check(r.media_type, "text/plain; charset=utf-8", "served as text")
        for path, code in ((tmp / "outside.gff", 400), (root / ".." / "outside.gff", 400), (root / "nope.gff", 404)):
            try:
                app_main.igv_annotation_file("p", path=str(path))
                check("served", code, str(path))
            except HTTPException as e:
                check(e.status_code, code, f"{path.name} -> {code}")
    finally:
        app_main._serve_roots = saved


def test_real_references():
    refs = Path.home() / ".local/share/bdtools/vsnp3-refs"
    gffs = sorted(p for pat in ("*.gff", "*.gff3") for p in refs.rglob(pat)) if refs.is_dir() else []
    if not gffs:
        print("real references: none on this machine, skipped")
        return
    print(f"real references: {len(gffs)} GFFs")
    for gff in gffs:
        src = gff.read_text(errors="surrogateescape")
        out = igv_annotation.igv_gff3(src)
        f = features(out)
        unnamed = [a.get("ID") for t, _, a in f
                   if (igv_annotation._is_gene(t) or t.endswith("RNA")) and not a.get("Name")]
        src_genes = sum(1 for t, _, _ in features(src) if igv_annotation._is_gene(t))
        out_genes = sum(1 for t, _, _ in f if igv_annotation._is_gene(t))
        ok = out.startswith("##gff-version 3\n") and not unnamed and src_genes == out_genes
        check(ok, True, f"{gff.parent.name}/{gff.name}: {out_genes} genes, all named, "
                        f"{sum(1 for t, _, _ in f if t == 'CDS')} CDS left")


if __name__ == "__main__":
    test_ncbi()
    test_prokka()
    test_keep()
    test_bytes_and_cache()
    test_endpoint()
    test_real_references()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        sys.exit(1)
    print("all passed")
