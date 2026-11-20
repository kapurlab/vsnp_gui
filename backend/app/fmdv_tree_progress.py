"""Where a VP1 tree build has got to, read from the files it writes.

A VP1 tree takes minutes for an outbreak in one lineage and hours for a
serotype spread over many (the test set's A tree took 13 hours on a laptop),
and the page said the same thing for all of it: "Building…" and "A few
minutes with bootstraps". On the Ames HPC (2026-11-20) the O tree of the full
test set sat like that for ten minutes with nothing moving, and the only
question a person could ask was whether it had hung.

It had not, and the build says so itself, in RAxML_info.<name>: one line per
bootstrap replicate with the time it took, then the stages of the search.
This reads that file and says, in order:

    aligning      mafft is still aligning the sequences
    bootstraps    k of n replicates done, with the seconds each has taken
    search        the maximum-likelihood search after the bootstraps, which
                  RAxML does in ten "slow" searches and one thorough one
    finishing     the tree is found; tables and reports are being written
    done          the tree file is there

The search has no clock: its length does not follow from the size of the
tree (docs: SAT1 and SAT2, 188 and 208 sequences, two hours and ten
minutes). So no time is promised for it; the count of slow searches is the
progress it has.

Stdlib only, and no process is asked anything: the backend that started the
build may have been restarted since, and the build carries on without it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

_BOOT = re.compile(r"^Bootstrap\[(\d+)\]: Time ([0-9.]+) seconds", re.M)
_SLOW = re.compile(r"^Slow ML Search (\d+) Likelihood", re.M)
_COUNT = re.compile(r"(?:-#|-N)\s+(\d+)")
_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})_VP1-tree")
SLOW_SEARCHES = 10      # RAxML -f a: ten slow searches, then the thorough one


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _leaves(vp1: Path) -> int:
    for name in ("VP1_aligned.fasta", "VP1_input.fasta"):
        text = _read(vp1 / name)
        if text:
            return sum(1 for line in text.splitlines() if line.startswith(">"))
    return 0


def started_at(run_id: str) -> str:
    """The run folder's own stamp as an ISO local time, or ""."""
    m = _STAMP.match(run_id or "")
    if not m:
        return ""
    try:
        t = time.mktime(tuple(int(x) for x in m.groups()) + (0, 0, -1))
    except (ValueError, OverflowError):
        return ""
    zone = time.strftime("%z", time.localtime(t))        # -0700
    if len(zone) == 5:
        zone = zone[:3] + ":" + zone[3:]                  # -07:00, which every browser parses
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + zone


def progress(run_dir: Path, bootstraps_asked: Optional[int] = None) -> Dict[str, Any]:
    """The stage of the VP1 tree build under run_dir, and its numbers."""
    vp1 = run_dir / "VP1"
    out: Dict[str, Any] = {
        "run_id": run_dir.name, "started_at": started_at(run_dir.name), "stage": "aligning",
        "leaves": 0, "bootstraps_done": 0, "bootstraps_total": 0, "seconds_per_bootstrap": None,
        "bootstrap_seconds_left": None, "slow_searches_done": 0, "slow_searches_total": SLOW_SEARCHES,
        "text": "",
    }
    try:
        done = vp1.is_dir() and any(vp1.glob("*.tre"))
    except OSError:
        done = False
    out["leaves"] = n = _leaves(vp1)
    seqs = f"{n:,} sequences" if n else "the sequences"
    info = ""
    try:
        info_files = sorted(vp1.glob("RAxML_info.*")) if vp1.is_dir() else []
    except OSError:
        info_files = []
    if info_files:
        info = _read(info_files[0])
    boots = _BOOT.findall(info)
    total = bootstraps_asked or 0
    if not total:
        m = _COUNT.search(info)
        total = int(m.group(1)) if m else 0
    out["bootstraps_done"], out["bootstraps_total"] = len(boots), total
    if boots:
        times = [float(t) for _i, t in boots]
        out["seconds_per_bootstrap"] = round(sum(times) / len(times), 2)
    slow = len(_SLOW.findall(info))
    out["slow_searches_done"] = slow

    if done:
        out["stage"], out["text"] = "done", f"Finished: {seqs} in the tree."
        return out
    if not info:
        aligned = (vp1 / "VP1_aligned.fasta")
        try:
            has_alignment = aligned.is_file() and aligned.stat().st_size > 0
        except OSError:
            has_alignment = False
        if has_alignment:
            out["stage"] = "bootstraps"
            out["text"] = f"Aligned {seqs}; the tree builder is starting."
        else:
            out["text"] = f"Aligning {seqs} with mafft."
        return out
    if "Final ML Optimization" in info or "Thorough ML search" in info:
        out["stage"] = "finishing"
        out["text"] = (f"The tree of {seqs} is found. Writing it, then the neighbour tables, the site "
                       "tables and the reports.")
        return out
    in_search = ("Starting ML Search" in info or "Overall Time for" in info
                 or (total and len(boots) >= total))
    if in_search:
        out["stage"] = "search"
        if slow:
            where = f"slow search {min(slow + 1, SLOW_SEARCHES)} of {SLOW_SEARCHES}" if slow < SLOW_SEARCHES \
                else "the last, thorough search"
        elif "Fast ML optimization finished" in info:
            where = f"slow search 1 of {SLOW_SEARCHES}"
        else:
            where = "the first, fast pass"
        out["text"] = (f"All {len(boots) or total} bootstraps are done. Searching for the best tree of {seqs}: "
                       f"{where}. This stage has no clock: it has taken minutes for some serotypes and "
                       "hours for others, and a long one is not a sign that anything is wrong.")
        return out
    out["stage"] = "bootstraps"
    if boots and total:
        left = max(0, total - len(boots)) * out["seconds_per_bootstrap"]
        out["bootstrap_seconds_left"] = int(round(left))
        out["text"] = (f"{seqs.capitalize()} in the tree. Bootstrap {len(boots)} of {total} done, about "
                       f"{out['seconds_per_bootstrap']:g} s each. The tree search follows the bootstraps.")
    else:
        out["text"] = f"{seqs.capitalize()} in the tree. The first bootstrap is running."
    return out
