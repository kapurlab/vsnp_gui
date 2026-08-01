"""Reproduce Vivek's two positive controls against whichever vsnp3 install is
on sys.path, using the H37Rv GenBank record shipped with the reference option.

  rpoB S450L  Rv0667  plus strand   761155  C>T   -> residue 450, S->L, nonsyn
  katG S315T  Rv1908c minus strand  2155168 C>G   -> residue 315, S->T, nonsyn
"""
import sys

GBK = "/srv/kapurlab/refs/vsnp3/reference_options/Mycobacterium_H37/NC_000962.gbk"
CHROM = "NC_000962.3"

CONTROLS = [
    ("rpoB S450L (plus strand)", 761155, "T", 450, "S", "L"),
    ("katG S315T (minus strand)", 2155168, "G", 315, "S", "T"),
]


def main():
    from vsnp3_annotation import Annotation
    print(f"module: {sys.modules['vsnp3_annotation'].__file__}")
    print(f"version: {getattr(sys.modules['vsnp3_annotation'], '__version__', '?')}")
    print()
    ok = True
    for label, pos, alt, exp_res, exp_ref_aa, exp_alt_aa in CONTROLS:
        ann = Annotation(gbk_list=[GBK])
        ann.run(f"{CHROM}:{pos}", alt)
        a = ann
        got_res = getattr(a, "aa_residue_pos", None)
        got_ref = getattr(a, "ref_aa", None)
        got_alt = getattr(a, "snp_aa", None)
        mut = getattr(a, "mutation_type", None)
        good = (got_res == exp_res and got_ref == exp_ref_aa and got_alt == exp_alt_aa)
        ok = ok and good
        print(f"{'PASS' if good else 'FAIL'}  {label}")
        print(f"      expected residue {exp_res} {exp_ref_aa}->{exp_alt_aa}")
        print(f"      got      residue {got_res} {got_ref}->{got_alt}  ({mut})")
        print(f"      gene={getattr(a, 'gene', None)} product={getattr(a, 'product', None)!r}")
        print(f"      ref codon={getattr(a, 'reference_base_code', None)} "
              f"alt codon={getattr(a, 'snp_base_code', None)}")
        print()
    print("ALL CONTROLS PASS" if ok else "SOME CONTROLS FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
