import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

# Paths the upstream vsnp3 author ships inside the package/source tree
# (dependencies/reference_options_paths.txt). They are his own machines' paths
# — never a choice made at THIS site — yet on a fresh deployment they show up
# in the Reference Editor as if someone here had added them. Recognized
# verbatim and removed once per install by sanitize_upstream_paths().
UPSTREAM_SHIPPED_PATHS = frozenset({
    "/Users/todstuber/vsnp3_test_dataset/vsnp_dependencies",
    "/home/tstuber/vSNP_reference_options",
    "/project/mycobacteria_brucella/mycobacterium/vsnp_dependencies",
})

_SANITIZED_MARKER = ".upstream_paths_removed"


def _write_paths_file(deps_file: Path, paths: List[str]) -> None:
    """Rewrite the registry atomically via a temp file + rename.

    The rename matters beyond atomicity: in a conda env this file is a
    HARDLINK into the package cache (pkgs/vsnp3-*/dependencies/...), so an
    in-place write silently edits the cache too and every future
    `conda create ... vsnp3` on the machine is born pre-seeded with this
    install's paths. A rename breaks the link and edits only this install.
    """
    deps_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(deps_file.parent), prefix=".rop-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(paths) + "\n" if paths else "\n")
        os.replace(tmp, deps_file)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sanitize_upstream_paths(vsnp3_path: Path) -> List[str]:
    """One-time removal of the upstream author's shipped paths (see
    UPSTREAM_SHIPPED_PATHS) from this install's registry. Runs at backend
    startup; a marker file next to the registry makes it once-per-install, so
    a user who deliberately re-adds one of those paths afterwards keeps it.
    Returns the list of removed paths (empty when nothing changed)."""
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    marker = deps_file.with_name(_SANITIZED_MARKER)
    if marker.exists() or not deps_file.is_file():
        return []
    existing = get_reference_paths(vsnp3_path)
    kept = [p for p in existing if p not in UPSTREAM_SHIPPED_PATHS]
    removed = [p for p in existing if p in UPSTREAM_SHIPPED_PATHS]
    try:
        if removed:
            _write_paths_file(deps_file, kept)
        marker.write_text(
            "reference_options_paths.txt was checked for upstream-shipped "
            "author paths at first backend start.\nremoved: "
            + (", ".join(removed) if removed else "(none)") + "\n",
            encoding="utf-8",
        )
    except OSError:
        # Read-only install (e.g. a shared env this user can't write): leave
        # it; the next start by someone with write access will do it.
        return []
    return removed


def list_references(vsnp3_path: Path) -> List[Dict]:
    refs: List[Dict] = []
    ref_paths = _load_reference_paths(vsnp3_path)
    for parent in ref_paths:
        if not parent.exists():
            continue
        for child in sorted(parent.iterdir()):
            if child.is_dir():
                refs.append({"name": child.name, "path": str(child)})
    return refs


def _load_reference_paths(vsnp3_path: Path) -> List[Path]:
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    if deps_file.exists():
        paths = []
        for line in deps_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                paths.append(Path(line))
        return paths
    fallback = vsnp3_path / "dependencies"
    return [fallback] if fallback.exists() else []


def get_reference_paths(vsnp3_path: Path) -> List[str]:
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    if not deps_file.exists():
        return []
    paths = []
    for line in deps_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            paths.append(line)
    return paths


def add_reference_path(vsnp3_path: Path, new_path: str) -> List[str]:
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    existing = get_reference_paths(vsnp3_path)
    resolved = str(Path(new_path).resolve())
    if resolved not in [str(Path(p).resolve()) for p in existing]:
        existing.append(resolved)
        _write_paths_file(deps_file, existing)
    return existing


def reference_roots(vsnp3_path: Path) -> List[Path]:
    """Public accessor for the configured reference option roots.

    Used by serve_project_file to widen the path-allow check so igv.js
    can fetch reference-resident files (e.g. GFF annotations) that live
    outside the project directory.
    """
    return _load_reference_paths(vsnp3_path)


def _alnum_lower(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def find_gff_for_fasta(fasta_path: Path, vsnp3_path: Path) -> Optional[Path]:
    """Locate a GFF/GFF3 that pairs with a step1 alignment fasta.

    Step 1 copies the reference fasta into the per-sample alignment
    directory but NOT the GFF, so the GFF must be looked up in the
    reference options dirs. vSNP3's reference dirs use inconsistent
    naming (e.g. dir ``mtbc0_v1.1`` contains ``MTBC0v1.1_PGAP_annot.gff``
    paired with alignment dir ``alignment_MTBC0_v1``; ``Brucella_abortus1``
    contains ``NC_006932-NC_006933.gff``; ``Mycobacterium_orygis``
    contains just ``orygis.gff``), so a strict stem match doesn't work.

    Scoring algorithm:
      - alignment dir name → ref dir name fuzzy match (lowercase, alnum-only):
          exact         = +100
          prefix match  =  +50
          substring     =  +25
          no match      =    0
      - fasta stem matches the gff stem: +20 bonus
      Highest combined score wins. Anything with score > 0 is preferred
      over no match.
    """
    if not fasta_path.exists():
        return None
    stem = fasta_path.stem
    align_dir_name = fasta_path.parent.name
    align_key = (
        align_dir_name[len("alignment_"):]
        if align_dir_name.lower().startswith("alignment_")
        else align_dir_name
    )
    align_norm = _alnum_lower(align_key)

    best_score = 0
    best_gff: Optional[Path] = None
    for root in _load_reference_paths(vsnp3_path):
        if not root.exists():
            continue
        for ref_dir in root.iterdir():
            if not ref_dir.is_dir():
                continue
            ref_norm = _alnum_lower(ref_dir.name)
            name_score = 0
            if ref_norm and align_norm:
                if ref_norm == align_norm:
                    name_score = 100
                elif ref_norm.startswith(align_norm) or align_norm.startswith(ref_norm):
                    name_score = 50
                elif ref_norm in align_norm or align_norm in ref_norm:
                    name_score = 25
            for pattern in ("*.gff3", "*.gff"):
                for gff in sorted(ref_dir.glob(pattern)):
                    score = name_score + (20 if gff.stem == stem else 0)
                    if score > best_score:
                        best_score = score
                        best_gff = gff
    return best_gff


def remove_reference_path(vsnp3_path: Path, remove_path: str) -> List[str]:
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    if not deps_file.exists():
        return []
    existing = get_reference_paths(vsnp3_path)
    resolved_remove = str(Path(remove_path).resolve())
    updated = [p for p in existing if str(Path(p).resolve()) != resolved_remove]
    _write_paths_file(deps_file, updated)
    return updated
