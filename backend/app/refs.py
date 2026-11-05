import getpass
import logging
import os
import shutil
import socket
import stat as _stat
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

try:
    import pwd
except ImportError:  # not POSIX
    pwd = None

# Paths the upstream vsnp3 author ships inside the package/source tree
# (dependencies/reference_options_paths.txt). They are his own machines' paths
# — on almost every deployment not a choice made at THIS site — yet on a fresh
# install they show up in the Reference Editor as if someone here had added
# them. Recognized verbatim and removed once per install by
# sanitize_upstream_paths(), BUT only when the path does not exist on this
# machine: on the author's own systems (the USDA Ames HPC, where
# /project/mycobacteria_brucella/... is real and holds the reference folders)
# these are genuine configuration, and removing them silently breaks vsnp3's
# -t resolution for every reference that lives there.
UPSTREAM_SHIPPED_PATHS = frozenset({
    "/Users/todstuber/vsnp3_test_dataset/vsnp_dependencies",
    "/home/tstuber/vSNP_reference_options",
    "/project/mycobacteria_brucella/mycobacterium/vsnp_dependencies",
})

_SANITIZED_MARKER = ".upstream_paths_removed"

logger = logging.getLogger("uvicorn.error")


def _write_paths_file(deps_file: Path, paths: List[str]) -> None:
    """Rewrite the registry atomically via a temp file + rename.

    The rename matters beyond atomicity: in a conda env this file is a
    HARDLINK into the package cache (pkgs/vsnp3-*/dependencies/...), so an
    in-place write silently edits the cache too and every future
    `conda create ... vsnp3` on the machine is born pre-seeded with this
    install's paths. A rename breaks the link and edits only this install.
    """
    # Create dependencies/ only INSIDE an install that exists. mkdir(parents=True)
    # on a vsnp3_path pointing nowhere fabricates the whole tree
    # (~/miniforge3/envs/vsnp3/dependencies and every parent), and that phantom
    # then satisfies every "is it configured?" check here while having no bin/ to
    # run — the Reference Editor lists references from it, the run executes some
    # other vsnp3 on PATH, and the two registries disagree silently.
    if not deps_file.parent.is_dir() and not deps_file.parent.parent.is_dir():
        raise FileNotFoundError(
            f"no vsnp3 install at {deps_file.parent.parent} — refusing to create "
            f"one by writing {deps_file.name}"
        )
    deps_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = deps_file.stat()
    except FileNotFoundError:
        old = None
    fd, tmp = tempfile.mkstemp(dir=str(deps_file.parent), prefix=".rop-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(paths) + "\n" if paths else "\n")
        _keep_permissions(deps_file, old, tmp)
        os.replace(tmp, deps_file)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _keep_permissions(deps_file: Path, old: Optional[os.stat_result], tmp: str) -> None:
    """Give the replacement list the permissions of the list it replaces.

    mkstemp makes the temp file 0600 and the rename puts it in place, so every
    save used to turn a list the site had shared (0664 in a setgid group folder,
    or an ACL) into one only the saver could read. On USDA SciComp, 2026-11-04,
    the list was -rw------- and every other account was refused it. Sites decide
    who may read and edit this list, so a save keeps exactly what was there and
    never widens it.

    copystat carries the mode and, on Linux, the extended attributes that hold
    POSIX and NFSv4 ACLs. On Ames (/project is NFSv4) the mode reads 0600+ even
    on files every user can read; the ACL is what grants that access, so copying
    the mode alone would drop it. The owner can only be kept by root. The group
    is kept when this account may set it, and a setgid folder usually sees to it
    anyway. A first list gets 0644 less the umask.

    Keeping permissions must never cost the save itself. A filesystem that
    refuses permission changes (some SMB/CIFS mounts fix every file's mode at
    mount time) falls back to the mode bits alone, and then to a plain save:
    there the mount, not this file, decides who can read it.
    """
    if old is None:
        try:
            os.chmod(tmp, 0o644 & ~_current_umask())
        except OSError as exc:
            logger.warning("reference locations: saved without setting permissions (%s)", exc)
        return
    try:
        shutil.copystat(str(deps_file), tmp)
    except OSError as exc:
        try:
            os.chmod(tmp, _stat.S_IMODE(old.st_mode))
            logger.warning("reference locations: kept the mode but not the ACL (%s)", exc)
        except OSError as exc2:
            logger.warning("reference locations: saved without keeping permissions (%s)", exc2)
    try:
        os.utime(tmp, None)  # copystat copied the old mtime; this is a new edit
    except OSError:
        pass
    for uid in (old.st_uid, -1):
        try:
            os.chown(tmp, uid, old.st_gid)
            break
        except OSError:
            continue


def _current_umask() -> int:
    """The process umask, read without changing it where Linux allows. Other
    systems can only set and restore it, and the mask is briefly made STRICTER,
    so a file another thread creates in that moment can only come out narrower."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError):
        pass
    mask = os.umask(0o077)
    os.umask(mask)
    return mask


# Said with every refusal. The registry lives inside the vsnp3 install, so on
# a shared deployment it is one list for everyone who runs vSNP there; which
# accounts may change it is the site's call, and the GUI neither widens access
# nor keeps a private per-user list to route around it.
SHARED_LIST_NOTE = (
    "This list is shared by every user of this vSNP install, so who may "
    "change it is the site admins' decision."
)


def _uid_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name if pwd else f"uid {uid}"
    except KeyError:  # an account the name service does not know (HPC LDAP)
        return f"uid {uid}"


def _account() -> str:
    """The account the backend runs as (one OS user per OnDemand session)."""
    if hasattr(os, "geteuid"):
        name = _uid_name(os.geteuid())
        if not name.startswith("uid "):
            return name
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "this account"


def _owner(p: Path) -> str:
    try:
        return _uid_name(p.stat().st_uid)
    except OSError:
        return "unknown"


def _mounted_read_only(p: Path) -> bool:
    try:
        return bool(os.statvfs(p).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False


def registry_unreadable_reason(deps_file: Path) -> str:
    """Why this account cannot read the registry: the file with its owner and
    mode, or, when even the folder holding it cannot be entered, that folder."""
    blocked, what = deps_file, "the site's reference location list"
    try:
        st = deps_file.stat()
    except OSError:
        blocked = deps_file.parent
        what = f"the folder that holds the site's reference location list ({deps_file.name})"
        try:
            st = blocked.stat()
        except OSError:
            st = None
    owner = _uid_name(st.st_uid) if st else "unknown"
    mode = f"{_stat.S_IMODE(st.st_mode):04o}" if st else "unknown"
    who = owner if st else "its owner"
    kind = "file" if blocked == deps_file else "folder"
    return (
        f"Account {_account()} cannot read {what}, {blocked} (owner: {owner}, "
        f"mode {mode}), so vSNP cannot show this site's reference locations or "
        f"the references in them. Only {who} or root can change that {kind} or "
        f"who may read it. {SHARED_LIST_NOTE}"
    )


class RegistryUnreadable(PermissionError):
    """The registry is there but this account may not read it.

    Raised instead of the bare PermissionError so that every caller's failure
    carries the reason, and main.py turns it into a 403 that says so. On USDA
    SciComp, 2026-11-04, Add, the location list and the reference dropdowns all
    failed with a bare HTTP 500 because the list was unreadable to the account.
    The Reference Locations panel then said "No reference locations configured."
    """

    def __init__(self, deps_file: Path, exc: OSError):
        super().__init__(exc.errno, exc.strerror, str(deps_file))
        self.file = deps_file
        self.reason = registry_unreadable_reason(deps_file)


def _read_registry(deps_file: Path) -> Optional[List[str]]:
    """The registry's non-blank lines, or None when there is no registry file."""
    try:
        text = deps_file.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except PermissionError as exc:
        raise RegistryUnreadable(deps_file, exc) from exc
    return [line.strip() for line in text.splitlines() if line.strip()]


def _registry_readable(deps_file: Path) -> bool:
    """True when the registry can be read, or when there is none yet to read."""
    try:
        deps_file.stat()
    except FileNotFoundError:
        return True
    except OSError:  # the folder holding it cannot be entered
        return False
    return os.access(deps_file, os.R_OK)


def registry_write_access(vsnp3_path: Path) -> Dict:
    """Can THIS account change the reference-location registry — asked before
    anyone types a path, so a shared install that this account may not edit
    shows read-only controls instead of failing after the click (USDA SciComp,
    2026-11-04: Add reported a bare HTTP 500).

    A save is mkstemp in dependencies/ plus os.replace over the file, so it is
    the FOLDER that must be writable, not the file; with no dependencies/ yet,
    the save creates it inside the install. The current list is read first, so
    the file must be readable too. An unreadable list is reported as such, not
    as an unwritable folder: they have different owners to ask. Returns
    {"writable", "readable", "reason", "installed", "account", "folder",
    "folder_owner", "file"}; reason is None when writable."""
    deps_dir = vsnp3_path / "dependencies"
    deps_file = deps_dir / "reference_options_paths.txt"
    folder = deps_dir if deps_dir.is_dir() else vsnp3_path
    account = _account()
    out = {
        "writable": False,
        "readable": True,
        "reason": None,
        "installed": folder.is_dir(),
        "account": account,
        "folder": str(folder),
        "folder_owner": _owner(folder),
        "file": str(deps_file),
    }
    if not out["installed"]:
        out["reason"] = (
            f"There is no vsnp3 install at {vsnp3_path}. Set vsnp3_path in "
            f"Settings to a directory that contains bin/vsnp3_step2.py."
        )
        return out
    if not _registry_readable(deps_file):
        out["readable"] = False
        out["reason"] = registry_unreadable_reason(deps_file)
        return out
    if folder == deps_dir:
        where = (f"{folder} (owner: {out['folder_owner']}), the folder that "
                 f"holds {deps_file.name}")
    else:
        where = (f"{folder} (owner: {out['folder_owner']}), where a change "
                 f"would create dependencies/{deps_file.name}")
    if _mounted_read_only(folder):
        out["reason"] = (
            f"{where}, is on a file system mounted read-only on "
            f"{socket.gethostname()}, so account {account} cannot change the "
            f"reference locations from here. {SHARED_LIST_NOTE}"
        )
    elif not os.access(folder, os.W_OK | os.X_OK):
        out["reason"] = (
            f"Account {account} cannot write {where}, so it cannot change the "
            f"reference locations. {SHARED_LIST_NOTE}"
        )
    else:
        out["writable"] = True
    return out


def sanitize_upstream_paths(vsnp3_path: Path) -> List[str]:
    """One-time removal of the upstream author's shipped paths (see
    UPSTREAM_SHIPPED_PATHS) from this install's registry — but ONLY the ones
    that do not exist on this machine. A shipped path that exists here is the
    author's (or this site's) real reference location, not stale clutter; on
    the USDA Ames HPC removing it broke `-t` resolution for every reference
    under it. Runs at backend startup; a marker file next to the registry
    makes it once-per-install, so a user who deliberately re-adds or removes
    one of those paths afterwards keeps their choice.

    On installs sanitized by an earlier version (which removed the shipped
    paths unconditionally), a one-time repair re-adds any removed path that
    DOES exist on this machine (recorded in the marker as `restored:`).

    Returns the list of removed paths (empty when nothing changed)."""
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    marker = deps_file.with_name(_SANITIZED_MARKER)
    if not deps_file.is_file():
        return []
    if marker.exists():
        _restore_upstream_paths_that_exist(deps_file, marker)
        return []
    existing = get_reference_paths(vsnp3_path)
    removed = [p for p in existing
               if p in UPSTREAM_SHIPPED_PATHS and not Path(p).is_dir()]
    kept = [p for p in existing if p not in removed]
    kept_upstream = [p for p in kept if p in UPSTREAM_SHIPPED_PATHS]
    try:
        if removed:
            _write_paths_file(deps_file, kept)
        marker.write_text(
            "reference_options_paths.txt was checked for upstream-shipped "
            "author paths at first backend start.\nremoved: "
            + (", ".join(removed) if removed else "(none)") + "\n"
            + ("kept (exists on this machine): " + ", ".join(kept_upstream) + "\n"
               if kept_upstream else "")
            # This install never removed a real path, so the repair pass for
            # installs sanitized by the old unconditional code must not run.
            + "restored: (not applicable)\n",
            encoding="utf-8",
        )
    except OSError:
        # Read-only install (e.g. a shared env this user can't write): leave
        # it; the next start by someone with write access will do it.
        return []
    return removed


def _restore_upstream_paths_that_exist(deps_file: Path, marker: Path) -> List[str]:
    """Repair pass for installs sanitized by the old unconditional code: any
    upstream-shipped path it removed that DOES exist on this machine was real
    local configuration (the Ames HPC case), so put it back. Once per install
    — the marker gains a `restored:` line, and a user who deletes the path
    from the registry afterwards keeps it deleted."""
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return []
    if "restored:" in text:
        return []
    removed_line = next(
        (l for l in text.splitlines() if l.startswith("removed:")), "")
    removed = [p.strip() for p in removed_line[len("removed:"):].split(",")
               if p.strip() and p.strip() != "(none)"]
    to_restore = [p for p in removed
                  if p in UPSTREAM_SHIPPED_PATHS and Path(p).is_dir()]
    current = [line.strip()
               for line in deps_file.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    add = [p for p in to_restore if p not in current]
    try:
        if add:
            _write_paths_file(deps_file, current + add)
        with marker.open("a", encoding="utf-8") as fh:
            fh.write("restored: " + (", ".join(add) if add else "(none)") + "\n")
    except OSError:
        return []
    if add:
        logger.info("Restored reference locations removed by an earlier "
                    "version (they exist on this machine): %s", ", ".join(add))
    return add


# File types that mark a directory as a real vsnp3 reference option. A
# reference dir always carries at least a FASTA (or, for one just created by
# "Download New Reference", the copied template spreadsheets while the FASTA
# downloads). Reference ROOTS are often git clones, so without this check
# `.git` — and any other incidental subdirectory — shows up in the reference
# dropdowns as a selectable "reference".
_REF_CONTENT_SUFFIXES = {
    ".fasta", ".fa", ".fna", ".fas", ".gbk", ".gb", ".gff", ".gff3", ".xlsx",
}


def _is_reference_dir(d: Path) -> bool:
    try:
        for entry in d.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_file() and entry.suffix.lower() in _REF_CONTENT_SUFFIXES:
                return True
    except OSError:
        return False
    return False


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def list_references(vsnp3_path: Path) -> List[Dict]:
    """Unique reference options across all registered roots, in vsnp3
    resolution order.

    vsnp3 resolves ``-t <name>`` by directory NAME across the registered
    roots and the first hit wins, so two roots holding the same name are ONE
    selectable reference, not two. Each name is returned once with the root
    it actually resolves to, plus any shadowed copies elsewhere, so the UI
    can say where a reference comes from instead of listing indistinguishable
    duplicates. Hidden directories and directories containing no
    reference-shaped files are skipped (see _REF_CONTENT_SUFFIXES).

    Entry shape: {name, path, root, shadowed: [{path, root}, ...]}.
    """
    by_name: Dict[str, Dict] = {}
    order: List[str] = []
    for parent in _load_reference_paths(vsnp3_path):
        if not parent.exists():
            continue
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not _is_reference_dir(child):
                continue
            entry = by_name.get(child.name)
            if entry is None:
                by_name[child.name] = {
                    "name": child.name,
                    "path": str(child),
                    "root": str(parent),
                    "shadowed": [],
                }
                order.append(child.name)
            elif not _same_dir(child, Path(entry["path"])):
                # Same dir reached twice (a registered symlink of a registered
                # root) is not a duplicate; a different dir with the same name
                # is — record it as shadowed by the winning copy.
                entry["shadowed"].append({"path": str(child), "root": str(parent)})
    return [by_name[n] for n in order]


def reference_locations(vsnp3_path: Path) -> List[Dict]:
    """Per-registered-root stats for the Reference Locations editor.

    Says what each root actually contributes to the dropdowns — and what it
    merely duplicates — so removing a redundant location is an informed act
    instead of a guess. Entry shape:
    {path, exists, provides, shadowed, implicit} where `provides` is the
    number of reference names this root resolves (wins), `shadowed` the
    number it holds that an earlier root already provides, and `implicit`
    marks the built-in fallback root (vsnp3's own dependencies/ dir, used
    only when no registry file exists) — shown for transparency, not
    removable.
    """
    refs = list_references(vsnp3_path)
    wins: Dict[str, int] = {}
    dups: Dict[str, int] = {}
    for r in refs:
        wins[r["root"]] = wins.get(r["root"], 0) + 1
        for s in r["shadowed"]:
            dups[s["root"]] = dups.get(s["root"], 0) + 1
    registered = {str(Path(p)) for p in get_reference_paths(vsnp3_path)}
    out: List[Dict] = []
    for root in _load_reference_paths(vsnp3_path):
        key = str(root)
        out.append({
            "path": key,
            "exists": root.is_dir(),
            "provides": wins.get(key, 0),
            "shadowed": dups.get(key, 0),
            "implicit": key not in registered,
        })
    return out


# Both readers raise RegistryUnreadable rather than a bare PermissionError when
# the list is there but this account cannot read it. Answering "no locations"
# instead would send vSNP looking for references in the wrong place, silently.
def _load_reference_paths(vsnp3_path: Path) -> List[Path]:
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    lines = _read_registry(deps_file)
    if lines is not None:
        return [Path(line) for line in lines]
    fallback = vsnp3_path / "dependencies"
    return [fallback] if fallback.exists() else []


def get_reference_paths(vsnp3_path: Path) -> List[str]:
    deps_file = vsnp3_path / "dependencies" / "reference_options_paths.txt"
    return _read_registry(deps_file) or []


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
