"""One listing of a project's Step 1 samples per project switch, and what each
sample directory holds that its own mtime vouches for.

Every request a project switch fires needs the same first answers: which
sample directories exist, and for each one which reads, stats workbooks and
edit folders sit directly inside it. Each request used to go and get them
itself. The status list, the edits list, the sample browser, the Step 2
sample count, the project card and the Results scan each listed every sample
directory: six listings of 8,000 directories for one click, and a fresh
backend (every Open OnDemand launch) did all of it again. On shared storage
each listing is a round trip per directory, and a new session on the Ames HPC
took minutes on a project it had shown the day before.

Two facts make that unnecessary. A directory's mtime changes when a direct
child is created, removed or renamed, so anything about a sample that is a
question about its direct children (has reads, is paired, which stats
workbooks with which sizes and mtimes, has vcf_edits/) is answered for good
by one listing made at one mtime. And the requests of one switch arrive
together, so one listing can serve them all.

``listing`` is that one listing, memoised for a few seconds so the requests
of one switch share it. ``facts`` are the per-directory answers, kept in
step1/.sample_index.json keyed by each directory's mtime, so a fresh backend
re-lists only the directories that changed while nothing was watching.
Nothing here decides membership or status: every caller keeps its own rule
and applies it to what the index recorded, so a sample dir that is a symlink,
a scaffolding folder or a dotfile is treated exactly as before.

What the index cannot see, and why that is acceptable: a file rewritten IN
PLACE under the same name changes no directory mtime. vsnp3 writes a new,
timestamped stats workbook every run, and the edit and staging paths create
and remove files rather than overwrite them, so nothing the app does is
invisible to it. Correctness never depends on the file existing: a missing or
unwritable index only means directories are listed again.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # imported as app.step1_index by the backend, as a top-level module by tests
    from app.fanout import fan_out
except ImportError:
    from fanout import fan_out

INDEX_BASENAME = ".sample_index.json"
_INDEX_VERSION = 1
# How long one listing serves the requests of one click. Long enough that a
# switch's dozen requests, and the ones they trigger, share it; short enough
# that a change made from a shell is seen almost at once. The project cards
# already trust their counts for this long.
_LISTING_TTL_S = float(os.environ.get("VSNP_GUI_LISTING_TTL", "3") or 3)
# The racy-timestamp guard make and ccache use: an answer is only persisted
# when the directory's mtime is comfortably in the past, because on a coarse
# (1 s) filesystem a change landing in the same tick would leave the mtime
# looking unchanged.
_SETTLE_NS = 2_000_000_000


@dataclass(frozen=True)
class Sample:
    """One directory entry of step1/. `hidden` is a dot name, `scaffold` an
    underscore name (the writer's _provenance/ sibling); callers filter."""
    name: str
    path: str
    is_symlink: bool

    @property
    def hidden(self) -> bool:
        return self.name.startswith(".")

    @property
    def scaffold(self) -> bool:
        return self.name.startswith("_")

    @property
    def regular(self) -> bool:
        return not (self.hidden or self.scaffold)


class Listing:
    """Every directory directly under step1/, sorted by name, with each one's
    mtime fetched on demand, once, for all of them at once."""

    def __init__(self, step1_dir: Path, samples: List[Sample], dir_mtime_ns: Optional[int]):
        self.step1_dir = step1_dir
        self.samples = samples
        self.dir_mtime_ns = dir_mtime_ns
        self.at = time.monotonic()
        self._mtimes: Dict[str, Optional[int]] = {}
        self._lock = threading.Lock()

    def regular(self) -> List[Sample]:
        return [s for s in self.samples if s.regular]

    def mtimes_for(self, names) -> Dict[str, Optional[int]]:
        """name -> the directory's mtime_ns (following a symlink), or None when
        it cannot be read, for these names. One stat per directory not yet
        asked about, all of them at once, remembered for the listing's life:
        a warm status poll asks about the few samples that just finished, a
        cold visit about all of them, and nobody asks twice."""
        wanted = set(names)
        with self._lock:
            missing = [s for s in self.samples if s.name in wanted and s.name not in self._mtimes]
            if missing:
                def _mt(s: Sample) -> Optional[int]:
                    try:
                        return os.stat(s.path).st_mtime_ns
                    except OSError:
                        return None
                self._mtimes.update(zip((s.name for s in missing), fan_out(_mt, missing)))
            return {n: self._mtimes.get(n) for n in wanted}

    def mtimes(self) -> Dict[str, Optional[int]]:
        """Every directory's mtime (see mtimes_for)."""
        return self.mtimes_for([s.name for s in self.samples])


_LISTINGS: Dict[str, Listing] = {}
_FACTS_MEMO: Dict[str, tuple] = {}     # step1 dir -> ((size, mtime_ns) of the index file, samples)
_LOCK = threading.RLock()


def _dir_mtime_ns(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def invalidate(step1_dir: Path) -> None:
    """Forget the memoised listing: for the endpoints that just changed it."""
    with _LOCK:
        _LISTINGS.pop(str(step1_dir), None)


def touched(step1_dir: Path) -> None:
    """The app itself just wrote a cache file into step1/, which moved the
    directory's mtime; the listing is as true as it was. Called after every
    such write, so the app's own bookkeeping never triggers a re-listing."""
    with _LOCK:
        hit = _LISTINGS.get(str(step1_dir))
        if hit is not None:
            hit.dir_mtime_ns = _dir_mtime_ns(step1_dir)


def listing(step1_dir: Path) -> Listing:
    """The directories under step1/, from one scandir shared by every request
    made within a few seconds. Re-listed sooner when step1/'s own mtime moves,
    which is what a sample added or removed (by anyone) does to it, and at
    once when an endpoint that changed a sample calls invalidate()."""
    key = str(step1_dir)
    with _LOCK:
        hit = _LISTINGS.get(key)
        now = time.monotonic()
        dir_mtime = _dir_mtime_ns(step1_dir)
        if hit is not None and now - hit.at < _LISTING_TTL_S and hit.dir_mtime_ns == dir_mtime:
            return hit
        samples: List[Sample] = []
        try:
            with os.scandir(step1_dir) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            entries = []
        for e in entries:
            try:
                if not e.is_dir():
                    continue
                samples.append(Sample(e.name, e.path, e.is_symlink()))
            except OSError:
                continue
        lst = Listing(step1_dir, samples, dir_mtime)
        _LISTINGS[key] = lst
        return lst


def _compute(sample: Sample, step1_dev: Optional[int]) -> Dict[str, Any]:
    """One scandir of a sample directory: its reads with their identities, its
    stats workbooks with their signatures, and whether it holds vcf_edits."""
    dev = step1_dev
    if sample.is_symlink:
        try:
            dev = os.stat(sample.path).st_dev
        except OSError:
            dev = None
    fq: List[list] = []
    stats: Dict[str, list] = {}
    edits = False
    try:
        with os.scandir(sample.path) as it:
            for e in it:
                n = e.name
                if n == "vcf_edits":
                    edits = True
                elif n.endswith(".fastq.gz"):
                    # Identity as the project card counts it (see
                    # projects._add_read_identity): a plain file's inode comes
                    # from the directory entry, a symlink's from one stat that
                    # follows it. None means unreadable: the card counts the
                    # path instead.
                    link = False
                    try:
                        link = e.is_symlink()
                        if link:
                            st = e.stat()
                            fq.append([n, True, st.st_dev, st.st_ino])
                        elif dev is not None:
                            fq.append([n, False, dev, e.inode()])
                        else:
                            st = e.stat(follow_symlinks=False)
                            fq.append([n, False, st.st_dev, st.st_ino])
                    except OSError:
                        fq.append([n, link, None, None])
                elif not n.startswith(".") and fnmatchcase(n, "*_stats.xlsx"):
                    try:
                        st = os.stat(e.path)
                        stats[n] = [st.st_mtime_ns, st.st_size]
                    except OSError:
                        pass
    except OSError:
        return {"fq": [], "stats": {}, "edits": False, "unreadable": True}
    return {"fq": fq, "stats": stats, "edits": edits}


def _load(step1_dir: Path) -> Dict[str, list]:
    path = step1_dir / INDEX_BASENAME
    try:
        st = path.stat()
    except OSError:
        return {}
    sig = (st.st_size, st.st_mtime_ns)
    hit = _FACTS_MEMO.get(str(step1_dir))
    if hit is not None and hit[0] == sig:
        return hit[1]
    stored: Dict[str, list] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == _INDEX_VERSION and isinstance(data.get("samples"), dict):
            stored = {k: v for k, v in data["samples"].items()
                      if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], dict)}
    except Exception:
        stored = {}
    _FACTS_MEMO[str(step1_dir)] = (sig, stored)
    return stored


def _save(step1_dir: Path, samples: Dict[str, list]) -> None:
    """Atomic and best-effort: a project this user cannot write stays unindexed."""
    path = step1_dir / INDEX_BASENAME
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=INDEX_BASENAME + ".", dir=str(step1_dir))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": _INDEX_VERSION, "samples": samples}, fh)
        os.replace(tmp, path)
        tmp = None
        try:
            os.chmod(path, 0o664)  # share the index with the project's group
        except OSError:
            pass
        try:
            st = path.stat()
            _FACTS_MEMO[str(step1_dir)] = ((st.st_size, st.st_mtime_ns), samples)
        except OSError:
            _FACTS_MEMO.pop(str(step1_dir), None)
        touched(step1_dir)
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def facts(step1_dir: Path, lst: Optional[Listing] = None) -> Dict[str, Dict[str, Any]]:
    """name -> {fq, stats, edits} for every directory in the listing.

    Answered from the index for a directory whose mtime is the one recorded,
    listed afresh (all at once) for the rest, and written back when anything
    new was learned. A directory changed within the last two seconds is
    answered but not recorded (see _SETTLE_NS).
    """
    lst = lst or listing(step1_dir)
    with _LOCK:
        stored = _load(step1_dir)
        mtimes = lst.mtimes()
        todo = [s for s in lst.samples if mtimes.get(s.name) is None or s.name not in stored
                or stored[s.name][0] != mtimes[s.name]]
        out: Dict[str, Dict[str, Any]] = {}
        for s in lst.samples:
            if s not in todo:
                out[s.name] = stored[s.name][1]
        if todo:
            try:
                step1_dev: Optional[int] = step1_dir.stat().st_dev
            except OSError:
                step1_dev = None
            fresh = fan_out(lambda s: _compute(s, step1_dev), todo)
            now = time.time_ns()
            updates: Dict[str, list] = {}
            for s, f in zip(todo, fresh):
                out[s.name] = f
                mt = mtimes.get(s.name)
                if mt is not None and not f.get("unreadable") and now - mt > _SETTLE_NS:
                    updates[s.name] = [mt, f]
            present = {s.name for s in lst.samples}
            kept = {k: v for k, v in stored.items() if k in present}
            if updates or kept.keys() != stored.keys():
                kept.update(updates)
                _save(step1_dir, kept)
        return out


def stats_sigs(step1_dir: Path) -> Dict[str, list]:
    """{workbook path: [mtime_ns, size]} for the Results scan: every
    *_stats.xlsx directly inside a non-dot directory of step1/, as
    qc_scan._stats_files discovered them."""
    lst = listing(step1_dir)
    fx = facts(step1_dir, lst)
    out: Dict[str, list] = {}
    for s in lst.samples:
        if s.hidden:
            continue
        for name, sig in fx.get(s.name, {}).get("stats", {}).items():
            out[os.path.join(s.path, name)] = sig
    return out
