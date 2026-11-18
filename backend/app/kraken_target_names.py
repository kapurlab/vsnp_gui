"""Target names — the "Target taxon" dropdown shared by Kraken ID Parse and vSNP.

A target name is a taxonomy name passed verbatim to the read parser (``-t``).
The parser matches it, case-insensitively, against the name column of the
Kraken report, so it has to be spelled exactly as Kraken spells it.

Two lists make up the dropdown:

* **Shipped** — ``config/taxa.yaml`` in the kraken_id_parse_gui checkout. It
  comes with the tool and changes only with a release.
* **Saved** — ``<config dir>/kraken_id_parse_gui/target_names.yaml``, the same
  per-user folder that holds this tool's ``config.json`` (``$XDG_CONFIG_HOME``,
  else ``~/.config``). Names added from either GUI go here.

Saved names used to be appended to the shipped file. That file is tracked in
git, so the first added name made the checkout "dirty": ``bdtools update`` and
``bdtools sync`` then refused to move the tool ("has local source changes:
config/taxa.yaml"), and on a site install, where the checkout belongs to the
admin, the add failed outright. Names already appended that way are carried
into the saved list once (``migrate_checkout_additions``).

This module is stdlib-only. vSNP GUI keeps a byte-identical copy as
``backend/app/kraken_target_names.py`` (its test compares the two), because the
two GUIs run in different environments but must agree on the file format and
the file locations.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

SHIPPED_REL = Path("config") / "taxa.yaml"
SAVED_NAME = "target_names.yaml"

SAVED_HEADER = (
    "# Kraken ID Parse — saved target names\n"
    "#\n"
    "# Names added from the Kraken ID Parse GUI or the vSNP GUI. Both GUIs show\n"
    "# these after the names that ship with the tool (config/taxa.yaml in its\n"
    "# checkout). Each entry is passed to the read parser (-t) and must match the\n"
    "# taxonomy name exactly as the Kraken report spells it.\n"
    "#\n"
    "# One entry per line as \"- Name\". The space after the dash is required.\n"
    "# Lines starting with # are notes; the GUIs keep them when they add or remove\n"
    "# a name.\n\n"
)

# A name the parser can actually use: one line, printable, not absurdly long.
MAX_NAME_LEN = 200


def config_dir() -> Path:
    """kraken_id_parse_gui's per-user config folder (mirrors its config.py)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "kraken_id_parse_gui"


def saved_path() -> Path:
    return config_dir() / SAVED_NAME


def shipped_path(kraken_gui_root: Path) -> Path:
    return Path(kraken_gui_root) / SHIPPED_REL


# ---------------------------------------------------------------------------
# Flat YAML sequence: one "- name" per line. Parsed without PyYAML so it works
# in either GUI's environment.
# ---------------------------------------------------------------------------
def _entry_name(raw: str) -> Optional[str]:
    """The name on one line of the file, or None when the line holds none."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("- "):
        line = line[2:].strip()
    elif line == "-":
        return None
    # A trailing note, only when the entry is not quoted.
    if line and line[0] not in "\"'" and " #" in line:
        line = line.split(" #", 1)[0].strip()
    if len(line) >= 2 and line[0] in "\"'" and line[-1] == line[0]:
        inner = line[1:-1]
        if line[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        line = inner
    return line or None


def parse_names(text: str) -> List[str]:
    names: List[str] = []
    for raw in (text or "").splitlines():
        name = _entry_name(raw)
        if name:
            names.append(name)
    return names


def read_names(path: Path) -> List[str]:
    try:
        return parse_names(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def yaml_line(name: str) -> str:
    """One name as a YAML sequence entry, quoted when the name needs it."""
    name = name.strip()
    if not name:
        return ""
    if name[0] in "-?:[]{}#&*!|>'\"%@`" or ": " in name or name.endswith(":") or " #" in name:
        esc = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'- "{esc}"'
    return f"- {name}"


def clean_name(name: str) -> str:
    """Normalise a typed name, or raise ValueError saying what is wrong."""
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Type a target name first.")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(f"A target name can be at most {MAX_NAME_LEN} characters.")
    if any(ord(c) < 32 for c in name):
        raise ValueError("A target name cannot contain control characters.")
    return name


def _write_atomic(path: Path, text: str) -> None:
    """Replace path's contents in one step, keeping its mode when it exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o7777
    except OSError:
        mode = None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# The list both GUIs show
# ---------------------------------------------------------------------------
def entries(kraken_gui_root: Path) -> List[Dict[str, str]]:
    """Every target name, shipped first then saved, once each (case-insensitive).

    Each entry is {"name", "source"} with source "shipped" or "saved". A saved
    name that matches a shipped one is not repeated.
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for source, path in (("shipped", shipped_path(kraken_gui_root)), ("saved", saved_path())):
        for name in read_names(path):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "source": source})
    return out


def names(kraken_gui_root: Path) -> List[str]:
    return [e["name"] for e in entries(kraken_gui_root)]


def add(kraken_gui_root: Path, name: str) -> Dict[str, object]:
    """Save one name. Returns {"name", "added"}; added is False for a duplicate.

    An append: the saved file's existing lines, notes included, are kept.
    """
    name = clean_name(name)
    existing = {n.lower(): n for n in names(kraken_gui_root)}
    if name.lower() in existing:
        return {"name": existing[name.lower()], "added": False}
    _append_saved(name)
    return {"name": name, "added": True}


def _append_saved(name: str) -> None:
    path = saved_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = SAVED_HEADER
    if text and not text.endswith("\n"):
        text += "\n"
    _write_atomic(path, text + yaml_line(name) + "\n")


def remove(kraken_gui_root: Path, name: str) -> Dict[str, object]:
    """Remove one saved name. Shipped names cannot be removed (ValueError).

    Only the matching entry lines go; notes and every other line stay as written.
    """
    target = " ".join(str(name or "").split()).lower()
    if not target:
        raise ValueError("No target name given.")
    path = saved_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        lines = []
    kept = [ln for ln in lines if (_entry_name(ln) or "").lower() != target]
    if len(kept) == len(lines):
        if any(n.lower() == target for n in read_names(shipped_path(kraken_gui_root))):
            raise ValueError(
                "That name ships with Kraken ID Parse and cannot be removed here. "
                "Only names you saved can be removed."
            )
        return {"removed": False}
    _write_atomic(path, "".join(kept))
    return {"removed": True}


# ---------------------------------------------------------------------------
# One-time carry-over of names appended to the shipped file by older releases
# ---------------------------------------------------------------------------
def _git_head_text(repo: Path, rel: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:{rel.as_posix()}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def migrate_checkout_additions(kraken_gui_root: Path) -> Dict[str, object]:
    """Move names that older releases appended to config/taxa.yaml into the saved list.

    Compares the checkout's file with the committed version. Names only in the
    working file are added to the saved list. Then, when the working file is the
    committed one plus appended lines (what every older add produced) and it can
    be written, it is put back to the committed text, so the checkout is clean
    and updates can move it again. Any other edit is left alone.

    Safe to call at every start: it does nothing once the file matches git.
    """
    root = Path(kraken_gui_root)
    shipped = shipped_path(root)
    result: Dict[str, object] = {"carried": [], "restored": False}
    try:
        working = shipped.read_text(encoding="utf-8")
    except OSError:
        return result
    committed = _git_head_text(root, SHIPPED_REL)
    if committed is None or committed == working:
        return result
    committed_keys = {n.lower() for n in parse_names(committed)}
    extra = [n for n in parse_names(working) if n.lower() not in committed_keys]
    saved_keys = {n.lower() for n in read_names(saved_path())}
    carried = []
    for n in extra:
        if n.lower() in saved_keys:
            continue
        # Straight to the saved file: add() would dedupe against the working
        # shipped file, which still holds n, and skip it — and the restore
        # below would then lose it.
        try:
            _append_saved(clean_name(n))
        except (OSError, ValueError):
            return result  # leave the shipped file alone if the copy failed
        saved_keys.add(n.lower())
        carried.append(n)
    result["carried"] = carried
    if working.startswith(committed) and os.access(shipped, os.W_OK):
        try:
            _write_atomic(shipped, committed)
            result["restored"] = True
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Names Kraken actually reported — so a new target can be picked, not typed
# ---------------------------------------------------------------------------
def report_names(report_path: Path, min_percent: float = 0.0) -> List[Dict[str, object]]:
    """Taxon names in one Kraken report, with clade % and reads.

    Standard Kraken2 report columns: percent, clade reads, taxon reads, rank
    code, taxid, indented name. Unclassified and root are skipped.
    """
    rows: List[Dict[str, object]] = []
    try:
        fh = open(report_path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            try:
                pct = float(parts[0].strip())
                reads = int(parts[1].strip())
            except ValueError:
                continue
            name = parts[-1].strip()
            rank = parts[-3].strip() if len(parts) >= 6 else ""
            if not name or name.lower() in ("unclassified", "root") or pct < min_percent:
                continue
            rows.append({"name": name, "percent": pct, "reads": reads, "rank": rank})
    return rows
