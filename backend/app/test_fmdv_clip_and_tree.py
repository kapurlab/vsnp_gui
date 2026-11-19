"""Primer placement (bin/fmdv_clip_primers.py) and VP1 tree inputs
(bin/fmdv_vp1_tree.py), the parts that need no aligner.

What these pin:
  * a primer is placed at its best window on either strand, IUPAC codes
    matching, and the BED is 0-based half-open with the strand recorded;
  * a primer under the fit floor is left out of the BED and listed, instead of
    clipping a random stretch of genome;
  * the BED is cached per reference and primer set and reused;
  * serotype comes off the safe name (ASIA1-ASIA-... is ASIA1), tree labels
    are Newick-safe, and sample collection honours the serotype filter and
    the VP1-called floor; the vp1_context hook is included only when present.

    <conda>/bin/python backend/app/test_fmdv_clip_and_tree.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


clip = load("fmdv_clip_primers")
tree = load("fmdv_vp1_tree")

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


GENOME = "TTTTTTTTTT" + "ACGTACGTACGTACGTACGT" + "GGGGGGGGGG" + "CATCATCATCATCATCATCA" + "TTTTTTTTTT"


def test_best_site_both_strands_and_iupac():
    fit, s, e, strand = clip.best_site(GENOME, "ACGTACGTACGTACGTACGT")
    check((fit, s, e, strand), (1.0, 10, 30, "+"), "exact forward match")
    rc = clip.revcomp("CATCATCATCATCATCATCA")
    fit, s, e, strand = clip.best_site(GENOME, rc)
    check((fit, s, e, strand), (1.0, 40, 60, "-"), "reverse-strand primer placed on its site")
    fit, s, e, strand = clip.best_site(GENOME, "ACGTACGTACRTACGTACGT")
    check((fit, strand), (1.0, "+"), "R matches A or G")
    fit, _, _, _ = clip.best_site(GENOME, "ACGTACGTACTTTCGTACGT")
    check(round(fit, 2), 0.9, "two mismatches of twenty is 0.90")


def test_bed_floor_and_cache():
    with tempfile.TemporaryDirectory() as td:
        ref = Path(td) / "SAT1-I_X.fasta"
        ref.write_text(">KM268899 test\n" + GENOME + "\n")
        primers = Path(td) / "set.fasta"
        primers.write_text(">good_F\nACGTACGTACGTACGTACGT\n>good_R\n" + clip.revcomp("CATCATCATCATCATCATCA")
                           + "\n>other_serotype\nAAACCCGGGTTTAAACCCGG\n")
        cache = Path(td) / "cache"
        bed, info = clip.primer_bed(ref, primers, cache, 0.8)
        rows = [ln.split("\t") for ln in bed.read_text().splitlines()]
        check([r[3] for r in rows], ["good_F", "good_R"], "only primers at or over the fit floor are in the BED")
        check(rows[0][:3], ["KM268899", "10", "30"], "chrom from the FASTA header, 0-based start, exclusive end")
        check(rows[1][5], "-", "strand recorded")
        check([w["primer"] for w in info["below_fit"]], ["other_serotype"], "the weak primer is listed")
        mtime = bed.stat().st_mtime
        bed2, info2 = clip.primer_bed(ref, primers, cache, 0.8)
        check((bed2, bed2.stat().st_mtime), (bed, mtime), "second call reuses the cached BED")
        check(info2["placed"], info["placed"], "and the cached metadata")


def test_serotype_and_labels():
    for safe, want in (("SAT1-I_KM268899", "SAT1"), ("ASIA1-ASIA-Sindh-08_KM268898", "ASIA1"),
                       ("O-ME-SA-PanAsia_HM008917", "O"), ("A-NA_AY593791", "A"), ("", "")):
        check(tree.serotype_of(safe), want, f"serotype_of({safe!r})")
    check(tree.tree_label("ETH SAT1/001 x:y"), "ETH", "label is the first token")
    check(tree.tree_label("SAT1/IX:ETH(3)"), "SAT1_IX_ETH_3_", "unsafe characters replaced")


def test_collect_samples_and_panel():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "proj"
        for name, ref, called in (("S1", "SAT1-I_K", 100.0), ("S2", "SAT1-I_K", 20.0), ("A1", "A-NA_X", 99.0)):
            d = project / "step1" / name
            d.mkdir(parents=True)
            (d / "fmdv_consensus.json").write_text(json.dumps({"reference": ref, "vp1_called_percent": called}))
            (d / f"{name}_VP1.fasta").write_text(f">{name} VP1\nACGTACGT\n")
        (project / "step1" / "noVP1").mkdir()
        seqs, report = tree.collect_samples(project, "SAT1", 50.0)
        check([s for s, _ in seqs], ["S1"], "SAT1 filter keeps S1; S2 is under the called floor; A1 is another serotype")
        reasons = {r["sample"]: r["reason"] for r in report}
        check("below 50.0%" in reasons["S2"], True, "S2 excluded for coverage, with the reason")
        check(reasons["A1"], "serotype A", "A1 excluded for serotype")
        check("noVP1" in reasons, False, "a folder with no consensus is not reported at all")
        seqs, _ = tree.collect_samples(project, None, 50.0)
        check(sorted(s for s, _ in seqs), ["A1", "S1"], "no serotype filter: every serotype over the floor")

        lib = Path(td) / "lib"
        (lib / "panels").mkdir(parents=True)
        (lib / "panels" / "vp1.fasta").write_text(">SAT1-I_K\nAAAA\n>SAT1-II_M\nCCCC\n>A-NA_X\nGGGG\n")
        # collect_panel(lib, serotype, samples_by_sero, per_topotype, neighbours, collapse)
        seqs, sources, _rows, _sel = tree.collect_panel(lib, "SAT1", {}, 20, 20, True)
        check([s for s, _ in seqs], ["SAT1-I_K", "SAT1-II_M"], "panel restricted to the serotype")
        check(len(sources), 1, "no context file: panel only")
        (lib / "vp1_context").mkdir()
        (lib / "vp1_context" / "SAT1.fasta").write_text(">SAT1-IX_ETH1 Ethiopia\nTTTT\n")
        seqs, sources, rows, sel = tree.collect_panel(lib, "SAT1", {}, 20, 20, True)
        check([s for s, _ in seqs][-1], "SAT1-IX_ETH1", "context set appended when present")
        check(rows[0]["why"] if rows else "", "backbone", "and the selection file says why it is there")
        check(len(sources), 2, "and listed as a source")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — primer placement and VP1 tree inputs")
