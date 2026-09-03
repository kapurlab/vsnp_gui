"""Prove, at dispatch, that the run about to start is the run the user asked for.

Every defect this module exists to catch produced the same symptom: a run that
started cleanly, finished cleanly, and analysed the wrong set of samples. The
pane said 25, the folder held 26, vsnp3 reported 26, and nothing anywhere
compared those numbers to each other. A tree is not self-evidently wrong the
way a crash is, so a silent disagreement here can be published.

Three sets, computed independently at the last possible moment:

  R  requested  — the samples the user chose, as sent in the request.
  S  staged     — what is actually on disk in the run folder.
  V  vsnp3      — what vsnp3 will read: its own ``glob('*vcf')`` pattern
                  applied to the run folder, minus whatever its
                  ``-remove_by_name`` pass would then drop.

They must be equal. A mismatch is a bug in THIS code, not a condition a user
can reach by legitimate means, so it is a refusal: no job starts, and the run
folder is left in place as evidence. The alternative — starting anyway and
noting it — is what the software already did, implicitly, for years.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Dict, Iterable, List, Set

from app.step2_inventory import is_db_vcf, sample_of
from app.step2_staging import vsnp3_would_remove


def vsnp3_visible(run_dir: Path, removal_names: Iterable[str]) -> Set[str]:
    """The sample names vsnp3 will actually build dataframes for.

    The discovery pattern is copied verbatim from vsnp3_step2.py rather than
    re-expressed, because the whole point is to agree with vsnp3 and not with
    our idea of it: ``vcf_list = glob.glob(f'{wd}/*vcf')``. Then vsnp3 pops the
    ``-remove_by_name`` keys out of the parsed dataframes, which
    vsnp3_would_remove mirrors.
    """
    removal = set(removal_names)
    out: Set[str] = set()
    for path in glob.glob(os.path.join(str(run_dir), "*vcf")):
        name = os.path.basename(path)
        if name.startswith("."):
            continue
        if vsnp3_would_remove(name, removal):
            continue
        out.add(sample_of(name))
    return out


def staged_samples(run_dir: Path) -> Set[str]:
    """Every VCF-shaped file physically in the run folder, by sample name.

    Deliberately broader than `vsnp3_visible`: a ``.vcf.gz`` sitting here is
    exactly the failure worth catching — staged, counted as compared, and
    never opened.
    """
    try:
        return {sample_of(e.name) for e in os.scandir(run_dir) if is_db_vcf(e.name)}
    except OSError:
        return set()


def reconcile(run_dir: Path, requested: Iterable[str], removal_names: Iterable[str]) -> Dict:
    """Compare requested / staged / vsnp3-visible. Empty ``problems`` = agreement."""
    R = {s for s in requested if s}
    S = staged_samples(run_dir)
    V = vsnp3_visible(run_dir, removal_names)
    problems: List[str] = []

    def _say(label: str, missing: Set[str], where: str) -> None:
        if not missing:
            return
        shown = ", ".join(sorted(missing)[:10])
        more = f", +{len(missing) - 10} more" if len(missing) > 10 else ""
        problems.append(f"{label}: {shown}{more} ({where})")

    _say("requested but not staged", R - S, "the file was not copied into the run folder")
    _say("staged but not requested", S - R, "the file joined the run without being selected")
    _say(
        "staged but unreadable by vsnp3", S - V - (R - S),
        "vsnp3 discovers inputs with glob('*vcf'), so this file would be counted but never analysed",
    )
    _say("analysable but not requested", V - R, "vsnp3 would compare a sample the run did not ask for")
    return {
        "ok": not problems,
        "problems": problems,
        "requested": sorted(R),
        "staged": sorted(S),
        "analyzable": sorted(V),
        "counts": {"requested": len(R), "staged": len(S), "analyzable": len(V)},
    }
