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
