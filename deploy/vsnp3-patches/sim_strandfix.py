"""Simulation harness modelled on Vivek's: sample SNPs across H37Rv CDS
features, compute truth by extracting the CDS in gene orientation, and compare
against whatever vsnp3_annotation is on sys.path.

Restricted to positions covered by exactly one single-part CDS whose length is a
multiple of three, so overlapping-feature ambiguity and multi-part features do
not muddy the comparison.

Deterministic: seeded, so a patched and an unpatched run see the same draw.
"""
import random
import sys
from collections import defaultdict

from Bio import SeqIO
from Bio.Seq import Seq

GBK = "/srv/kapurlab/refs/vsnp3/reference_options/Mycobacterium_H37/NC_000962.gbk"
CHROM = "NC_000962.3"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
SEED = 20260801


def main():
    from vsnp3_annotation import Annotation
    mod = sys.modules["vsnp3_annotation"]
    print(f"module : {mod.__file__}")
    print(f"version: {getattr(mod, '__version__', '?')}")

    rec = SeqIO.to_dict(SeqIO.parse(GBK, "genbank"))[CHROM]
    genome = rec.seq

    # Count CDS coverage per position so we can keep only unambiguous ones.
    cds = [f for f in rec.features if f.type == "CDS" and len(f.location.parts) == 1]
    cover = defaultdict(int)
    for f in cds:
        for p in range(int(f.location.start), int(f.location.end)):
            cover[p] += 1

    usable = [
        f for f in cds
        if (int(f.location.end) - int(f.location.start)) % 3 == 0
        and (int(f.location.end) - int(f.location.start)) >= 6
    ]

    rng = random.Random(SEED)
    picks = []
    guard = 0
    while len(picks) < N and guard < N * 60:
        guard += 1
        f = rng.choice(usable)
        pos0 = rng.randrange(int(f.location.start), int(f.location.end))
        if cover[pos0] != 1:
            continue
        ref = str(genome[pos0]).upper()
        if ref not in "ACGT":
            continue
        alt = rng.choice([b for b in "ACGT" if b != ref])
        picks.append((f, pos0, ref, alt))

    ann = Annotation(gbk_list=[GBK])
    stats = defaultdict(lambda: [0, 0, 0])  # strand -> [n, aa_ok, syn_ok]

    for f, pos0, ref, alt in picks:
        start, end = int(f.location.start), int(f.location.end)
        minus = f.location.strand == -1

        # --- truth: build the CDS in gene orientation and substitute there ---
        cds_seq = genome[start:end]
        mutated = genome[:pos0] + Seq(alt) + genome[pos0 + 1:]
        mut_cds = mutated[start:end]
        if minus:
            cds_seq = cds_seq.reverse_complement()
            mut_cds = mut_cds.reverse_complement()
            gene_off = end - 1 - pos0
        else:
            gene_off = pos0 - start
        ci = gene_off // 3
        true_ref_aa = str(Seq(str(cds_seq[ci * 3:ci * 3 + 3])).translate())
        true_alt_aa = str(Seq(str(mut_cds[ci * 3:ci * 3 + 3])).translate())
        true_syn = (true_ref_aa == true_alt_aa)

        # --- vsnp3 ---
        ann.run(f"{CHROM}:{pos0 + 1}", alt)
        got_ref, got_alt = ann.ref_aa, ann.snp_aa
        got_syn = (ann.mutation_type == "silent mutation")

        key = "minus" if minus else "plus"
        stats[key][0] += 1
        if got_ref == true_ref_aa and got_alt == true_alt_aa:
            stats[key][1] += 1
        if got_syn == true_syn:
            stats[key][2] += 1

    print(f"\n{'strand':8} {'n':>6} {'ref+alt AA correct':>20} {'silent/nonsyn correct':>23}")
    for key in ("plus", "minus"):
        n, aa, syn = stats[key]
        if not n:
            continue
        print(f"{key:8} {n:6} {aa / n * 100:19.1f}% {syn / n * 100:22.1f}%")


if __name__ == "__main__":
    main()
