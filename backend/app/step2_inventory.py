"""One answer to "what is in the Step 2 comparison set".

Before this module there were three, over the same directory:

  * the browse/selection list globbed ``*_zc.vcf`` and ``*_zc.vcf.gz``,
  * the card's count accepted any name ending ``.vcf`` / ``.vcf.gz``,
  * staging copied any name ending ``.vcf`` / ``.vcf.gz``.

A VCF whose name lacks the ``_zc`` marker was therefore counted in the total,
invisible to every control the user could reach, and copied into every run —
which is how a comparison of 25 named samples staged and analysed 26. The
extra file could not be unticked, could not be named in a list, and did not
appear in any table; it simply joined the tree. The import path MANUFACTURES
such names (see ``import_tail``), so this was never limited to hand-copied
files.

The rule here is deliberately about SUFFIXES ONLY. Membership of the database
cannot depend on a naming convention the software itself does not enforce:
anything a run would read is in the set, therefore it is listed, therefore it
can be excluded.

Two populations, two rules, kept apart on purpose:

  ``is_db_vcf``      what may sit in step2/vcf_database — ``.vcf`` or ``.vcf.gz``.
  ``is_analyzable``  what vsnp3 will actually READ out of a run folder. vsnp3
                     discovers its inputs with ``glob.glob(f'{wd}/*vcf')``
                     (vsnp3_step2.py), so a ``.vcf.gz`` is never opened — not
                     unsupported-but-tried, simply never matched. Staging is
                     responsible for making every staged file satisfy this.
"""

from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Longest first: "_zc.vcf.gz" must be tried before ".vcf.gz", or a sample would
# keep a "_zc" tail. Every name-derivation in the app funnels through this
# table so a new shape is added in exactly one place.
_DB_SUFFIXES = ("_zc.vcf.gz", "_zc.vcf", ".vcf.gz", ".vcf")


def is_db_vcf(name: str) -> bool:
    """True if this basename counts as an entry of the VCF database."""
    return not name.startswith(".") and name.endswith((".vcf", ".vcf.gz"))


def is_analyzable(name: str) -> bool:
    """True if vsnp3's ``glob('*vcf')`` would pick this basename up.

    Mirrors the deployed vsnp3_step2.py verbatim. Note it is `*vcf`, not
    `*.vcf` — a file literally named ``notes_vcf`` would match too; that is
    vsnp3's behaviour and this predicate exists to agree with it, not to
    improve on it.
    """
    return not name.startswith(".") and name.endswith("vcf")


def sample_of(name: str) -> str:
    """The sample name a VCF basename stands for.

    Suffix-stripping, never ``str.replace`` — a sample legitimately called
    ``run.vcf.backup_zc.vcf`` must lose only its trailing marker. The prior
    chained-replace derivation would have gutted the middle of the name.
    """
    for suffix in _DB_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def import_tail(name: str) -> tuple[str, str]:
    """Split a VCF basename into (stem, recognised tail).

    ``Path.stem``/``Path.suffixes`` cannot do this: for ``X_zc.vcf.gz`` the
    stem is ``X_zc.vcf``, so inserting a disambiguator between them produced
    ``X_zc.vcf_import1.vcf.gz`` — a name that is a database entry, is staged,
    is read by vsnp3, and matches no ``_zc`` reader anywhere. Splitting on the
    suffix table instead yields ``("X", "_zc.vcf.gz")`` so a caller can build
    ``X_import1_zc.vcf.gz``, whose sample name still derives correctly.
    """
    for suffix in _DB_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)], suffix
    return name, ""


@dataclass(frozen=True)
class Entry:
    """One VCF in the database."""
    filename: str
    sample: str
    compressed: bool
    edited: bool

    @property
    def analyzable_name(self) -> str:
        """The basename this entry gets once staged into a run folder.

        A compressed entry is decompressed on the way in (staging), because
        vsnp3 would otherwise never read it while every count claimed it had.
        """
        if not self.compressed:
            return self.filename
        return self.filename[: -len(".gz")]


def db_entries(vcf_source_dir: Path) -> List[Entry]:
    """Every VCF in the database, sorted by filename.

    One scandir. Only SYMLINKED entries pay a ``resolve()`` — a regular file
    sitting in this directory cannot resolve anywhere else, and at the Ames
    project's 8,000+ VCFs a resolve-per-file cost ~50k syscalls inside the
    hottest endpoint in the pane. The directory itself is resolved once, for
    the case where the database dir IS a link into vcf_edits/.
    """
    try:
        dir_is_edited = "vcf_edits" in vcf_source_dir.resolve().parts
    except OSError:
        dir_is_edited = False
    entries: List[Entry] = []
    try:
        with os.scandir(vcf_source_dir) as it:
            for e in it:
                if not is_db_vcf(e.name):
                    continue
                edited = dir_is_edited
                if not edited:
                    try:
                        if e.is_symlink():
                            edited = "vcf_edits" in Path(e.path).resolve().parts
                    except OSError:
                        edited = False
                entries.append(Entry(
                    filename=e.name,
                    sample=sample_of(e.name),
                    compressed=e.name.endswith(".gz"),
                    edited=edited,
                ))
    except OSError:
        return []
    entries.sort(key=lambda x: x.filename)
    return entries


def by_sample(entries: List[Entry]) -> Dict[str, List[Entry]]:
    """Group entries by the sample they claim to be.

    A sample with more than one entry (typically ``A_zc.vcf`` alongside an
    edited ``A_zc.vcf.gz``) is a data defect, not a choice to be made silently:
    the two files hold different calls and picking one at random decides the
    science. Callers surface it; dispatch refuses.
    """
    out: Dict[str, List[Entry]] = {}
    for e in entries:
        out.setdefault(e.sample, []).append(e)
    return out


def duplicate_samples(entries: List[Entry]) -> Dict[str, List[str]]:
    """{sample: [filename, ...]} for every sample carrying more than one file."""
    return {s: [e.filename for e in es] for s, es in by_sample(entries).items() if len(es) > 1}


def stage_entry(src: Path, dest_dir: Path, entry: Optional[Entry] = None) -> str:
    """Copy one database VCF into a run folder in a form vsnp3 will read.

    Returns the basename written. A ``.vcf.gz`` source is DECOMPRESSED rather
    than copied: vsnp3 globs ``*vcf`` and would never open the ``.gz``, so a
    verbatim copy left the file sitting in the run folder, counted by the GUI
    as compared and absent from the matrix.

    Written to ``<name>.part`` and renamed into place, so a concurrent reader
    (the staging-progress poll, a resumed run) can never observe a half-written
    VCF and treat it as staged.
    """
    entry = entry or Entry(src.name, sample_of(src.name), src.name.endswith(".gz"), False)
    final = dest_dir / entry.analyzable_name
    part = dest_dir / f"{entry.analyzable_name}.part"
    # Refuse to write THROUGH a symlink. The run folder is created with
    # exist_ok=True inside a group-writable project, so another member could
    # pre-plant the target as a link elsewhere and the copy would follow it and
    # overwrite that file as this user. Removing the link (never its target)
    # keeps staging correct.
    for p in (final, part):
        if p.is_symlink():
            p.unlink()
    try:
        if entry.compressed:
            with gzip.open(src, "rb") as fh_in, open(part, "wb") as fh_out:
                shutil.copyfileobj(fh_in, fh_out)
        else:
            # copy2 follows symlinks, so the real content (database entries are
            # links into step1/) lands as a regular file.
            shutil.copy2(src, part)
        os.replace(part, final)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return final.name
