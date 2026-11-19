"""The mature protein boundaries and the rewritten reference records
(bin/fmdv_proteins.py).

What these pin:
  * the eight spellings NCBI uses for the 12 FMDV proteins map to one name
    each, and VPg1/2/3 merge into 3B;
  * a boundary set transferred by protein alignment survives a substitution,
    an insertion and a deletion in the target, stays in frame, tiles the
    coding sequence and keeps every protein near its donor's length;
  * a transfer that lands a protein far from its donor's length is refused;
  * the rewritten GenBank text parses to 12 CDS features whose translations
    are the polyprotein slices, with the polyprotein kept as a misc_feature,
    3D running to the stop codon, and the GFF carrying the same 12 lines on
    the bare accession.

    <conda>/bin/python backend/app/test_fmdv_proteins.py
"""
from __future__ import annotations

import importlib.util
import io
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("fmdv_proteins", ROOT / "bin" / "fmdv_proteins.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


def test_protein_names():
    cases = {
        "Lab": "L", "LAB": "L", "L": "L", "Lab peptide": "L",
        "Lab peptide; leader peptide": "L", "leader proteinase": "L",
        "VP4 peptide": "VP4", "VP1": "VP1", "2A peptide": "2A", "2C": "2C",
        "3B": "3B", "3B1-3B3": "3B", "3B1/3B2/3B3 peptide": "3B",
        "3B peptide; 3B1/3B2/3B3 peptide": "3B", "VPg1": "3B", "VPg3": "3B",
        "3C peptide; pro peptide": "3C", "3C proteinase": "3C",
        "pol peptide; 3D peptide": "3D", "3D": "3D", "polyprotein": None,
    }
    for product, want in cases.items():
        check(fp.canonical_protein(product), want, f"canonical_protein({product!r})")


# A toy polyprotein: 12 proteins of distinct lengths, 240 residues in all.
LENGTHS = [30, 12, 25, 25, 28, 6, 20, 30, 18, 9, 17, 20]
random.seed(7)
AA = "ACDEFGHIKLMNPQRSTVWY"
DONOR = "M" + "".join(random.choice(AA) for _ in range(sum(LENGTHS) - 1))


def donor_res():
    out, pos = [], 0
    for name, n in zip(fp.PROTEINS, LENGTHS):
        out.append((name, pos, pos + n))
        pos += n
    return out


def test_transfer_survives_indels():
    d = donor_res()
    # Target: a substitution inside VP2, two residues inserted inside 2C and
    # one deleted inside 3D. Boundaries should move with the indels and
    # nothing else should change.
    t = list(DONOR)
    t[d[2][1] + 5] = "W" if t[d[2][1] + 5] != "W" else "Y"
    target = "".join(t)
    ins = d[7][1] + 10
    target = target[:ins] + "GG" + target[ins:]
    dele = d[11][1] + 2 + 5
    target = target[:dele] + target[dele + 1:]
    res, identity = fp.transfer(DONOR, d, target)
    check([r[0] for r in res], list(fp.PROTEINS), "names in genome order")
    check(fp.check(res, d, len(target)), [], "transfer passes every check")
    lengths = [e - s for _, s, e in res]
    want = list(LENGTHS)
    want[7] += 2
    want[11] -= 1
    check(lengths, want, "2C grows by the insertion, 3D shrinks by the deletion, others unchanged")
    check(identity > 98.0, True, f"identity reported ({identity:.1f})")
    ivs = fp.genome_intervals(res, 101)
    check(ivs[0][1], 101, "L starts at the CDS start")
    check(all((e - s + 1) % 3 == 0 for _, s, e in ivs), True, "every interval is whole codons")
    check(all(ivs[i][2] + 1 == ivs[i + 1][1] for i in range(11)), True, "intervals tile with no gap")


def test_transfer_refuses_a_bad_landing():
    d = donor_res()
    # A target missing most of 2B: the transfer still produces 12 ranges but
    # 2B is far shorter than its donor, and check() must say so.
    target = DONOR[:d[6][1]] + DONOR[d[6][1] + 15:]
    res, _ = fp.transfer(DONOR, d, target)
    problems = fp.check(res, d, len(target))
    check(any(p.startswith("2B is") for p in problems), True, f"short 2B refused: {problems}")



def test_rewrite_round_trip():
    from Bio import SeqIO
    from Bio.Data import CodonTable
    from Bio.Seq import Seq

    back = {}
    for codon, aa in CodonTable.unambiguous_dna_by_id[1].forward_table.items():
        back.setdefault(aa, codon)
    cds = "".join(back[a] for a in DONOR) + "TAA"
    lead, tail = "ACGT" * 10, "TTTT" * 5
    genome = lead + cds + tail
    cds_start, cds_end = len(lead) + 1, len(lead) + len(cds)
    d = donor_res()
    ivs = fp.genome_intervals(d, cds_start)
    acc = "TEST00001"

    translation_lines = fp._qualifier_lines("translation", DONOR)
    gbk = "\n".join([
        f"LOCUS       {acc:<16}{len(genome):>12} bp    RNA     linear   VRL 01-JAN-2026",
        "DEFINITION  toy FMDV genome.",
        f"ACCESSION   {acc}",
        f"VERSION     {acc}",
        "FEATURES             Location/Qualifiers",
        f"     source          1..{len(genome)}",
        '                     /organism="toy"',
        '                     /mol_type="genomic RNA"',
        f"     CDS             {cds_start}..{cds_end}",
        "                     /codon_start=1",
        '                     /product="polyprotein"',
        '                     /protein_id="TOY00001.1"',
        *translation_lines,
        "ORIGIN",
    ] + [f"{i + 1:>9} " + " ".join(genome[i + k:i + k + 10] for k in range(0, 60, 10))
         for i in range(0, len(genome), 60)] + ["//", ""])
    gff = "\n".join([
        "##gff-version 3",
        f"{acc}\tGenbank\tregion\t1\t{len(genome)}\t.\t+\t.\tID={acc}.1:1..{len(genome)}",
        f"{acc}\tGenbank\tCDS\t{cds_start}\t{cds_end}\t.\t+\t0\tID=cds-TOY00001.1;product=polyprotein",
        "",
    ])

    new_gbk = fp.rewrite_gbk(gbk, ivs, d, DONOR, cds_start, cds_end)
    rec = next(SeqIO.parse(io.StringIO(new_gbk), "genbank"))
    cdss = [f for f in rec.features if f.type == "CDS"]
    check([f.qualifiers["gene"][0] for f in cdss], list(fp.PROTEINS), "12 CDS features, one per protein")
    check([f.qualifiers["transl_table"][0] for f in cdss], ["1"] * 12, "standard code declared")
    for f, (name, s, e) in zip(cdss, d):
        tr = f.qualifiers["translation"][0]
        check(tr, DONOR[s:e], f"{name} translation is the polyprotein slice")
        check(str(Seq(str(f.extract(rec.seq))).translate()).rstrip("*"), tr,
              f"{name} translation matches its own sequence")
        check(f.qualifiers["product"][0], f"{name} (polyprotein {s + 1}-{e})", f"{name} product")
    check(int(cdss[-1].location.end), cds_end, "3D runs to the stop codon")
    check(int(cdss[0].location.start) + 1, cds_start, "L starts at the polyprotein start")
    misc = [f for f in rec.features if f.type == "misc_feature"]
    check(len(misc), 1, "polyprotein kept once as a misc_feature")
    check((int(misc[0].location.start) + 1, int(misc[0].location.end)), (cds_start, cds_end),
          "misc_feature spans the ORF")
    check(misc[0].qualifiers.get("product"), ["polyprotein"], "misc_feature still says polyprotein")
    check("translation" in misc[0].qualifiers, False, "misc_feature carries no translation")
    check(max(len(line) for line in new_gbk.splitlines()) <= 79, True, "no line past column 79")
    check(fp.MARKER in " ".join(misc[0].qualifiers["note"]), True, "marker note present after parsing")

    new_gff = fp.rewrite_gff(gff, acc, ivs, d, cds_start, cds_end)
    rows = [l.split("\t") for l in new_gff.splitlines() if l and not l.startswith("#")]
    cds_rows = [r for r in rows if r[2] == "CDS"]
    check(len(cds_rows), 12, "12 CDS rows in the GFF")
    check({r[0] for r in rows}, {acc}, "first column is the bare accession")
    check([r[8].split(";")[1] for r in cds_rows], [f"Name={n}" for n in fp.PROTEINS], "GFF names")
    check(int(cds_rows[-1][4]), cds_end, "GFF 3D runs to the stop codon")
    check(any(r[8].endswith("product=polyprotein") for r in rows), False, "no polyprotein CDS row left")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — fmdv mature protein features")
