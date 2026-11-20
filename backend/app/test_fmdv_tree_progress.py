"""A VP1 tree build reports where it is, from the files it writes.

The page used to say "Building…" and "A few minutes with bootstraps" for a
build that takes anything from minutes to hours, so a long one could not be
told from a hung one (Ames HPC, 2026-11-20). app.fmdv_tree_progress reads
RAxML's own log. Each stage of a real `raxmlHPC -f a -# 100` log is replayed
here by cutting the log off where the build would have been.

Run directly:  python test_fmdv_tree_progress.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import fmdv_tree_progress as tp  # noqa: E402


def check(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: expected {want!r}, got {got!r}")
    print(f"  OK  {what}")


HEAD = (
    "This is RAxML version 8.2.13 released by Alexandros Stamatakis on October 2022.\n\n"
    "RAxML was called as follows:\n\n"
    "raxmlHPC-PTHREADS-AVX2 -s VP1_aligned.fasta -n VP1 -m GTRGAMMA -p 12345 -w /x/VP1 -T 8 -f a -x 12345 -# 100 \n\n\n"
    "Time for BS model parameter optimization 0.151234\n"
)


def boots(n, secs=30.0):
    return "".join(f"Bootstrap[{i}]: Time {secs:.6f} seconds, bootstrap likelihood -14411.70, "
                   f"best rearrangement setting 13\n" for i in range(n))


AFTER_BOOTS = "\n\nOverall Time for 100 Rapid Bootstraps 3000.000000 seconds\n" \
              "Average Time per Rapid Bootstrap 30.000000 seconds\n\nStarting ML Search ...\n\n"
FAST_DONE = "Fast ML optimization finished\n\nFast ML search Time: 12.5 seconds\n\n"


def slow(n):
    return "".join(f"Slow ML Search {i} Likelihood: -14521.49\n" for i in range(n))


THOROUGH = "Slow ML optimization finished\n\nSlow ML search Time: 400.1 seconds\nThorough ML search Time: 3.45 seconds\n\n" \
           "Final ML Optimization Likelihood: -14521.469765\n"


def run_with(tmp: Path, name: str, info=None, aligned=True, leaves=208, tre=False) -> Path:
    run = tmp / name
    vp1 = run / "VP1"
    vp1.mkdir(parents=True)
    fasta = "".join(f">s{i}\nACGT\n" for i in range(leaves))
    (vp1 / "VP1_input.fasta").write_text(fasta)
    if aligned:
        (vp1 / "VP1_aligned.fasta").write_text(fasta)
    if info is not None:
        (vp1 / "RAxML_info.VP1").write_text(info)
    if tre:
        (vp1 / "VP1_2026-11-20_10-43-11.tre").write_text("(a,b);\n")
    return run


def main() -> int:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        rid = "2026-11-20_10-43-11_VP1-tree-O"

        print("mafft is still aligning")
        p = tp.progress(run_with(tmp / "a", rid, aligned=False))
        check(p["stage"], "aligning", "stage")
        check(p["leaves"], 208, "the sequences are counted from the input")
        check("Aligning 208 sequences" in p["text"], True, "and the text says so")
        check(p["started_at"][:19], "2026-11-20T10:43:11", "the start is the run folder's own stamp")

        print("aligned, RAxML not writing yet")
        p = tp.progress(run_with(tmp / "b", rid))
        check(p["stage"], "bootstraps", "stage")
        check(p["bootstraps_done"], 0, "no replicate yet")

        print("37 of 100 bootstraps")
        p = tp.progress(run_with(tmp / "c", rid, info=HEAD + boots(37)))
        check((p["stage"], p["bootstraps_done"], p["bootstraps_total"]), ("bootstraps", 37, 100), "37 of 100, total read from the command")
        check(p["seconds_per_bootstrap"], 30.0, "seconds each, from RAxML's own figures")
        check(p["bootstrap_seconds_left"], 63 * 30, "what is left of the bootstraps")
        check("Bootstrap 37 of 100" in p["text"], True, "the text carries the count")

        print("bootstraps done, search starting")
        p = tp.progress(run_with(tmp / "d", rid, info=HEAD + boots(100) + AFTER_BOOTS))
        check(p["stage"], "search", "stage")
        check("first, fast pass" in p["text"], True, "the fast pass is named")
        check("no clock" in p["text"], True, "and no time is promised for the search")

        print("slow searches")
        p = tp.progress(run_with(tmp / "e", rid, info=HEAD + boots(100) + AFTER_BOOTS + FAST_DONE))
        check("slow search 1 of 10" in p["text"], True, "the first slow search")
        p = tp.progress(run_with(tmp / "f", rid, info=HEAD + boots(100) + AFTER_BOOTS + FAST_DONE + slow(3)))
        check((p["slow_searches_done"], "slow search 4 of 10" in p["text"]), (3, True), "three done, the fourth running")
        p = tp.progress(run_with(tmp / "g", rid, info=HEAD + boots(100) + AFTER_BOOTS + FAST_DONE + slow(10)))
        check("thorough search" in p["text"], True, "after ten, the thorough one")

        print("tree found, tables and reports being written")
        p = tp.progress(run_with(tmp / "h", rid, info=HEAD + boots(100) + AFTER_BOOTS + FAST_DONE + slow(10) + THOROUGH))
        check(p["stage"], "finishing", "stage")

        print("done")
        p = tp.progress(run_with(tmp / "i", rid, info=HEAD + boots(100) + AFTER_BOOTS + FAST_DONE + slow(10) + THOROUGH, tre=True))
        check(p["stage"], "done", "stage")

        print("a folder that is not there, or holds nothing, does not raise")
        check(tp.progress(tmp / "nowhere" / rid)["stage"], "aligning", "a missing folder reads as not started")
        check(tp.started_at("not-a-run"), "", "and a name that is no stamp gives no start time")
    print("test_fmdv_tree_progress: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
