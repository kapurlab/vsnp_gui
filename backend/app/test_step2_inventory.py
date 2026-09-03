"""One membership rule for step2/vcf_database, and what a run is allowed to stage.

These pin the defect that produced a comparison of 26 VCFs from a list of 25
names. Three readers disagreed about which files were in the database:

  * the browse/selection list globbed *_zc.vcf(.gz) -> could not see the extra file
  * the card's count accepted any .vcf/.vcf.gz     -> counted it
  * staging accepted any .vcf/.vcf.gz              -> analysed it

so a file without the _zc marker was unlistable, unexcludable and in every run.
The import path manufactures such names, so it was never only hand-copied files.

  cd backend/app && ../../env/bin/python test_step2_inventory.py
"""

import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.step2_inventory import (  # noqa: E402
    db_entries, duplicate_samples, import_tail, is_analyzable, is_db_vcf,
    sample_of, stage_entry,
)
from app.step2_staging import stage_step2_vcfs, vsnp3_would_remove  # noqa: E402

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


VCF_TEXT = (
    "##fileformat=VCFv4.2\n"
    "##reference=file:///refs/Mycobacterium_AF2122/Mycobacterium_AF2122.fasta\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
    "NC_002945.4\t1000\t.\tA\tG\t500\tPASS\tDP=40\tGT:DP\t1/1:40\n"
)


def _db(tmp, names):
    d = Path(tmp) / "vcf_database"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        if n.endswith(".gz"):
            with gzip.open(d / n, "wt") as fh:
                fh.write(VCF_TEXT)
        else:
            (d / n).write_text(VCF_TEXT)
    return d


def test_membership_is_suffix_only():
    """Anything a run would read is in the set — no naming convention required."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, [
            "A_zc.vcf", "B_zc.vcf.gz", "C.vcf", "D_import1_zc.vcf", "E.vcf.gz",
            ".hidden_zc.vcf", "notes.txt",
        ])
        (d / "notes.txt").write_text("not a vcf")
        entries = db_entries(d)
        check({e.filename for e in entries},
              {"A_zc.vcf", "B_zc.vcf.gz", "C.vcf", "D_import1_zc.vcf", "E.vcf.gz"},
              "membership is decided by suffix, not by the _zc marker")
        check({e.sample for e in entries}, {"A", "B", "C", "D_import1", "E"},
              "sample names strip the longest matching tail")
        check([e.sample for e in entries if e.compressed], ["B", "E"],
              "compressed entries are flagged")


def test_sample_derivation_strips_suffixes_not_substrings():
    """The old chained str.replace() gutted the middle of a name."""
    check(sample_of("run.vcf.backup_zc.vcf"), "run.vcf.backup",
          "only the trailing marker is removed")
    check(sample_of("16-014020-020_Bovine_Maiduguri-NG.vcf"),
          "16-014020-020_Bovine_Maiduguri-NG", "a plain .vcf yields its bare name")
    check(sample_of("A.vcf.gz"), "A", "a gzipped plain .vcf keeps no .vcf tail")


def test_analyzable_mirrors_vsnp3_glob():
    """vsnp3 discovers inputs with glob('*vcf') — a .gz is never opened."""
    check(is_analyzable("A_zc.vcf"), True, "plain vcf is read")
    check(is_analyzable("A_zc.vcf.gz"), False, "gzipped vcf is invisible to vsnp3")
    check(is_db_vcf("A_zc.vcf.gz"), True, "...but it IS a database entry")


def test_import_tail_keeps_a_derivable_name():
    """Path.stem/suffixes produced X_zc.vcf_import1.vcf.gz — unreadable by every reader."""
    stem, tail = import_tail("X_zc.vcf.gz")
    check(f"{stem}_import1{tail}", "X_import1_zc.vcf.gz", "disambiguator goes before the tail")
    check(sample_of("X_import1_zc.vcf.gz"), "X_import1", "and the sample still derives")


def test_include_is_an_allowlist():
    """The reported bug, minimally: a file nobody named is not staged."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, ["A_zc.vcf", "B_zc.vcf", "16-014020-020_Bovine_Maiduguri-NG.vcf"])
        run = Path(tmp) / "run"
        run.mkdir()
        copied, skipped, staged = stage_step2_vcfs(d, run, include_samples=["A"])
        check(copied, 1, "exactly one file staged")
        check(staged, {"A_zc.vcf"}, "and it is the one that was asked for")
        check(sorted(p.name for p in run.iterdir()), ["A_zc.vcf"],
              "the unnamed non-_zc VCF is NOT in the run folder")


def test_denylist_path_still_works_for_an_older_client():
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, ["A_zc.vcf", "B_zc.vcf"])
        run = Path(tmp) / "run"
        run.mkdir()
        copied, skipped, staged = stage_step2_vcfs(d, run, removal_names=["B"])
        check((copied, skipped, staged), (1, 1, {"A_zc.vcf"}),
              "no include -> the previous removal-driven behaviour")


def test_gz_is_decompressed_so_vsnp3_can_read_it():
    """A staged .gz was counted as compared and never opened."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, ["A_zc.vcf.gz"])
        run = Path(tmp) / "run"
        run.mkdir()
        copied, _, staged = stage_step2_vcfs(d, run, include_samples=["A"])
        check(copied, 1, "the gzipped entry is staged")
        check(staged, {"A_zc.vcf"}, "under a name vsnp3 will glob")
        check([p.name for p in run.iterdir()], ["A_zc.vcf"], "and nothing .gz is left behind")
        check((run / "A_zc.vcf").read_text(), VCF_TEXT, "with the real, decompressed content")
        check(all(is_analyzable(p.name) for p in run.iterdir()), True,
              "every staged file satisfies vsnp3's discovery rule")


def test_excluded_gz_is_not_staged():
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, ["A_zc.vcf.gz"])
        run = Path(tmp) / "run"
        run.mkdir()
        copied, _, _ = stage_step2_vcfs(d, run, include_samples=[])
        check((copied, list(run.iterdir())), (0, []),
              "an unselected gz is not copied (no tier could ever remove it)")


def test_two_files_for_one_sample_is_refused_not_guessed():
    """A_zc.vcf and an edited A_zc.vcf.gz hold different calls."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, ["A_zc.vcf", "A_zc.vcf.gz"])
        check(duplicate_samples(db_entries(d)),
              {"A": ["A_zc.vcf", "A_zc.vcf.gz"]}, "the clash is reported")
        run = Path(tmp) / "run"
        run.mkdir()
        try:
            stage_step2_vcfs(d, run, include_samples=["A"])
            FAILS.append("staging picked one of two files for a sample instead of refusing")
        except ValueError as exc:
            check("more than one VCF" in str(exc), True, f"refusal explains itself: {exc}")


def test_no_partial_file_is_ever_visible():
    """A reader polling the run folder must never see a half-written VCF."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _db(tmp, ["A_zc.vcf.gz"])
        run = Path(tmp) / "run"
        run.mkdir()
        import app.step2_inventory as inv
        real = inv.shutil.copyfileobj

        def boom(*a, **k):
            raise OSError("disk full")
        inv.shutil.copyfileobj = boom
        try:
            stage_entry(d / "A_zc.vcf.gz", run)
            FAILS.append("a failing copy did not raise")
        except OSError:
            pass
        finally:
            inv.shutil.copyfileobj = real
        check(sorted(p.name for p in run.iterdir()), [],
              "no .vcf and no .part survives a failed copy")


def test_vsnp3_removal_mirror():
    """Mirrors Remove_From_Analysis: N, N.vcf, N_zc.vcf — and nothing gzipped."""
    check(vsnp3_would_remove("A_zc.vcf", {"A"}), True, "_zc.vcf matches the bare name")
    check(vsnp3_would_remove("A.vcf", {"A"}), True, ".vcf matches the bare name")
    check(vsnp3_would_remove("A_zc.vcf.gz", {"A"}), False,
          "a .gz can never be removed by name — which is why it must not be staged as .gz")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — step2 inventory + staging")
