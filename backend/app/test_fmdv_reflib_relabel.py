"""Giving a library genome the topotype a reference laboratory has named.

`fmdv_reflib.py add` lists a genome as unassigned when no annotated VP1 in
the typing set is near enough to name its topotype. OQ557397 (ETH/2/2022)
went in that way: the typing set holds no SAT2 topotype XIV record, so the
nearest annotated VP1 was 24% away. The WRL sequence metadata names it XIV,
and `relabel` moves the label in.

The safe name carries the topotype, so a relabel is a rename across the
library: the genome FASTA and its header, the annotation files and the
protein coordinate table, both panels, the hierarchy the picker reads, and
the typing set's own copies of that accession. The typing report quotes the
best hit's label and the typing set's slice of the genome scores as well as
the panel entry, so a label left there would still report a sample without
a topotype. This checks every one of those moves.

Run directly:  python test_fmdv_reflib_relabel.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_BIN = Path(__file__).resolve().parents[2] / "bin" / "fmdv_reflib.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fmdv_reflib", _BIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}")


def assert_true(cond, label):
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def make_library(root: Path) -> Path:
    """A library of two SAT2 genomes, one labelled and one unassigned."""
    lib = root / "lib"
    for sub in ("genomes", "panels", "annotations", "vp1_context", "reference_options"):
        (lib / sub).mkdir(parents=True)
    genomes = [
        {"id": "SAT2/XIII_AY343939_1977_SUD/6/77", "serotype": "SAT2", "lineage": "XIII",
         "accession": "AY343939", "year": "1977", "isolate": "SUD/6/77",
         "safe": "SAT2-XIII_AY343939", "length": 8100, "n_count": 0, "source": "test"},
        {"id": "SAT2_OQ557397_2022_ETH/2/2022", "serotype": "SAT2", "lineage": "",
         "accession": "OQ557397", "year": "2022", "isolate": "ETH/2/2022",
         "safe": "SAT2-NA_OQ557397", "length": 8205, "n_count": 0,
         "source": "GenBank OQ557397, added 2026-11-22 by fmdv_reflib.py add"},
    ]
    (lib / "lineages.json").write_text(json.dumps(
        {"genomes": genomes, "n_genomes": 2,
         "tree": {"SAT2": {"XIII": ["SAT2-XIII_AY343939"], "(unassigned)": ["SAT2-NA_OQ557397"]}}}, indent=1))
    for g in genomes:
        (lib / "genomes" / f"{g['safe']}.fasta").write_text(f">{g['safe']}\n{'ACGT' * 60}\n")
    (lib / "panels" / "genomes.fasta").write_text(
        "".join(f">{g['safe']}\n{'ACGT' * 60}\n" for g in genomes))
    (lib / "panels" / "vp1.fasta").write_text(
        ">SAT2-XIII_AY343939\nACGTACGT\n>SAT2-NA_OQ557397\nTTGGCCAA\n")
    (lib / "annotations" / "raw").mkdir()
    for ext in (".gbk", ".gff"):
        (lib / "annotations" / f"SAT2-NA_OQ557397{ext}").write_text("rewritten\n")
        (lib / "annotations" / "raw" / f"SAT2-NA_OQ557397{ext}").write_text("as downloaded\n")
    (lib / "annotations" / "protein_coords.json").write_text(json.dumps(
        {"genomes": {"SAT2-XIII_AY343939": {"VP1": [1, 2]}},
         "failed": {"SAT2-NA_OQ557397": ["expected one CDS, found 12"]},
         "donors": ["SAT2-NA_OQ557397", "SAT2-XIII_AY343939"], "n_genomes": 1}, indent=1))
    # the typing set holds the slice taken from the same genome, and its own
    # copy of the other accession, which must not move
    (lib / "vp1_context" / "SAT2.fasta").write_text(
        ">SAT2-XIII_AY343939\nACGTACGT\n>SAT2-NA_OQ557397.1\nTTGGCCAA\n")
    cols = ["name", "serotype", "topotype", "country", "year", "host", "accession",
            "length", "isolate", "region_source"]
    (lib / "vp1_context" / "SAT2.tsv").write_text(
        "\t".join(cols) + "\n"
        + "SAT2-XIII_AY343939\tSAT2\tXIII\tSUDAN\t1977\tBOVINE\tAY343939\t648\tSUD/6/77\tvp1_record\n"
        + "SAT2-NA_OQ557397.1\tSAT2\t\t\t\t\tOQ557397.1\t648\tETH/2/2022\tsliced_from_genome\n")
    return lib


def main() -> int:
    mod = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        lib = make_library(Path(tmp))
        rec = mod.relabel_genome(lib, "OQ557397", "XIV", "WRL Pirbright sequence metadata")

        assert_eq(rec["safe"], "SAT2-XIV_OQ557397", "the safe name carries the topotype")
        assert_eq(rec["lineage"], "XIV", "the record's lineage")
        assert_eq(rec["id"], "SAT2/XIV_OQ557397_2022_ETH/2/2022", "the display id")
        assert_true("topotype XIV assigned" in rec["source"], "the record says the topotype was assigned")
        assert_true("WRL Pirbright sequence metadata" in rec["source"], "and where it came from")

        idx = json.loads((lib / "lineages.json").read_text())
        assert_eq(idx["tree"]["SAT2"].get("XIV"), ["SAT2-XIV_OQ557397"], "the picker's hierarchy")
        assert_true("(unassigned)" not in idx["tree"]["SAT2"], "the emptied unassigned bucket is dropped")
        assert_eq(idx["tree"]["SAT2"]["XIII"], ["SAT2-XIII_AY343939"], "the other genome stays put")

        assert_true((lib / "genomes" / "SAT2-XIV_OQ557397.fasta").is_file(), "the genome FASTA is renamed")
        assert_true(not (lib / "genomes" / "SAT2-NA_OQ557397.fasta").exists(), "the old FASTA is gone")
        assert_eq((lib / "genomes" / "SAT2-XIV_OQ557397.fasta").read_text().splitlines()[0],
                  ">SAT2-XIV_OQ557397", "and its header with it")

        for ext in (".gbk", ".gff"):
            assert_true((lib / "annotations" / f"SAT2-XIV_OQ557397{ext}").is_file(),
                        f"the annotation {ext} is renamed")
        for ext in (".gbk", ".gff"):
            assert_true((lib / "annotations" / "raw" / f"SAT2-XIV_OQ557397{ext}").is_file(),
                        f"the untouched download {ext} is renamed with it")
            assert_true(not (lib / "annotations" / "raw" / f"SAT2-NA_OQ557397{ext}").exists(),
                        f"and none left under the old name ({ext})")
        coords = json.loads((lib / "annotations" / "protein_coords.json").read_text())
        assert_eq(sorted(coords["failed"]), ["SAT2-XIV_OQ557397"],
                  "the protein table's failed entry is rekeyed")
        assert_eq(coords["donors"], ["SAT2-XIII_AY343939", "SAT2-XIV_OQ557397"],
                  "and its donor list")

        panel = (lib / "panels" / "vp1.fasta").read_text()
        assert_true(">SAT2-XIV_OQ557397\n" in panel and "SAT2-NA" not in panel,
                    "the VP1 panel entry is relabelled")
        assert_true(">SAT2-XIV_OQ557397\n" in (lib / "panels" / "genomes.fasta").read_text(),
                    "the genome panel is rewritten")

        ctx = (lib / "vp1_context" / "SAT2.fasta").read_text()
        assert_true(">SAT2-XIV_OQ557397.1\n" in ctx, "the typing set's copy is relabelled")
        assert_true("SAT2-NA" not in ctx, "with nothing left under the old label")
        assert_true(">SAT2-XIII_AY343939\n" in ctx, "and the other accession untouched")
        rows = [r.split("\t") for r in (lib / "vp1_context" / "SAT2.tsv").read_text().splitlines()]
        moved = next(r for r in rows if r[6].startswith("OQ557397"))
        assert_eq(moved[0], "SAT2-XIV_OQ557397.1", "the provenance table's name")
        assert_eq(moved[2], "XIV", "the provenance table's topotype")
        kept = next(r for r in rows if r[6] == "AY343939")
        assert_eq(kept[2], "XIII", "the other row's topotype is left alone")

        # `add` asserts a topotype of its own, so it moves the same label
        mod._relabel_typing_set(lib, "SAT2", "AY343939", "XIII-b")
        rows = [r.split("\t") for r in (lib / "vp1_context" / "SAT2.tsv").read_text().splitlines()]
        assert_eq(next(r for r in rows if r[6] == "AY343939")[2], "XIII-b",
                  "a label asserted on add reaches the typing set")

        for bad, label in ((("OQ557397", "XIV"), "relabelling to the label it already has"),
                           (("ZZ000000", "XIV"), "an accession the library does not hold")):
            try:
                mod.relabel_genome(lib, *bad)
            except SystemExit:
                print(f"  OK  refused: {label}")
            else:
                raise AssertionError(f"not refused: {label}")

    print("\ntest_fmdv_reflib_relabel: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
