from pathlib import Path
from typing import Dict, List, Optional


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
    deps_dir = vsnp3_path / "dependencies"
    deps_dir.mkdir(parents=True, exist_ok=True)
    deps_file = deps_dir / "reference_options_paths.txt"
    existing = get_reference_paths(vsnp3_path)
    resolved = str(Path(new_path).resolve())
    if resolved not in [str(Path(p).resolve()) for p in existing]:
        existing.append(resolved)
        deps_file.write_text("\n".join(existing) + "\n", encoding="utf-8")
    return existing


def reference_roots(vsnp3_path: Path) -> List[Path]:
    """Public accessor for the configured reference option roots.

    Used by serve_project_file to widen the path-allow check so igv.js
    can fetch reference-resident files (e.g. GFF annotations) that live
    outside the project directory.
    """
    return _load_reference_paths(vsnp3_path)


def find_gff_for_fasta(fasta_path: Path, vsnp3_path: Path) -> Optional[Path]:
    """Locate a GFF/GFF3 that pairs with a step1 alignment fasta.

    Step 1 copies the reference fasta into the per-sample alignment
    directory but not the GFF, so the GFF must be looked up in the
    original reference options dirs by matching the fasta stem
    (e.g. ``NC_045512.fasta`` → ``NC_045512.gff``).
    """
    if not fasta_path.exists():
        return None
    stem = fasta_path.stem
    for root in _load_reference_paths(vsnp3_path):
        if not root.exists():
            continue
        for ref_dir in root.iterdir():
            if not ref_dir.is_dir():
                continue
            for ext in (".gff", ".gff3"):
                candidate = ref_dir / f"{stem}{ext}"
                if candidate.exists():
                    return candidate
    return None


def remove_reference_path(vsnp3_path: Path, remove_path: str) -> List[str]:
    deps_dir = vsnp3_path / "dependencies"
    deps_file = deps_dir / "reference_options_paths.txt"
    if not deps_file.exists():
        return []
    existing = get_reference_paths(vsnp3_path)
    resolved_remove = str(Path(remove_path).resolve())
    updated = [p for p in existing if str(Path(p).resolve()) != resolved_remove]
    deps_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return updated
