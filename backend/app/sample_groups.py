"""Which defining-SNP groups each VCF falls into, read without running Step 2.

vsnp3 sorts a comparison into groups from the reference's define_filter
workbook (vsnp3_group_on_defining_snps.Group): row 1 names a position per
column, row 2 the group that position defines. A sample is in a group when
every position the column names is a GOOD SNP in its VCF — QUAL above -w,
AC=2, single-base REF and ALT, MQ at least -y — and none of the positions
marked "!" is. The Step 2 build list shows those groups, and filters on them,
so a comparison can be chosen by group before it is run.

The rules are vsnp3's, mirrored one for one, because a group shown here that
the run then disagrees with would choose the wrong samples:

  * a record is read the way VCF_to_DF reads vsnp3's normalized copy: quotes
    dropped, runs of spaces as tabs, ";MQM=" as ";MQ="; INFO split on ";" and
    "=", an item that splits into more than two parts leaving AC and MQ
    unknown; QUAL and MQ truncated to integers; AC parsed as a number or 0;
    the FIRST record at a position the one kept.
  * with filters on (no -n), the positions listed in the workbook's first
    column, ranges expanded, are dropped before the thresholds apply.
  * a column whose position is "#"-marked is live only with -hash.
  * a sample with no good SNP at all is in no group: vsnp3 drops it as empty,
    which only changes anything for a group defined by "!" positions alone.

Not mirrored: density filtering. vsnp3 applies it over every sample of a run
together, so no per-sample answer can give it; the pane says so.

One shortcut, and why it is exact: records whose ALT, QUAL, FILTER and INFO
are all "." (zero-coverage positions, most of a _zc.vcf) are skipped unread.
They can never be good SNPs, and a position with no coverage carries no other
call that such a record could shadow as "first at its position".

Cost is reading each VCF once. What a read learns is kept per file in
step2/.vcf_group_cache.json for one (size, mtime): the QUAL and MQ at each
defining position whose record passes the rules that do not depend on a
threshold (AC=2, single bases, both values present) — so a later request costs
one stat per VCF, and a changed -w or -y is re-applied without a read.
The cache is stamped with the defining positions; edit those and every VCF is
read again. Correctness never depends on it.

The reads are CPU: splitting and parsing 4-5 MB of text per VCF, about 40 ms
each on one core, and 24,000 VCFs on the Ames projects. Threads cannot share
that work (the GIL), so a batch of eight or more files is read in worker
PROCESSES (VSNP_GUI_VCF_WORKERS wide; one fewer than the cores, at most 8),
started fresh ("spawn") rather than forked from the multi-threaded server.
What a worker returns is exactly what a thread returned; the cache is written
by this process, as before. Smaller batches, and a backend where a pool
cannot start, read in threads as they always did.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import multiprocessing
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from app.fanout import fan_out

CACHE_BASENAME = ".vcf_group_cache.json"
_CACHE_VERSION = 1

# vsnp3_version.py's defaults (-w, -y).
QUAL_THRESHOLD = 150
MQ_THRESHOLD = 56
AC_HOMOZYGOUS = 2

# ALT, QUAL, FILTER and INFO all "." — a zero-coverage record.
_ZC = b"\t.\t.\t.\t.\t"
_SPACES = re.compile(rb" +")

Sig = Tuple[int, int]                       # (size, mtime_ns)
Record = List                               # [qual|None, ac, mq|None, snv]
Candidate = List                            # [qual, mq]: could be good at some threshold


# --- The define_filter -------------------------------------------------------

@dataclass(frozen=True)
class Definitions:
    path: str
    # (group name, positions that must be good, positions that must not be),
    # in the workbook's column order.
    groups: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...]
    wanted: FrozenSet[str]                  # every position any group names
    filter_all: FrozenSet[str]              # first-column filters, expanded
    needs_any_good: bool                    # some group is "!" positions only
    digest: str                             # what the per-file cache depends on


def define_filter_of(ref_dir: Optional[Path]) -> Tuple[Optional[Path], str]:
    """The workbook vsnp3 reads as defining SNPs for this reference folder.

    vsnp3_reference_options.metadata_gather's rule: every *xlsx that is not an
    Excel lock file and not a *remove* or *meta* workbook; exactly one is
    allowed. Returns (path, "") or (None, why).
    """
    if not ref_dir:
        return None, "the project's reference folder could not be found"
    try:
        found = sorted(str(p) for p in Path(ref_dir).glob("*xlsx"))
    except OSError as exc:
        return None, f"the reference folder could not be read: {exc}"
    found = [f for f in found if not re.search(r"~\$.*", f)]
    found = [f for f in found if not re.search(r".*remove.*", f)]
    found = [f for f in found if not re.search(r".*meta.*", f)]
    if not found:
        return None, "the reference has no defining-SNP workbook"
    if len(found) > 1:
        names = ", ".join(os.path.basename(f) for f in found)
        return None, f"the reference folder has more than one defining-SNP workbook ({names}); vsnp3 requires exactly one"
    return Path(found[0]), ""


def _expand(entries: Iterable) -> List[str]:
    """Group.list_expansion: "chrom:100-200" becomes every position in it."""
    out: List[str] = []
    for entry in entries:
        entry = str(entry)
        if "-" not in entry.split(":")[-1]:
            out.append(entry)
        elif "-" in entry:
            try:
                chrom, span = entry.split(":")
                lo, hi = span.split("-")
                for pos in range(int(lo.replace(",", "")), int(hi.replace(",", "")) + 1):
                    out.append(f"{chrom}:{pos}")
            except ValueError:
                continue                    # vsnp3 stops the run on these
    return out


_DEF_CACHE: Dict[tuple, Definitions] = {}


def load_definitions(path: Path, hash_groups: bool = False) -> Definitions:
    """Parse the workbook exactly as Group.__init__ does. Cached per file state."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size, bool(hash_groups))
    hit = _DEF_CACHE.get(key)
    if hit is not None:
        return hit
    import pandas as pd

    if hash_groups:
        df = pd.read_excel(path, header=None)
        if not df.empty:
            first = df.iloc[0]
            df.iloc[0] = first.apply(lambda x: str(x).replace("#", "") if pd.notna(x) else x)
            df.columns = df.iloc[0]
            df = df.drop(df.index[0]).reset_index(drop=True)
            df = df.dropna(axis=1, how="all")
            df = df.loc[:, (df != "").any()]
    else:
        df = pd.read_excel(path)
    try:
        spec = df.iloc[:, 1:].head(n=1).to_dict(orient="records")[0]
    except IndexError:
        try:
            spec = df.iloc[:, 0:].head(n=1).to_dict(orient="records")[0]
        except IndexError:
            spec = {}
    groups = []
    wanted: set = set()
    for abs_pos, group in spec.items():
        parts = str(abs_pos).split(", ")
        normal = tuple(p for p in parts if not p.endswith("!"))
        inverted = tuple(p[:-1] for p in parts if p.endswith("!"))
        if not (normal or inverted):
            continue
        groups.append((str(group), normal, inverted))
        wanted.update(normal)
        wanted.update(inverted)
    filter_all: List[str] = []
    if not df.empty:
        raw = [x for x in df.iloc[:, 0].to_list()[1:] if str(x) != "nan"]
        filter_all = _expand(raw)
    needs_any_good = any(not normal for _name, normal, _inv in groups)
    h = hashlib.sha256()
    for p in sorted(wanted):
        h.update(p.encode() + b"\n")
    if needs_any_good:
        # Only then does a file's answer depend on the filters (see
        # read_records): otherwise they are applied at evaluation time.
        h.update(b"\x00any\x00")
        for p in sorted(filter_all):
            h.update(p.encode() + b"\n")
    defs = Definitions(str(path), tuple(groups), frozenset(wanted), frozenset(filter_all),
                       needs_any_good, h.hexdigest()[:20])
    if len(_DEF_CACHE) > 8:
        _DEF_CACHE.clear()
    _DEF_CACHE[key] = defs
    return defs


# --- One VCF ------------------------------------------------------------------

def _num(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _info(info: str) -> Tuple[Optional[str], Optional[str]]:
    """(AC, MQ) as VCF_to_DF.read_vcf's extract_info_field finds them."""
    try:
        d = dict(item.split("=") for item in info.split(";") if "=" in item)
    except ValueError:
        return None, None
    return d.get("AC"), d.get("MQ")


def parse_record(line: bytes) -> Optional[Tuple[str, Record]]:
    """(abs_pos, [qual, ac, mq, snv]) for one data line, or None."""
    text = line.decode("utf-8", "replace").rstrip()
    if not text:
        return None
    # _write_normalized, in its order, for a data line.
    text = text.replace(";MQM=", ";MQ=")
    if '"' in text:
        text = text.replace('"AC=', "AC=")
        text = text.replace('"', "")
    if " " in text:
        text = re.sub(r" +", "\t", text)
    fields = text.split("\t")
    if len(fields) < 8:
        return None
    chrom, pos, _id, ref, alt, qual, _filter, info = fields[:8]
    p = _num(pos)
    abs_pos = f"{chrom}:{int(p) if p is not None else 0}"
    q = _num(qual)
    ac_raw, mq_raw = _info(info)
    ac = _num(ac_raw)
    mq = _num(mq_raw)
    return abs_pos, [
        int(q) if q is not None else None,          # np.trunc, nullable
        int(ac) if ac is not None else 0,            # fillna(0).astype(int)
        int(mq) if mq is not None else None,
        len(ref) == 1 and len(alt) == 1,
    ]


def _candidate(rec: Record) -> Optional[Candidate]:
    """[qual, mq] when the record passes every rule a threshold cannot change."""
    q, ac, mq, snv = rec
    if q is None or mq is None or ac != AC_HOMOZYGOUS or not snv:
        return None
    return [q, mq]


def read_records(path: Path, defs: Definitions) -> Tuple[Dict[str, Candidate], Dict]:
    """What one VCF says at the defining positions, thresholds not yet applied.

    Returns (candidates, best): `candidates` maps each defining position whose
    first record could be a good SNP to its [qual, mq]; `best`, only when some
    group is defined by "!" positions alone, holds the highest QUAL seen at each
    MQ among all the file's candidates — enough to answer "has it any good
    SNP?" at any thresholds — under "all" and, with filters on, "kept".
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as fh:
        data = fh.read()
    wanted = defs.wanted
    need_best = defs.needs_any_good
    records: Dict[str, Candidate] = {}
    seen: set = set()
    best_all: Dict[int, int] = {}
    best_kept: Dict[int, int] = {}
    for line in [l for l in data.split(b"\n") if _ZC not in l]:
        if not line or line[0] == 35:                 # "#"
            continue
        # The position straight from the raw bytes, when normalizing could not
        # change it: tab-separated, no quotes, a plain decimal POS. Anything
        # else takes parse_record's full normalization first.
        if b'"' not in line and b" " not in line:
            head = line.split(b"\t", 2)
            if len(head) > 2 and head[1].isdigit() and head[1][:1] != b"0":
                key = (head[0] + b":" + head[1]).decode("utf-8", "replace")
                if key in seen:
                    continue                        # drop_duplicates keeps the first
                if not need_best and key not in wanted:
                    seen.add(key)
                    continue
        parsed = parse_record(line)
        if parsed is None:
            continue
        abs_pos, rec = parsed
        if abs_pos in seen:
            continue
        seen.add(abs_pos)
        cand = _candidate(rec)
        if cand is None:
            continue
        if abs_pos in wanted:
            records[abs_pos] = cand
        if need_best:
            q, mq = cand
            if q > best_all.get(mq, -1):
                best_all[mq] = q
            if abs_pos not in defs.filter_all and q > best_kept.get(mq, -1):
                best_kept[mq] = q
    best = {"all": best_all, "kept": best_kept} if need_best else {}
    return records, best


def groups_of(records: Dict[str, Candidate], best: Dict, defs: Definitions,
              qual_threshold: int = QUAL_THRESHOLD, mq_threshold: int = MQ_THRESHOLD,
              no_filters: bool = False) -> List[str]:
    """The groups Group.group_selection puts this file in, in workbook order."""
    good = {pos for pos, (q, mq) in records.items()
            if (no_filters or pos not in defs.filter_all) and q > qual_threshold and mq >= mq_threshold}
    nonempty = None
    out: List[str] = []
    for name, normal, inverted in defs.groups:
        if normal and not good.issuperset(normal):
            continue
        if inverted and any(p in good for p in inverted):
            continue
        if not normal:
            # "!" alone: the sample must still have survived as non-empty.
            if nonempty is None:
                pool = best.get("all" if no_filters else "kept", {})
                nonempty = bool(good) or any(
                    q > qual_threshold for mq, q in pool.items() if int(mq) >= mq_threshold)
            if not nonempty:
                continue
        out.append(name)
    return out


# --- The per-project cache ---------------------------------------------------

def _stat_sig(path: Path) -> Optional[Sig]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


class GroupCache:
    """Per-file reads, valid for one (size, mtime) and one set of definitions.

    Saved merged onto the file as it is on disk, atomically, so two requests
    running side by side cannot erase each other's work (ContigMatcher's rule).
    """

    def __init__(self, cache_path: Optional[Path], digest: str):
        self._path = cache_path
        self._digest = digest
        self._files: Dict[str, list] = {}
        self._new: Dict[str, list] = {}
        self._lock = threading.Lock()
        if cache_path:
            self._files = self._read()

    def _read(self) -> Dict[str, list]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if (data.get("version") == _CACHE_VERSION and data.get("digest") == self._digest
                    and isinstance(data.get("files"), dict)):
                return data["files"]
        except Exception:
            pass
        return {}

    def get(self, name: str, sig: Sig) -> Optional[Tuple[Dict[str, Candidate], Dict]]:
        hit = self._files.get(name)
        if hit and len(hit) == 4 and hit[0] == sig[0] and hit[1] == sig[1]:
            return hit[2], hit[3]
        return None

    def put(self, name: str, sig: Sig, records: Dict[str, Candidate], best: Dict) -> None:
        row = [sig[0], sig[1], records, best]
        with self._lock:
            self._files[name] = row
            self._new[name] = row

    def save(self) -> None:
        if not (self._new and self._path):
            return
        tmp = None
        try:
            merged = self._read()
            with self._lock:
                merged.update(self._new)
                self._new = {}
            fd, tmp = tempfile.mkstemp(prefix=CACHE_BASENAME + ".", dir=str(self._path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": _CACHE_VERSION, "digest": self._digest, "files": merged},
                          fh, separators=(",", ":"))
            os.replace(tmp, self._path)
            tmp = None
            try:
                os.chmod(self._path, 0o664)
            except OSError:
                pass
        except Exception:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass


# --- Reading in worker processes ---------------------------------------------

# Fewer files than this are read in threads: a pool costs a few hundred
# milliseconds to start, which a handful of reads does not earn back.
POOL_MIN_FILES = 8
_POOL_DEFS: Optional[Definitions] = None    # set once per worker by _pool_init


def pool_width() -> int:
    """How many worker processes read at once: VSNP_GUI_VCF_WORKERS, or one
    fewer than the cores so the backend keeps answering, at most 8 — these run
    on a shared login or OnDemand node, not a batch allocation."""
    try:
        forced = int(os.environ.get("VSNP_GUI_VCF_WORKERS", "") or 0)
    except ValueError:
        forced = 0
    if forced > 0:
        return forced
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _pool_init(defs: Definitions) -> None:
    global _POOL_DEFS
    _POOL_DEFS = defs


def _read_in_worker(item: Tuple[str, str, Sig]) -> Tuple[str, Sig, Optional[Dict], Optional[Dict]]:
    """read_records for one file, in a worker: (name, sig, records, best), the
    last two None when the file could not be read — the same answer the thread
    path gives, carried back as plain dicts and lists."""
    name, path, sig = item
    try:
        records, best = read_records(Path(path), _POOL_DEFS)
    except (OSError, EOFError, ValueError, UnicodeError):
        return name, sig, None, None
    return name, sig, records, best


def _start_pool(defs: Definitions, workers: int) -> Optional[ProcessPoolExecutor]:
    """A spawn-started pool, or None where one cannot be had (a restricted
    environment, no spawn support): the caller then reads in threads."""
    try:
        return ProcessPoolExecutor(max_workers=workers,
                                   mp_context=multiprocessing.get_context("spawn"),
                                   initializer=_pool_init, initargs=(defs,))
    except Exception:
        return None


def sample_groups(files: List[Tuple[str, Path]], defs: Definitions, cache_path: Optional[Path],
                  qual_threshold: int = QUAL_THRESHOLD, mq_threshold: int = MQ_THRESHOLD,
                  no_filters: bool = False, budget_s: float = 20.0) -> Dict:
    """Groups for every (filename, path), reading only what the cache lacks.

    Reads stop once `budget_s` has passed — a request on shared storage must
    answer before a proxy gives up on it — and what was read is saved, so the
    next request carries on from there. `pending` counts the files still to be
    read; the caller asks again until it is 0.

    A batch of POOL_MIN_FILES or more is read in worker processes (see the
    module docstring); fewer, or a pool that cannot start or breaks, in threads.
    """
    started = time.monotonic()
    cache = GroupCache(cache_path, defs.digest)
    sigs = fan_out(lambda item: _stat_sig(item[1]), files)
    todo = [(name, path, sig) for (name, path), sig in zip(files, sigs)
            if sig is not None and cache.get(name, sig) is None]

    def read_one(item):
        name, path, sig = item
        try:
            records, best = read_records(path, defs)
        except (OSError, EOFError, ValueError, UnicodeError):
            return None
        cache.put(name, sig, records, best)
        return name

    def read_chunk_in_pool(pool, chunk) -> List[Optional[str]]:
        out: List[Optional[str]] = []
        items = [(name, str(path), sig) for name, path, sig in chunk]
        for name, sig, records, best in pool.map(_read_in_worker, items, chunksize=4):
            if records is None:
                out.append(None)
                continue
            cache.put(name, sig, records, best)
            out.append(name)
        return out

    read = 0
    unreadable = 0
    batch = 256
    i = 0
    width = pool_width()
    pool = None
    if len(todo) >= POOL_MIN_FILES and width > 1:
        pool = _start_pool(defs, min(width, len(todo)))
    try:
        # At least one batch per request, however long the stats took: otherwise
        # storage slow enough to spend the budget on stats alone would leave every
        # request with nothing read, and the pane polling forever.
        while i < len(todo) and (i == 0 or time.monotonic() - started < budget_s):
            chunk = todo[i:i + batch]
            if pool is not None:
                try:
                    done = read_chunk_in_pool(pool, chunk)
                except Exception:
                    # A worker died or the pool broke: finish this and every
                    # later batch in threads rather than answer nothing.
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    pool = None
                    done = fan_out(read_one, chunk)
            else:
                done = fan_out(read_one, chunk)
            read += sum(1 for d in done if d)
            unreadable += sum(1 for d in done if not d)
            i += len(chunk)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    cache.save()

    groups: Dict[str, List[str]] = {}
    for (name, _path), sig in zip(files, sigs):
        hit = cache.get(name, sig) if sig is not None else None
        if hit is not None:
            groups[name] = groups_of(hit[0], hit[1], defs, qual_threshold, mq_threshold, no_filters)
    return {
        "groups": groups,
        "group_names": [name for name, _n, _i in defs.groups],
        "total": len(files),
        "read": read,
        "unreadable": unreadable,
        "pending": len(todo) - i,
        "elapsed_s": round(time.monotonic() - started, 2),
    }
