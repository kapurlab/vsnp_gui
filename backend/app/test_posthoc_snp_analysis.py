"""SNP distances: where the results land, and why a run produced nothing.

Two defects, one report. A user ran SNP distances on every group of a
comparison, saw each one start and finish, and found the files under exactly
one group. Both halves of that are tested here against the real tool and a real
snp-dists:

  * "Include: only samples" could never work. The allow-list of Step 1 samples
    was looked up under the group's PARENT, which stopped being step2/ when
    comparisons moved into dated run folders — so the manifest was never found,
    the allow-list came back empty, and the run wrote an error and stopped.

  * The error was invisible. stats.json counted as one of the tool's outputs,
    so a run that produced nothing else still reported "has outputs" and the
    pane drew its ready chip; stats.json is hidden from the file list, so the
    group showed a ready badge above no files.

Also pinned: results belong in the group folder beside the alignment they were
computed from, working files do not survive the run, and comparisons made
before the move still report what they hold.

  cd backend && ../env/bin/python -m app.test_posthoc_snp_analysis
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.posthoc import output_path  # noqa: E402
from app.posthoc.registry import TOOLS  # noqa: E402
from app.posthoc.snp_analysis import (  # noqa: E402
    FILTERED_FASTA_NAME,
    find_group_fasta,
    find_vcf_manifest,
    load_step1_allowlist,
    run,
)

FAILS = []


def check(got, want, what):
    if got == want:
        print(f"  OK  {what}")
    else:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")
        print(f"  FAIL {what}\n    got:  {got!r}\n    want: {want!r}")


SEQS = {
    "TX-17-0001_zc.vcf": "ACGTACGTAC",
    "TX-17-0002_zc.vcf": "ACGTACGTAA",
    "TX-17-0003_zc.vcf": "ACGTACGTTT",
    "ERR2704709_zc.vcf": "TTTTACGTAC",  # a reference-panel genome, not Step 1
}


def build_project(tmp: Path, group_name="Mbovis-11B"):
    """A project shaped the way one on disk actually is: the VCF database at
    step2/, the comparison in a dated folder under it, groups under that."""
    step2 = tmp / "project" / "step2"
    db = step2 / "vcf_database"
    db.mkdir(parents=True)
    with (db / ".vcf_source_manifest.csv").open("w", encoding="utf-8") as fh:
        fh.write("filename,source_type\n")
        for name in SEQS:
            fh.write(f"{name},{'reference' if name.startswith('ERR') else 'step1'}\n")
    group = step2 / "2026-09-03_12-19-58" / group_name
    group.mkdir(parents=True)
    with (group / f"{group_name}-2026-09-03.fasta").open("w", encoding="utf-8") as fh:
        fh.write(">root\nACGTACGTAC\n")
        for name, seq in SEQS.items():
            fh.write(f">{name}\n{seq}\n")
    return step2, group


def snp_dists():
    exe = Path(__file__).resolve().parents[2] / "env" / "bin" / "snp-dists"
    return str(exe) if exe.exists() else "snp-dists"


def test_manifest_is_found_from_a_dated_run_folder():
    print("\n[the reported bug: 'Include: only samples' never found the manifest]")
    with tempfile.TemporaryDirectory() as t:
        step2, group = build_project(Path(t))
        # The old code took the group's parent to BE step2/ and looked for the
        # database one level down from it. Under a dated run folder that path
        # does not exist and never has — the empty allow-list it returned is
        # the whole failure.
        run_dir = group.parent
        check((run_dir / "vcf_database").exists(), False,
              "step2/<run_id>/vcf_database — where the old lookup went — is not a place")
        check(find_vcf_manifest(group), step2 / "vcf_database" / ".vcf_source_manifest.csv",
              "walking up from the group reaches the database beside the runs")
        allowed = load_step1_allowlist(group)
        check(sorted(allowed),
              ["TX-17-0001_zc.vcf", "TX-17-0002_zc.vcf", "TX-17-0003_zc.vcf"],
              "and only the Step 1 rows come back")
        check("ERR2704709_zc.vcf" in allowed, False,
              "a reference-panel genome is not a Step 1 sample")


def test_only_samples_run_produces_results_in_the_group_folder():
    print("\n['Include: only samples' runs, and lands beside the alignment]")
    with tempfile.TemporaryDirectory() as t:
        _step2, group = build_project(Path(t))
        rc = run(group, "Mbovis-11B", group, snp_dists(), "step1_only")
        check(rc, 0, "the run succeeds")
        stats = json.loads((group / "stats.json").read_text())
        check(stats["status"], "ok", f"stats say ok: {stats.get('message', '')}")
        check(stats["n_sequences"], 3, "the three Step 1 samples were compared")
        check((group / "snp_matrix.csv").exists(), True, "snp_matrix.csv is in the group folder")
        check((group / "kdp.png").exists(), True, "kdp.png is in the group folder")
        check((group / "posthoc").exists(), False, "and no posthoc/ subfolder was made")
        left = sorted(p.name for p in group.iterdir())
        for junk in (FILTERED_FASTA_NAME, "snp_matrix.tsv", "snp_distances.txt",
                     "filtered_step1.fasta"):
            check(junk in left, False, f"no working file left behind: {junk}")
        # The reference genome is excluded, so the matrix is 3x3 of Step 1 names.
        header = (group / "snp_matrix.csv").read_text().splitlines()[0]
        check("ERR2704709" in header, False, "the reference genome is not in the matrix")


def test_a_second_run_still_reads_the_groups_own_alignment():
    print("\n[the filtered alignment never becomes the input]")
    with tempfile.TemporaryDirectory() as t:
        _step2, group = build_project(Path(t))
        run(group, "Mbovis-11B", group, snp_dists(), "step1_only")
        # Leave a filtered alignment behind by hand: even then it is not the
        # newest *.fasta the next run picks up.
        (group / FILTERED_FASTA_NAME).write_text(">TX-17-0001_zc.vcf\nAAAA\n")
        check(find_group_fasta(group).name, "Mbovis-11B-2026-09-03.fasta",
              "the group's own alignment is chosen, not the working file")
        rc = run(group, "Mbovis-11B", group, snp_dists(), "all")
        check(rc, 0, "the second run succeeds")
        stats = json.loads((group / "stats.json").read_text())
        check(stats["n_sequences"], 4, "scope 'all' compares the reference genome too")


def test_a_missing_manifest_says_what_to_do():
    print("\n[a run that cannot work says why, and does not look ready]")
    with tempfile.TemporaryDirectory() as t:
        _step2, group = build_project(Path(t))
        shutil.rmtree(group.parents[1] / "vcf_database")
        rc = run(group, "Mbovis-11B", group, snp_dists(), "step1_only")
        check(rc, 1, "the run fails")
        stats = json.loads((group / "stats.json").read_text())
        check(stats["status"], "error", "and says so")
        check("samples + reference" in stats["message"], True,
              f"with a way forward: {stats['message']}")
        tool = TOOLS["snp_analysis"]
        check([o for o in tool.outputs if (group / o).exists()], [],
              "no output exists, so the pane cannot draw a ready chip")
        check(tool.stats_file in tool.outputs, False,
              "stats.json is not an output — writing it is not producing results")


def test_a_group_with_no_step1_samples_is_told_so():
    print("\n[a group of reference genomes has no pair to measure]")
    with tempfile.TemporaryDirectory() as t:
        _step2, group = build_project(Path(t))
        aln = next(group.glob("*.fasta"))
        aln.write_text(">root\nACGTACGTAC\n>ERR2704709_zc.vcf\nTTTTACGTAC\n")
        rc = run(group, "Mbovis-11B", group, snp_dists(), "step1_only")
        check(rc, 1, "the run fails")
        stats = json.loads((group / "stats.json").read_text())
        check("no pair to measure" in stats["message"], True,
              f"and names the reason: {stats['message']}")
        check((group / FILTERED_FASTA_NAME).exists(), False,
              "the working alignment is cleaned up on the failure path too")


def test_a_comparison_made_before_the_move_still_reports():
    print("\n[runs made under the old layout keep working]")
    with tempfile.TemporaryDirectory() as t:
        _step2, group = build_project(Path(t))
        legacy = group / "posthoc"
        legacy.mkdir()
        (legacy / "snp_matrix.csv").write_text("a,b\n")
        check(output_path(group, "snp_matrix.csv"), legacy / "snp_matrix.csv",
              "an old result is found under posthoc/")
        (group / "snp_matrix.csv").write_text("a,b\n")
        check(output_path(group, "snp_matrix.csv"), group / "snp_matrix.csv",
              "a re-run in the group folder wins over the old copy")
        check(output_path(group, "kdp.png"), group / "kdp.png",
              "and a result that exists nowhere is named where it will appear")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"\nFAIL — {len(FAILS)} assertion(s)")
    sys.exit(1)
print("\nAll post-hoc SNP-distance tests passed.")
