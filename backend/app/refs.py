from pathlib import Path
from typing import Dict, List


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
