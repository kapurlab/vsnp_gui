"""Where vSNP's reference folders are, and which one a run uses.

The list of reference LOCATIONS (parent folders whose subfolders are reference
types) is data, kept by this app in two places:

  site list      <SITE_ROOT>/refs/vsnp3/locations/reference_locations.txt,
                 beside the site's shared reference data. One per site, shared
                 by every user; whoever may write that folder may change it.
  personal list  "reference_locations" in the user's own config.json
                 (~/.config/vsnp_gui/config.json). Only that user sees it.

The effective list is the site list, then the personal one. Add writes to the
site list when this account can, and otherwise to the personal list, and says
which. Nothing here writes inside the vsnp3 install.

vSNP 3 keeps its own list inside its conda install
(dependencies/reference_options_paths.txt). On a shared deployment that file
broke over and over: ordinary users cannot write the software folder, every
save made it 0600, conda updates replaced it with the package's copy, and an
admin's chmod on NFSv4 rewrote its ACL. So it is now read only to SEED the site
list, once (see seed_site_list), and runs no longer depend on it: Step 1 and
Step 2 are handed the reference FOLDER this list resolves to (`-t <folder>`,
see main.py), and vsnp3 takes that folder's files exactly as it would have for
`-t <name>`. Until a site list has been seeded, the install's entries are shown
(read-only, marked as vSNP's own list) so nothing disappears in between.
"""
import errno
import getpass
import logging
import os
import shlex
import shutil
import socket
import stat as _stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import grp
    import pwd
except ImportError:  # not POSIX
    grp = pwd = None

# Paths the upstream vsnp3 author ships inside the package
# (dependencies/reference_options_paths.txt). They are his own machines' paths,
# not a choice made at THIS site, so they are left out when the install's list
# seeds the site list, BUT only when the path does not exist on this machine:
# on the author's own systems (the USDA Ames HPC, where
# /project/mycobacteria_brucella/... is real and holds the reference folders)
# they are genuine configuration.
UPSTREAM_SHIPPED_PATHS = frozenset({
    "/Users/todstuber/vsnp3_test_dataset/vsnp_dependencies",
    "/home/tstuber/vSNP_reference_options",
    "/project/mycobacteria_brucella/mycobacterium/vsnp_dependencies",
})

# config.json keys. SITE_LIST_KEY is site-derived (config.SITE_DERIVED_KEYS):
# recomputed from the launch context on every load, never frozen per user.
SITE_LIST_KEY = "reference_locations_file"
PERSONAL_KEY = "reference_locations"

# Where each effective entry came from.
SITE = "site"
PERSONAL = "personal"
INSTALL = "install"   # vsnp3's own list, shown only until the site list is seeded

INSTALL_LIST = Path("dependencies") / "reference_options_paths.txt"

logger = logging.getLogger("uvicorn.error")

# Said wherever the site list cannot be changed by this account.
SHARED_LIST_NOTE = (
    "The site list is shared by every vSNP user here, so who may change it is "
    "the site admins' decision."
)


# --------------------------------------------------------------------------
# Accounts and file facts, for messages that say who and where
# --------------------------------------------------------------------------

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


def _mode(p: Path) -> str:
    try:
        return f"{_stat.S_IMODE(p.stat().st_mode):04o}"
    except OSError:
        return "unknown"


def _mounted_read_only(p: Path) -> bool:
    try:
        return bool(os.statvfs(p).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False


def _exists(p: Path) -> Optional[bool]:
    """True/False, or None when this account cannot tell (a folder on the way
    may not be entered). Path.exists() raises PermissionError there."""
    try:
        p.stat()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _norm(p: str) -> str:
    """A location's spelling, normalised without asking the file system."""
    return os.path.normpath(os.path.expanduser(p))


def _key(p: str) -> str:
    """A location's identity: its resolved path, or the spelled one when
    resolving fails. Asks the file system, so only Add and Remove use it."""
    try:
        return str(Path(p).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return _norm(p)


def _unreadable_reason(path: Path, what: str) -> str:
    """Why this account cannot read a list file: the file with its owner and
    mode, or, when even the folder holding it cannot be entered, that folder."""
    blocked, kind = path, "file"
    try:
        path.stat()
    except OSError:
        blocked, kind = path.parent, "folder"
    owner = _owner(blocked)
    return (
        f"Account {_account()} cannot read {what}, {blocked} (owner: {owner}, "
        f"mode {_mode(blocked)}). Only {owner if owner != 'unknown' else 'its owner'} "
        f"or root can change that {kind} or who may read it."
    )


def _read_list(path: Optional[Path], what: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """A list file's non-blank lines. (lines, None); (None, None) when there is
    no such file; (None, reason) when it is there and cannot be read. Never
    raises: a list that cannot be read is reported, not a 500."""
    if path is None:
        return None, None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except IsADirectoryError:
        return None, f"{path} is a folder, not a list file."
    except OSError as exc:
        if isinstance(exc, PermissionError):
            return None, _unreadable_reason(path, what)
        return None, f"Could not read {what}, {path}: {exc.strerror or exc}."
    return [line.strip() for line in text.splitlines() if line.strip()], None


# --------------------------------------------------------------------------
# The three lists
# --------------------------------------------------------------------------

def install_list_file(cfg: Dict) -> Path:
    """vsnp3's own list, inside its install. Read to seed the site list and for
    best-reference auto-detection (which vsnp3 does itself); never written."""
    return Path(str(cfg.get("vsnp3_path", "") or "")) / INSTALL_LIST


def site_list_file(cfg: Dict) -> Optional[Path]:
    val = str(cfg.get(SITE_LIST_KEY, "") or "").strip()
    return Path(val) if val else None


def _seed_marker(site_file: Path) -> Path:
    return site_file.with_name(f".{site_file.name}.seeded")


def _seeded(cfg: Dict) -> bool:
    f = site_list_file(cfg)
    return bool(f is not None and _exists(_seed_marker(f)))


def personal_locations(cfg: Dict) -> List[str]:
    raw = cfg.get(PERSONAL_KEY)
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for p in raw:
        if isinstance(p, str) and p.strip() and p.strip() not in out:
            out.append(p.strip())
    return out


def _install_candidates(lines: List[str]) -> Tuple[List[str], List[str]]:
    """The install list's entries worth carrying over, and the upstream author
    paths left out (shipped in the package, absent from this machine). A
    shipped path this account cannot look into is kept: it may well be real."""
    keep, dropped = [], []
    for p in lines:
        if p in UPSTREAM_SHIPPED_PATHS and _exists(Path(p)) is False:
            dropped.append(p)
        elif p not in keep:
            keep.append(p)
    return keep, dropped


def site_list_status(cfg: Dict) -> Dict:
    """Can THIS account read and change the site list? Asked before anyone types
    a path, so the UI can say where an Add will go. A save is a temp file in the
    list's folder renamed over the list, so it is the FOLDER that must be
    writable; a missing folder is created inside its parent (which must exist).
    The current list is read first, so the file must be readable too.

    {file, folder, exists, readable, writable, reason, seeded, account}; reason
    is None when writable."""
    f = site_list_file(cfg)
    account = _account()
    out = {"file": str(f) if f else None, "folder": str(f.parent) if f else None,
           "exists": False, "readable": True, "writable": False, "reason": None,
           "seeded": False, "account": account}
    if f is None:
        out["reason"] = "This vSNP has no site data folder configured, so there is no site list."
        return out
    folder = f.parent
    out["seeded"] = _seeded(cfg)
    exists = _exists(f)
    out["exists"] = bool(exists)
    if exists is None or (exists and not os.access(f, os.R_OK)):
        out["readable"] = False
        out["reason"] = _unreadable_reason(f, "the site's reference location list")
        return out
    target = folder if _is_dir(folder) else folder.parent
    if not _is_dir(target):
        out["reason"] = (f"The site list's folder {folder} does not exist, and neither "
                         f"does {folder.parent}, so there is no site list to add to.")
    elif _mounted_read_only(target):
        out["reason"] = (f"{target} is on a file system mounted read-only on "
                         f"{socket.gethostname()}, so account {account} cannot change "
                         f"the site list from here. {SHARED_LIST_NOTE}")
    elif not os.access(target, os.W_OK | os.X_OK):
        what = (f"the folder that holds the site list, {folder}" if target == folder
                else f"{target}, where the site list's folder would be created")
        out["reason"] = (f"Account {account} cannot write {what} (owner: {_owner(target)}, "
                         f"mode {_mode(target)}). {SHARED_LIST_NOTE}")
    else:
        out["writable"] = True
    return out


def _entries(cfg: Dict) -> Tuple[List[Dict], List[str]]:
    """The effective list, in resolution order: site, then (until the site list
    is seeded) the install's entries, then personal. A location listed twice is
    kept where it first appears. Returns (entries, notes); entries are
    {path, source}. Never raises on an unreadable list: it becomes a note.

    Costs one stat (the seed marker) and one read (the site list), plus one
    read of the install's list until the site list is seeded."""
    notes: List[str] = []
    out: List[Dict] = []
    seen: set = set()

    def _take(paths: List[str], source: str) -> None:
        for p in paths:
            k = _norm(p)
            if k not in seen:
                seen.add(k)
                out.append({"path": p, "source": source})

    site_lines, why = _read_list(site_list_file(cfg), "the site's reference location list")
    if why:
        notes.append(f"The site list cannot be read, so its locations are not shown. {why}")
    _take(site_lines or [], SITE)
    if not _seeded(cfg):
        lines, why = _read_list(install_list_file(cfg),
                                "vSNP's own reference list inside its install")
        if why:
            notes.append("vSNP's own list (shown until the site list is set up) "
                         f"cannot be read. {why}")
        _take(_install_candidates(lines or [])[0], INSTALL)
    _take(personal_locations(cfg), PERSONAL)
    return out, notes


def site_list_state(status: Dict) -> str:
    """One word (or three) for the site list, as the Reference Locations pane
    shows it: "editable", "read-only for you", "not readable for you", "not
    created yet", or "none" when this vSNP has no site list at all. Read off
    site_list_status, so it never disagrees with where an Add goes."""
    if not status.get("file"):
        return "none"
    if not status.get("readable", True):
        return "not readable for you"
    if not status.get("exists"):
        return "not created yet"
    return "editable" if status.get("writable") else "read-only for you"


def stored_in(cfg: Dict, status: Dict, personal_file: str) -> Dict:
    """Where the locations shown come from, for the pane's "Stored in" block:
    {"site": {file, state}, "personal": {file, key}, "install": {file, exists}
    or None}. The install's list is included only until the site list is
    seeded, which is exactly when its entries are shown (source "install")."""
    inst = install_list_file(cfg)
    return {
        "site": {"file": status.get("file"), "state": site_list_state(status)},
        "personal": {"file": personal_file, "key": PERSONAL_KEY},
        "install": (None if status.get("seeded")
                    else {"file": str(inst), "exists": bool(_exists(inst))}),
    }


def reference_roots(cfg: Dict) -> List[Path]:
    """The effective reference locations, in resolution order. Cheap: reads the
    lists, opens no reference folder."""
    return [Path(e["path"]) for e in _entries(cfg)[0]]


# --------------------------------------------------------------------------
# Writing a list file (the site list; never the install's)
# --------------------------------------------------------------------------

def _write_list_file(path: Path, lines: List[str]) -> None:
    """Rewrite a list atomically via a temp file + rename, keeping the old
    file's permissions (see _keep_permissions). Creates the list's own folder
    when its parent exists; never a deeper tree."""
    if not _is_dir(path.parent):
        path.parent.mkdir(exist_ok=True)
    try:
        old = path.stat()
    except FileNotFoundError:
        old = None
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n" if lines else "")
        _keep_permissions(path, old, tmp)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _new_list_mode(folder: Path) -> int:
    """A first list: readable by all, and group-writable when its folder is
    (a setgid lab folder), so the admins' folder setting carries to the file."""
    try:
        group_writable = bool(folder.stat().st_mode & _stat.S_IWGRP)
    except OSError:
        group_writable = False
    return (0o664 if group_writable else 0o644) & ~_current_umask()


def _keep_permissions(path: Path, old: Optional[os.stat_result], tmp: str) -> None:
    """Give the replacement list the permissions of the list it replaces.

    mkstemp makes the temp file 0600 and the rename puts it in place, so a save
    used to turn a list the site had shared (0664 in a setgid group folder, or
    an ACL) into one only the saver could read (USDA SciComp, 2026-11-04). Sites
    decide who may read and edit a list, so a save keeps exactly what was there
    and never widens it.

    copystat carries the mode and, on Linux, the extended attributes that hold
    POSIX and NFSv4 ACLs. On Ames (/project is NFSv4) the mode reads 0600+ even
    on files every user can read; the ACL is what grants that access, so copying
    the mode alone would drop it. The owner can only be kept by root. The group
    is kept when this account may set it, and a setgid folder usually sees to it
    anyway. A first list gets _new_list_mode.

    Keeping permissions must never cost the save itself. A filesystem that
    refuses permission changes (some SMB/CIFS mounts fix every file's mode at
    mount time) falls back to the mode bits alone, and then to a plain save:
    there the mount, not this file, decides who can read it.
    """
    if old is None:
        try:
            os.chmod(tmp, _new_list_mode(path.parent))
        except OSError as exc:
            logger.warning("reference locations: saved without setting permissions (%s)", exc)
        return
    try:
        shutil.copystat(str(path), tmp)
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


# --------------------------------------------------------------------------
# Seeding the site list from vsnp3's own list (once)
# --------------------------------------------------------------------------

def seed_site_list(cfg: Dict) -> Dict:
    """Carry the install's reference locations into the site list, once.

    Runs at backend start and before the first change to the site list. Only
    an account that can write the site list does it; anyone else keeps seeing
    the install's entries (read-only) until someone who can has started vSNP.
    The upstream author's shipped paths are left out when they do not exist
    here. A marker beside the site list makes it once per site, so a location
    an admin removes later is never re-added, and nothing reads the install's
    list again. The install's list is never written.

    Returns {"seeded", "added", "left_out", "reason"}.
    """
    out = {"seeded": False, "added": [], "left_out": [], "reason": None}
    f = site_list_file(cfg)
    if f is None or _seeded(cfg):
        return out
    status = site_list_status(cfg)
    if not status["writable"]:
        out["reason"] = status["reason"]
        return out
    inst = install_list_file(cfg)
    lines, why = _read_list(inst, "vSNP's own reference list inside its install")
    if why:
        # Not marked: the install's entries cannot be read now, but may be
        # readable to the next account that starts vSNP.
        out["reason"] = why
        return out
    candidates, left_out = _install_candidates(lines or [])
    current, why = _read_list(f, "the site's reference location list")
    if why:
        out["reason"] = why
        return out
    current = current or []
    have = {_key(p) for p in current}
    added = [p for p in candidates if _key(p) not in have]
    if added or not status["exists"]:
        _write_list_file(f, current + added)
    marker_text = (
        f"Seeded from {inst} by {_account()}.\n"
        f"added: {', '.join(added) if added else '(none)'}\n"
        f"left out (upstream author paths not on this machine): "
        f"{', '.join(left_out) if left_out else '(none)'}\n"
        f"From now on {f.name} is the site's list; {inst.name} inside the vSNP "
        f"install is not read for it again.\n"
    )
    try:
        _seed_marker(f).write_text(marker_text, encoding="utf-8")
    except OSError as exc:
        logger.warning("reference locations: seeded %s but could not write its marker (%s)", f, exc)
    out.update(seeded=True, added=added, left_out=left_out)
    if added:
        logger.info("Reference locations: site list %s seeded from %s: %s", f, inst, ", ".join(added))
    return out


# --------------------------------------------------------------------------
# Add / remove
# --------------------------------------------------------------------------

class SiteListRefused(PermissionError):
    """A change only the site list could make, refused for this account."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_REFUSED_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}


def _refused(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or exc.errno in _REFUSED_ERRNOS


def os_error_text(exc: OSError) -> str:
    return (exc.strerror or str(exc)) + (f": {exc.filename}" if exc.filename else "")


def _site_write(cfg: Dict, change: Callable[[List[str]], List[str]]) -> None:
    """Apply `change` to the site list's lines and save them, seeding first."""
    f = site_list_file(cfg)
    seed_site_list(cfg)
    lines, why = _read_list(f, "the site's reference location list")
    if why:
        raise SiteListRefused(why)
    _write_list_file(f, change(lines or []))


_WHERE = {SITE: "the site list", PERSONAL: "your personal list",
          INSTALL: "vSNP's own list (copied to the site list once that is set up)"}


def add_reference_location(cfg: Dict, new_path: str,
                           save: Callable[[Dict], None]) -> Dict:
    """Add a location: to the site list when this account can write it, else to
    the personal list. Returns {"list": "site"|"personal"|None, "path", "note"};
    list is None when the location was already there. `save` persists cfg (the
    personal list lives in config.json). An OSError from the site list other
    than a refusal is raised for the caller to explain."""
    resolved = str(Path(new_path).expanduser().resolve())
    k = _key(resolved)
    entries, _notes = _entries(cfg)
    for e in entries:
        if _norm(e["path"]) == _norm(resolved) or _key(e["path"]) == k:
            return {"list": None, "path": resolved,
                    "note": f"{resolved} is already a reference location, on {_WHERE[e['source']]}."}
    status = site_list_status(cfg)
    reason = status["reason"]
    if status["writable"]:
        try:
            _site_write(cfg, lambda lines: lines + [resolved])
            return {"list": SITE, "path": resolved, "note": None}
        except SiteListRefused as exc:
            reason = exc.reason
        except OSError as exc:
            if not _refused(exc):
                raise
            reason = (f"Account {status['account']} was refused saving the site list "
                      f"{status['file']} ({os_error_text(exc)}). {SHARED_LIST_NOTE}")
    cfg[PERSONAL_KEY] = personal_locations(cfg) + [resolved]
    save(cfg)
    return {"list": PERSONAL, "path": resolved,
            "note": (f"Added {resolved} to your personal list, which only you see. "
                     f"It was not added to the site list: {reason}")}


def remove_reference_location(cfg: Dict, path: str,
                              save: Callable[[Dict], None]) -> Dict:
    """Remove a location from the list(s) holding it: the personal list always,
    the site list when this account can write it. A location only the site list
    holds, on a site list this account cannot change, raises SiteListRefused.
    Returns {"removed_from": [...], "note"}. No files are deleted."""
    k, n = _key(path), _norm(path)

    def same(p: str) -> bool:
        return _norm(p) == n or _key(p) == k

    # The effective list keeps a location where it first appears, so a personal
    # copy of a site entry is not in it; ask the personal list itself.
    entries, _notes = _entries(cfg)
    sources = {e["source"] for e in entries if same(e["path"])}
    if any(same(p) for p in personal_locations(cfg)):
        sources.add(PERSONAL)
    removed: List[str] = []
    note = None
    if PERSONAL in sources:
        cfg[PERSONAL_KEY] = [p for p in personal_locations(cfg) if not same(p)]
        save(cfg)
        removed.append(PERSONAL)
    if sources & {SITE, INSTALL}:
        status = site_list_status(cfg)
        if status["writable"]:
            try:
                _site_write(cfg, lambda lines: [p for p in lines if not same(p)])
                removed.append(SITE)
            except OSError as exc:
                if not (removed and _refused(exc)):
                    raise
                note = (f"Removed {path} from your personal list. It is still on the "
                        f"site list, which could not be saved: {os_error_text(exc)}.")
        elif removed:
            note = (f"Removed {path} from your personal list. It is still on the site "
                    f"list, which this account cannot change: {status['reason']}")
        else:
            raise SiteListRefused(
                f"{path} is on the site's reference location list, which this "
                f"account cannot change. {status['reason']}")
    if not sources:
        note = f"{path} is not one of the reference locations; nothing was removed."
    return {"removed_from": removed, "note": note}


# --------------------------------------------------------------------------
# What an admin runs to fix the permissions (shown in the Reference Editor)
# --------------------------------------------------------------------------
#
# The user, 2026-11-05: "If the permissions are not correct for the file
# visibly show what needs to be done to fix." Each fix is the problem in a
# sentence plus the exact commands, with this site's real paths, the account,
# and the current owner, group and mode filled in. On NFSv4 (Ames' /project)
# chmod can rewrite a file's ACL, so the nfs4_setfacl form, with an
# inheritable group ACE on folders, is given as well.

_PROC_MOUNTS = "/proc/mounts"
_IDMAPD_CONF = "/etc/idmapd.conf"
GROUP_PLACEHOLDER = "<group>"
DOMAIN_PLACEHOLDER = "<nfs4-domain>"


def _my_uid() -> int:
    return os.geteuid() if hasattr(os, "geteuid") else -1


def _my_groups() -> List[int]:
    try:
        return list({*os.getgroups(), os.getegid()})
    except (OSError, AttributeError):
        return []


def _group_name(gid: int) -> Optional[str]:
    if grp is None:
        return None
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return None


def _personal_group(name: str, gid: int) -> bool:
    """A user-private group: named after a user whose primary group it is."""
    if pwd is None:
        return False
    try:
        return pwd.getpwnam(name).pw_gid == gid
    except KeyError:
        return False


def _q(p) -> str:
    return shlex.quote(str(p))


def _nearest_existing(p: Path) -> Path:
    while not _exists(p) and p != p.parent:
        p = p.parent
    return p


def _filesystem_type(p: Path) -> str:
    """"nfs4" when `p` is on an NFSv4 mount (Linux /proc/mounts), else "posix".
    Elsewhere there is no /proc/mounts, and plain mode bits are assumed."""
    try:
        text = Path(_PROC_MOUNTS).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "posix"
    try:
        target = str(_nearest_existing(p).resolve())
    except (OSError, RuntimeError):
        target = str(p)
    best, fstype, opts = "", "", ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mnt = parts[1].replace("\\040", " ")
        inside = target == mnt or target.startswith(mnt.rstrip("/") + "/")
        if inside and len(mnt) >= len(best):
            best, fstype, opts = mnt, parts[2], parts[3]
    if fstype == "nfs4" or (fstype == "nfs" and ("vers=4" in opts or "nfsvers=4" in opts)):
        return "nfs4"
    return "posix"


def _nfs4_domain() -> Optional[str]:
    """The NFSv4 id-mapping domain (Domain = ... in /etc/idmapd.conf)."""
    try:
        text = Path(_IDMAPD_CONF).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, val = line.partition("=")
        if key.strip().lower() == "domain" and val.strip() and not line.lstrip().startswith("#"):
            return val.strip()
    return None


def _nfs4_getfacl(p: Path) -> Optional[str]:
    """nfs4_getfacl's output for `p`, or None when the tool is not here."""
    try:
        out = subprocess.run(["nfs4_getfacl", str(p)], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _nfs4_others_may_read(p: Path) -> Optional[bool]:
    """Does an ACE grant read to anyone but the owner? None when unknown. On
    NFSv4 the mode bits can read 0600 on a file the ACL shares with everyone,
    so the mode alone would raise a false alarm there."""
    acl = _nfs4_getfacl(p)
    if acl is None:
        return None
    for line in acl.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 4 and parts[0] == "A" and parts[2] != "OWNER@" and "r" in parts[3]:
            return True
    return False


class _FixContext:
    """What every command needs: the site's group (or a placeholder), the
    file system, and the NFSv4 domain."""

    def __init__(self, cfg: Dict, anchor: Path):
        self.account = _account()
        self.notes: List[str] = []
        self.fs = _filesystem_type(anchor)
        shown = _nearest_existing(anchor)
        try:
            gid = shown.stat().st_gid
        except OSError:
            gid = None
        name = _group_name(gid) if gid is not None else None
        if name and not _personal_group(name, gid):
            self.group = name
        else:
            self.group = GROUP_PLACEHOLDER
            why = (f"the group of {shown}, {name}, is the personal group of user {name}"
                   if name else f"the group of {shown} has no name here")
            self.notes.append(f"Replace {GROUP_PLACEHOLDER} with the lab group whose "
                              f"members use vSNP ({why}).")
        self.domain = None
        if self.fs == "nfs4":
            self.domain = _nfs4_domain()
            if not self.domain:
                self.domain = DOMAIN_PLACEHOLDER
                self.notes.append(f"Replace {DOMAIN_PLACEHOLDER} with the NFSv4 domain "
                                  f"(Domain in {_IDMAPD_CONF}; nfs4_getfacl shows it).")

    def ace(self, flags: str, perms: str) -> str:
        return f"A:{flags}g:{self.group}@{self.domain}:{perms}"


def _describe(p: Path) -> str:
    try:
        st = p.stat()
    except OSError:
        return f"{p}"
    group = _group_name(st.st_gid) or f"gid {st.st_gid}"
    return (f"{p} (owner {_uid_name(st.st_uid)}, group {group}, mode "
            f"{_stat.S_IMODE(st.st_mode):04o})")


def site_list_fix(cfg: Dict) -> Optional[Dict]:
    """What is wrong with the site list's permissions, and what an admin runs to
    fix it; None when nothing is. {"cases", "problem", "commands",
    "nfs4_commands", "filesystem", "notes", "recheck"}.

    Cases: the list's folder is missing; this account cannot write the folder
    (or is not in the group the folder is shared with); the list cannot be
    read; the list is narrower than its folder (0600 in a group-shared folder),
    so other users cannot read it although this account can."""
    f = site_list_file(cfg)
    if f is None:
        return None
    folder = f.parent
    ctx = _FixContext(cfg, folder)
    g, acc = ctx.group, ctx.account
    cases: List[str] = []
    problems: List[str] = []
    cmds: List[str] = []
    nfs: List[str] = []

    def add(case, problem, posix, nfs4=()):
        cases.append(case)
        problems.append(problem)
        cmds.extend(c for c in posix if c not in cmds)
        nfs.extend(c for c in nfs4 if c not in nfs)

    def share_folder() -> Tuple[List[str], List[str]]:
        return ([f"chgrp {_q(g)} {_q(folder)}", f"chmod 2775 {_q(folder)}"],
                [f"nfs4_setfacl -a {_q(ctx.ace('fd', 'RWX'))} {_q(folder)}"])

    def share_file() -> Tuple[List[str], List[str]]:
        return ([f"chgrp {_q(g)} {_q(f)}", f"chmod 664 {_q(f)}"],
                [f"nfs4_setfacl -a {_q(ctx.ace('', 'RW'))} {_q(f)}"])

    folder_known = _exists(folder)
    if folder_known is False:
        parent = folder.parent
        posix, nfs4 = share_folder()
        add("folder_missing",
            f"The site list's folder {folder} does not exist"
            + ("" if _is_dir(parent) else f", nor does {parent}") + ".",
            [f"mkdir -p {_q(folder)}"] + posix, [f"mkdir -p {_q(folder)}"] + nfs4)
    elif folder_known is None or not os.access(folder, os.X_OK):
        posix, nfs4 = share_folder()
        add("list_unreadable",
            f"Account {acc} cannot enter the site list's folder, {_describe(folder)}.",
            posix, nfs4)
    else:
        try:
            fst = folder.stat()
        except OSError:
            fst = None
        file_known = _exists(f)
        if file_known and not os.access(f, os.R_OK):
            posix, nfs4 = share_file()
            add("list_unreadable",
                f"Account {acc} cannot read the site list, {_describe(f)}.", posix, nfs4)
        if not os.access(folder, os.W_OK | os.X_OK) and not _mounted_read_only(folder):
            fgroup = _group_name(fst.st_gid) if fst else None
            if (fst and fst.st_uid != _my_uid() and fst.st_mode & _stat.S_IWGRP
                    and fst.st_gid not in _my_groups() and fgroup
                    and not _personal_group(fgroup, fst.st_gid)):
                add("folder_not_writable",
                    f"The site list's folder, {_describe(folder)}, is shared with group "
                    f"{fgroup}, and account {acc} is not a member of it.",
                    [f"usermod -aG {_q(fgroup)} {_q(acc)}"],
                    [])
                ctx.notes.append(f"A new group membership counts from {acc}'s next "
                                 f"login: start a new vSNP session after it. Where groups "
                                 f"come from LDAP/AD, the directory admins add it.")
            else:
                posix, nfs4 = share_folder()
                add("folder_not_writable",
                    f"Account {acc} cannot write the site list's folder, {_describe(folder)}, "
                    f"so it cannot change the site list.", posix, nfs4)
        if file_known and fst and os.access(f, os.R_OK):
            try:
                mode = _stat.S_IMODE(f.stat().st_mode)
            except OSError:
                mode = None
            fmode = _stat.S_IMODE(fst.st_mode)
            missing = [bit for bit in (_stat.S_IRGRP, _stat.S_IWGRP, _stat.S_IROTH)
                       if mode is not None and fmode & bit and not mode & bit]
            if missing and ctx.fs == "nfs4" and _nfs4_others_may_read(f) is not False:
                missing = []   # the ACL decides there, and it shares the file (or we cannot tell)
            if missing:
                posix, nfs4 = share_file()
                add("list_narrower",
                    f"The site list, {_describe(f)}, is narrower than its folder "
                    f"(mode {fmode:04o}), so other vSNP users cannot read"
                    + (" or change" if _stat.S_IWGRP in missing else "") + " it.",
                    posix, nfs4)
    if not cases:
        return None
    recheck = [f"ls -ld {_q(folder)}" + (f" {_q(f)}" if _exists(f) else "")]
    if ctx.fs == "nfs4":
        recheck.append(f"nfs4_getfacl {_q(folder)}")
    return {"cases": cases, "problem": " ".join(problems), "commands": cmds,
            "nfs4_commands": nfs if ctx.fs == "nfs4" else [],
            "filesystem": ctx.fs, "notes": ctx.notes, "recheck": recheck}


def location_fix(cfg: Dict, root: Path) -> Optional[Dict]:
    """For a listed folder this account cannot open: who owns what, and the
    chmod or ACL that lets the site's users open it. None when the cause is
    not permissions (a dangling link, a file, a missing path)."""
    blocker, whole_tree = None, True
    try:
        root.stat()
        if not os.access(root, os.R_OK | os.X_OK):
            blocker = root
    except PermissionError:
        whole_tree = False
        probe = Path(root.anchor or "/")
        for part in root.parts[1:]:
            probe = probe / part
            if _exists(probe) and not os.access(probe, os.X_OK):
                blocker = probe
                break
    except OSError:
        return None
    if blocker is None:
        return None
    site = site_list_file(cfg)
    ctx = _FixContext(cfg, site.parent if site else blocker)
    g = ctx.group
    try:
        st = blocker.stat()
        same_group = _group_name(st.st_gid) == g
    except OSError:
        same_group = False
    linux = Path(_PROC_MOUNTS).exists()
    if whole_tree:
        problem = (f"Account {ctx.account} may not open {_describe(blocker)}.")
        if same_group:
            posix = [f"chmod -R g+rX {_q(blocker)}"]
        elif linux:
            posix = [f"setfacl -R -m {_q(f'g:{g}:rX,d:g:{g}:rX')} {_q(blocker)}"]
        else:
            posix = [f"chgrp -R {_q(g)} {_q(blocker)}", f"chmod -R g+rX {_q(blocker)}"]
        nfs4 = [f"nfs4_setfacl -R -a {_q(ctx.ace('fd', 'RX'))} {_q(blocker)}"]
    else:
        problem = (f"Account {ctx.account} may not pass through {_describe(blocker)}, "
                   f"on the way to {root}.")
        if same_group:
            posix = [f"chmod g+x {_q(blocker)}"]
        elif linux:
            posix = [f"setfacl -m {_q(f'g:{g}:x')} {_q(blocker)}"]
        else:
            posix = [f"chgrp {_q(g)} {_q(blocker)}", f"chmod g+x {_q(blocker)}"]
        nfs4 = [f"nfs4_setfacl -a {_q(ctx.ace('', 'X'))} {_q(blocker)}"]
    return {"cases": ["location_cannot_open"], "problem": problem, "commands": posix,
            "nfs4_commands": nfs4 if ctx.fs == "nfs4" else [],
            "filesystem": ctx.fs, "notes": ctx.notes,
            "recheck": [f"ls -ld {_q(blocker)}"]}


# --------------------------------------------------------------------------
# What the locations hold
# --------------------------------------------------------------------------

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


def _open_root(root: Path) -> Tuple[str, Optional[str], List[Path]]:
    """(status, reason, children) for one location: "ok", "missing" or
    "cannot_open". A folder this account may not open, a dangling link or a
    file where a folder should be is "cannot_open" with the reason, never an
    exception: one bad location must not take the list down (Ames, 2026-11-05:
    a location the account could not open made GET /api/references/paths a
    bare HTTP 500)."""
    try:
        children = sorted(root.iterdir())
    except (FileNotFoundError, NotADirectoryError) as exc:
        if root.is_symlink():
            try:
                target = os.readlink(root)
            except OSError:
                target = "?"
            return "cannot_open", f"cannot open: it is a link to {target}, which does not exist here", []
        known = _exists(root)
        if isinstance(exc, NotADirectoryError) and known:
            return "cannot_open", "cannot open: it is a file, not a folder", []
        if known is None:
            return "cannot_open", (f"cannot open: account {_account()} may not enter a folder "
                                   f"on the way to it"), []
        return "missing", "path not found", []
    except PermissionError:
        return "cannot_open", (f"cannot open: account {_account()} may not open it (owner: "
                               f"{_owner(root)}, mode {_mode(root)})"), []
    except OSError as exc:
        return "cannot_open", f"cannot open: {exc.strerror or exc}", []
    return "ok", None, children


def survey(cfg: Dict) -> Dict:
    """One pass over the effective list: the reference options and what each
    location contributes. {"entries", "references", "locations", "site",
    "notes"}.

    vsnp3 resolves a reference by directory NAME across the locations and the
    first hit wins, so two locations holding the same name are ONE selectable
    reference, not two. Each name is listed once with the location it resolves
    to, plus any shadowed copies elsewhere. Hidden directories and directories
    with no reference-shaped files are skipped (see _REF_CONTENT_SUFFIXES).

    Reference entry: {name, path, root, shadowed: [{path, root}, ...]}.
    Location entry: {path, source, exists, status, reason, provides, shadowed,
    removable, implicit, fix}. `provides` counts the names this location
    resolves, `shadowed` the ones an earlier location already provides, and
    `fix` is location_fix's for a folder this account cannot open. `site_fix`
    is site_list_fix's.
    """
    entries, notes = _entries(cfg)
    status = site_list_status(cfg)
    by_name: Dict[str, Dict] = {}
    order: List[str] = []
    opened: Dict[str, Tuple[str, Optional[str]]] = {}
    for e in entries:
        parent = Path(e["path"])
        state, reason, children = _open_root(parent)
        opened[e["path"]] = (state, reason)
        for child in children:
            try:
                if child.name.startswith(".") or not child.is_dir():
                    continue
            except OSError:
                continue
            if not _is_reference_dir(child):
                continue
            entry = by_name.get(child.name)
            if entry is None:
                by_name[child.name] = {"name": child.name, "path": str(child),
                                       "root": str(parent), "shadowed": []}
                order.append(child.name)
            elif not _same_dir(child, Path(entry["path"])):
                # Same dir reached twice (a listed symlink of a listed location)
                # is not a duplicate; a different dir with the same name is.
                entry["shadowed"].append({"path": str(child), "root": str(parent)})
    refs = [by_name[n] for n in order]
    wins: Dict[str, int] = {}
    dups: Dict[str, int] = {}
    for r in refs:
        wins[r["root"]] = wins.get(r["root"], 0) + 1
        for s in r["shadowed"]:
            dups[s["root"]] = dups.get(s["root"], 0) + 1
    locations = []
    for e in entries:
        state, reason = opened[e["path"]]
        key = str(Path(e["path"]))
        locations.append({
            "path": e["path"],
            "source": e["source"],
            "exists": state == "ok",
            "status": state,
            "reason": reason,
            "provides": wins.get(key, 0),
            "shadowed": dups.get(key, 0),
            "removable": e["source"] == PERSONAL or status["writable"],
            "implicit": False,
            "fix": location_fix(cfg, Path(e["path"])) if state == "cannot_open" else None,
        })
    return {"entries": entries, "references": refs, "locations": locations,
            "site": status, "notes": notes, "site_fix": site_list_fix(cfg)}


def list_references(cfg: Dict) -> List[Dict]:
    """Unique reference options across the effective list (see survey)."""
    return survey(cfg)["references"]


def reference_locations(cfg: Dict) -> List[Dict]:
    """Per-location rows for the Reference Locations editor (see survey)."""
    return survey(cfg)["locations"]


def unresolved_reference_message(cfg: Dict, ref_name: str) -> str:
    """Why `ref_name` resolves to no folder, and what to do about it."""
    entries, notes = _entries(cfg)
    site = site_list_file(cfg)
    lists = [f"the site list ({site})" if site else "no site list"]
    if any(e["source"] == INSTALL for e in entries):
        lists.append(f"vSNP's own list ({install_list_file(cfg)})")
    lists.append("your personal list")
    msg = (f"Reference '{ref_name}' was not found: none of the {len(entries)} reference "
           f"location(s) in {', '.join(lists)} has a folder named '{ref_name}'. Add the "
           f"folder that contains it under Reference Editor > Reference Locations, or "
           f"choose another reference.")
    return " ".join([msg] + notes)


def _alnum_lower(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def find_gff_for_fasta(fasta_path: Path, cfg: Dict) -> Optional[Path]:
    """Locate a GFF/GFF3 that pairs with a step1 alignment fasta.

    Step 1 copies the reference fasta into the per-sample alignment
    directory but NOT the GFF, so the GFF must be looked up in the
    reference locations. vSNP3's reference dirs use inconsistent
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
    if not _exists(fasta_path):
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
    for root in reference_roots(cfg):
        state, _reason, children = _open_root(root)
        if state != "ok":
            continue
        for ref_dir in children:
            if not _is_dir(ref_dir):
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
            try:
                gffs = [g for pattern in ("*.gff3", "*.gff") for g in sorted(ref_dir.glob(pattern))]
            except OSError:
                continue
            for gff in gffs:
                score = name_score + (20 if gff.stem == stem else 0)
                if score > best_score:
                    best_score = score
                    best_gff = gff
    return best_gff
