"""Which files are Step 1 stats workbooks: one rule for the Step 1 index that
lists them (step1_index), the Results scan that reads them (qc_scan) and the
Stats button (main._latest_step1_stats).

vsnp3 names each run's workbook <sample>_<date>_stats.xlsx. vSNP v1, the
pipeline before it (2017-2019), wrote <sample>_<YYYY-MM-DD_HH-MM-SS>.xlsx, and
a project aligned back then and added to the GUI has only those. Kept apart
from qc_scan because importing that module silences every warning in the
importing process, which is right for the scan's subprocess and wrong for the
backend.
"""

from fnmatch import fnmatchcase

V1_STATS_GLOB = "*_[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_[0-9][0-9]-[0-9][0-9]-[0-9][0-9].xlsx"


def is_stats_workbook(name: str) -> bool:
    """Whether the Results scan reads a file of this name: vsnp3's workbook or
    vSNP v1's. Case-sensitive, as glob is. Skipping dotfiles is the caller's job.
    Asked of every entry of every sample folder listed, so names that are not
    .xlsx at all are turned away by the cheap test before any pattern runs."""
    if not name.endswith(".xlsx"):
        return False
    return name.endswith("_stats.xlsx") or fnmatchcase(name, V1_STATS_GLOB)
