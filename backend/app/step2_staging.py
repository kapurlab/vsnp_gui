"""Step 2 run-folder staging.

Each Step 2 run works on COPIES of the cumulative step2/vcf_database in a dated
run folder (vsnp3 deletes every VCF out of its -wd, so the database itself must
never be handed to it). Staging copies only what the run will analyse: vsnp3
parses every VCF in its -wd into a dataframe BEFORE applying -remove_by_name,
so copying the whole database made a 25-sample comparison parse 8,600 files.

Staging is an ALLOW-LIST. It used to be a denylist — copy every ``*.vcf`` /
``*.vcf.gz`` in the folder, minus the names the removal list catches — and that
is the shape of the bug it caused: the removal list can only ever contain names
the SELECTION UI knew about, and the selection UI listed only ``*_zc.vcf``. A
database entry with any other name was therefore unnameable, unexcludable, and
copied into every single run. Naming what to include cannot fail that way: a
file nobody asked for is simply not in the list.

Compressed entries are DECOMPRESSED on the way in. vsnp3 discovers its inputs
with ``glob.glob(f'{wd}/*vcf')``, which cannot match a name ending ``.gz``, and
opens them with a plain ``open()`` — so a ``.vcf.gz`` copied verbatim sat in the
run folder, was counted by the GUI as part of the comparison, and never reached
the matrix. That hit edited VCFs hardest: the editor writes its patch bgzipped,
so the correction a user made was the one file guaranteed to be ignored.

``vsnp3_would_remove`` remains because ``-remove_by_name`` is still passed to
vsnp3 (belt and braces on the legacy path, and the reconciler checks against
it). Its docstring used to claim a mirror mismatch "can only cost time, never
correctness" — untrue for ``.vcf.gz``, which neither the mirror nor vsnp3 can
match. With an allow-list and decompression, no staged file is compressed and
the claim holds again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.step2_inventory import Entry, db_entries, is_db_vcf, sample_of, stage_entry


def removal_keys(vcf_basename: str) -> Set[str]:
    """Every -remove_by_name spelling that would drop this staged file.

    vsnp3's Remove_From_Analysis builds, for each listed name N and extension
    "vcf", the keys N, N.vcf and N_zc.vcf, and pops those basenames out of the
    parsed dataframes. Nothing else matches — a ``.vcf.gz`` key never can. Read
    backwards, that makes this the set of removal names a given staged file can
    answer to, which is what lets a caller narrow a database-wide removal list
    to the handful of names that can actually do anything in one run folder.
    """
    keys = {vcf_basename}
    if vcf_basename.endswith(".vcf"):
        stem = vcf_basename[: -len(".vcf")]
        keys.add(stem)
        if stem.endswith("_zc"):
            keys.add(stem[: -len("_zc")])
    return keys


def removals_that_bite(staged_basenames: Iterable[str], removal_names: Iterable[str]) -> List[str]:
    """The removal names that can drop one of these staged files, sorted.

    A removal name matching nothing in the run folder is a no-op: vsnp3 pops
    keys out of dataframes it built from its own ``-wd`` glob, so a name for a
    file that was never staged changes neither what it reads nor what it
    writes. Dropping those names is therefore behaviour-preserving — and it is
    the difference between a comparison folder that names its own samples and
    one that names every isolate in the database.
    """
    keys: Set[str] = set()
    for name in staged_basenames:
        keys |= removal_keys(name)
    return sorted(keys & {str(n).strip() for n in removal_names if str(n).strip()})


def vsnp3_would_remove(vcf_basename: str, removal_names: Set[str]) -> bool:
    """True when vsnp3's -remove_by_name would drop this staged file."""
    return any(key in removal_names for key in removal_keys(vcf_basename))


def stage_step2_vcfs(
    vcf_source_dir: Path,
    run_dir: Path,
    removal_names: Optional[Iterable[str]] = None,
    include_samples: Optional[Iterable[str]] = None,
) -> Tuple[int, int, Set[str]]:
    """Copy the VCFs this run will analyse from the database into run_dir.

    ``include_samples`` is the allow-list, by SAMPLE name — exactly those are
    staged and nothing else. ``removal_names`` is the legacy denylist, used
    only when no allow-list is given (an older frontend, or a caller that has
    not been migrated); it reproduces the previous behaviour so the migration
    is not a flag day.

    Returns (copied, skipped, staged_basenames) where staged_basenames are the
    names as WRITTEN — a decompressed ``.gz`` is reported under its ``.vcf``
    name, because that is what vsnp3 will see.

    Raises OSError with the failing filename attached, and ValueError when a
    sample carries more than one database file: the two hold different calls,
    and choosing between them silently would decide the science by filesystem
    order.
    """
    entries = db_entries(vcf_source_dir)
    copied = 0
    skipped = 0
    staged: Set[str] = set()

    if include_samples is not None:
        wanted = {s for s in include_samples if s}
        chosen: List[Entry] = [e for e in entries if e.sample in wanted]
        clashes: Dict[str, List[str]] = {}
        for e in chosen:
            clashes.setdefault(e.sample, []).append(e.filename)
        ambiguous = {s: fns for s, fns in clashes.items() if len(fns) > 1}
        if ambiguous:
            detail = "; ".join(f"{s}: {', '.join(sorted(fns))}" for s, fns in sorted(ambiguous.items())[:5])
            raise ValueError(
                f"{len(ambiguous)} sample(s) have more than one VCF in the database, so this run "
                f"cannot say which calls to compare — {detail}"
            )
        skipped = len(entries) - len(chosen)
        for e in chosen:
            staged.add(stage_entry(vcf_source_dir / e.filename, run_dir, e))
            copied += 1
        return copied, skipped, staged

    removal_set = set(removal_names or ())
    for e in entries:
        if vsnp3_would_remove(e.filename, removal_set):
            skipped += 1
            continue
        staged.add(stage_entry(vcf_source_dir / e.filename, run_dir, e))
        copied += 1
    return copied, skipped, staged
