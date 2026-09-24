"""Which reference a VCF was called against, decided by its coordinates.

A VCF names its reference twice. By FILE NAME: the ``##reference=`` header and
the ``alignment_<name>/`` directory vsnp3 writes it into. And by COORDINATE
SYSTEM: the ``##contig=<ID=...,length=...>`` line freebayes writes for every
sequence of the FASTA it was given. Only the second decides whether two VCFs'
positions can be compared — vsnp3 step 2 never reads ``##reference=`` at all.

The GUI used to decide by name alone, and a renamed copy of a reference defeated
it. The Ames owl project's reference ships as ``owl_25-003495-001.fasta``; 3,204
of its samples were called in earlier runs against the same eight segments saved
as ``25-003495-001.fasta``. Same contig names, same lengths, same positions — and
every one was reported as a foreign reference, kept out of Build and offered for
deletion.

So a VCF whose name does not match gets a second hearing on its contigs. If they
are exactly the reference's — every ID, every length, nothing more and nothing
less — it was called against the same coordinate system under another file
name. Names and lengths, not bases: a FASTA edited base for base under the same
contigs would pass. That is the price of not hashing sequence, and the same
judgement vsnp3 makes when it takes positions from whatever VCFs it is handed.

Cost is why this is shaped the way it is. Only VCFs that FAIL the name check ever
reach it; each file is read at most once per (size, mtime), because the verdict
is cached on disk, negative as well as positive; and the reads a cold cache still
needs are issued in parallel, because on a shared filesystem a header read is a
round trip, not bandwidth.
"""

from __future__ import annotations

import glob
import gzip
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

try:  # imported as app.ref_contigs by the backend, as a top-level module by scripts
    from app.fanout import fan_out
except ImportError:
    from fanout import fan_out

# Sorted (sequence name, length) pairs: a reference's coordinate system.
Contigs = Tuple[Tuple[str, int], ...]
# (size, mtime_ns) — what a cached verdict is valid for.
Sig = Tuple[int, int]

CACHE_BASENAME = ".vcf_contig_cache.json"
_CACHE_VERSION = 1

_ID_RE = re.compile(r"(?:^|,)ID=([^,>]+)")
_LEN_RE = re.compile(r"(?:^|,)length=(\d+)")


def vcf_contigs(path: Union[str, Path]) -> Optional[Contigs]:
    """The contigs a VCF header declares, or None when it cannot say.

    None covers a header with no ``##contig`` lines, a contig without a length,
    a repeated ID and an unreadable file — every case where the coordinate
    system is not stated in full, so nothing can be matched against it. Reading
    stops at the first line that is not ``##`` meta (the ``#CHROM`` line), so
    only the header is read.
    """
    pairs: Dict[str, int] = {}
    try:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith("##"):
                    break
                if not line.startswith("##contig=<"):
                    continue
                body = line.strip()[len("##contig=<"):]
                if body.endswith(">"):
                    body = body[:-1]
                mid, mlen = _ID_RE.search(body), _LEN_RE.search(body)
                if not mid or not mlen or mid.group(1) in pairs:
                    return None
                pairs[mid.group(1)] = int(mlen.group(1))
    except Exception:
        return None
    return tuple(sorted(pairs.items())) or None


_FASTA_MEMO: Dict[tuple, Optional[Contigs]] = {}
_FASTA_LOCK = threading.Lock()


def fasta_contigs(paths: List[Path]) -> Optional[Contigs]:
    """Every sequence across these FASTAs as (name, length), or None.

    Several files are one reference: vsnp3 concatenates every ``*fasta`` in a
    reference directory into the FASTA it aligns to (``concat_fasta`` in
    vsnp3_step1.py), so a two-chromosome Brucella reference is two files and one
    coordinate system. A sequence's name is the first word of its header line,
    which is what bwa, samtools and freebayes call it. Memoised per process on
    each file's size and mtime, so the shared reference tree is read once.
    """
    if not paths:
        return None
    try:
        key = tuple(
            (str(p), st.st_size, st.st_mtime_ns) for p, st in ((p, os.stat(p)) for p in paths)
        )
    except OSError:
        return None
    with _FASTA_LOCK:
        if key in _FASTA_MEMO:
            return _FASTA_MEMO[key]
    pairs: Dict[str, int] = {}
    result: Optional[Contigs] = None
    try:
        for p in paths:
            name: Optional[str] = None
            length = 0
            with open(p, "rb") as fh:
                for raw in fh:
                    if raw.startswith(b">"):
                        if name is not None:
                            pairs[name] = length
                        words = raw[1:].split(None, 1)
                        if not words:
                            raise ValueError("unnamed sequence")
                        name = words[0].decode("utf-8", "replace")
                        if name in pairs:
                            raise ValueError("repeated sequence name")
                        length = 0
                    else:
                        length += len(raw.strip())
            if name is not None:
                pairs[name] = length
        result = tuple(sorted(pairs.items())) or None
    except (OSError, ValueError):
        result = None
    with _FASTA_LOCK:
        _FASTA_MEMO[key] = result
    return result


def reference_fastas(ref_dir: Path) -> List[Path]:
    """The FASTAs vsnp3 aligns to for a reference directory.

    vsnp3's own rule, verbatim: ``sorted(glob.glob(f'{directory}/*fasta'))`` in
    vsnp3_reference_options.py — which, unlike ``Path.glob``, skips dotfiles.
    """
    pattern = os.path.join(glob.escape(str(ref_dir)), "*fasta")
    return [Path(p) for p in sorted(glob.glob(pattern))]


def reference_contigs(ref_dir: Optional[Path]) -> Optional[Contigs]:
    """The coordinate system of the reference in `ref_dir`, or None."""
    if not ref_dir:
        return None
    try:
        return fasta_contigs(reference_fastas(ref_dir))
    except Exception:
        return None


def contig_digest(contigs: Contigs) -> str:
    h = hashlib.sha256()
    for name, length in contigs:
        h.update(f"{name}\t{length}\n".encode("utf-8"))
    return h.hexdigest()[:20]


def _stat_sig(path: Path) -> Optional[Sig]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


class ContigMatcher:
    """Per-file answers to "is this VCF's coordinate system the reference's?"

    A matcher with no reference contigs — the reference FASTA is not reachable
    from this machine, or declares nothing usable — is disabled and answers
    False for every file, so each caller falls back to exactly the name-only
    behaviour that preceded it.

    Verdicts are cached in ``<project>/step2/.vcf_contig_cache.json``, keyed by
    the file's path under the project and valid for one (size, mtime). The cache
    is stamped with the reference's contig digest, so re-pointing a project at a
    different reference discards every verdict at once. Correctness never
    depends on it: a missing or unwritable cache only means files are read again.
    """

    def __init__(self, contigs: Optional[Contigs], cache_path: Optional[Path] = None,
                 root: Optional[Path] = None):
        self.contigs = contigs
        self.digest = contig_digest(contigs) if contigs else ""
        self._cache_path = cache_path
        self._root = str(root) if root else None
        self._files: Dict[str, list] = {}
        self._new: Dict[str, list] = {}
        # Files whose (size, mtime) this matcher has itself checked: a matcher
        # lives for one request, so a verdict it settled a moment ago needs no
        # second stat to be trusted (see prefetch).
        self._checked: set = set()
        self._lock = threading.Lock()
        if self.digest and cache_path:
            self._files = self._read_cache()

    @property
    def enabled(self) -> bool:
        return bool(self.contigs)

    def _key(self, path: Path) -> str:
        p = str(path)
        if self._root and p.startswith(self._root + os.sep):
            return p[len(self._root) + 1:]
        return p

    def _read_cache(self) -> Dict[str, list]:
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if (data.get("version") == _CACHE_VERSION and data.get("reference") == self.digest
                    and isinstance(data.get("files"), dict)):
                return data["files"]
        except Exception:
            pass
        return {}

    def cached(self, path: Path, sig: Sig) -> Optional[bool]:
        hit = self._files.get(self._key(path))
        if hit and len(hit) == 3 and hit[0] == sig[0] and hit[1] == sig[1]:
            return bool(hit[2])
        return None

    def _record(self, path: Path, sig: Sig, verdict: bool) -> None:
        row = [sig[0], sig[1], bool(verdict)]
        key = self._key(path)
        with self._lock:
            self._files[key] = row
            self._new[key] = row

    def matches(self, path: Path, sig: Optional[Sig] = None) -> bool:
        """True when `path` declares exactly the reference's contigs.

        `sig` is the file's (size, mtime_ns) when the caller has already
        stat()ed it, which saves the one filesystem call a cache hit costs.
        """
        if not self.enabled:
            return False
        if sig is None and self._key(path) in self._checked:
            return bool(self._files[self._key(path)][2])
        sig = sig or _stat_sig(path)
        if sig is None:
            return False
        verdict = self.cached(path, sig)
        if verdict is None:
            verdict = vcf_contigs(path) == self.contigs
            self._record(path, sig, verdict)
        return verdict

    def prefetch(self, items: Iterable[Union[Path, Tuple[Path, Optional[Sig]]]]) -> None:
        """Settle many files' verdicts at once, reading the uncached in parallel.

        Takes paths, or (path, sig) pairs when the caller already has the stat.
        Afterwards matches() on any of them is a cache hit.
        """
        if not self.enabled:
            return
        todo: List[Tuple[Path, Optional[Sig]]] = []
        for it in items:
            path, sig = it if isinstance(it, tuple) else (it, None)
            if sig is not None and self.cached(path, sig) is not None:
                continue
            todo.append((path, sig))
        if not todo:
            return

        def work(pair):
            path, sig = pair
            sig = sig or _stat_sig(path)
            if sig is None:
                return None
            if self.cached(path, sig) is not None:
                return path, sig, None
            return path, sig, vcf_contigs(path) == self.contigs

        # Concurrently, on the app's shared pool (see fanout): each header read
        # is a few round trips on shared storage and almost no bytes. Every
        # file checked here is remembered as checked, so the matches() calls
        # that follow cost nothing — without that, each one re-stat()ed its
        # file, one after another, undoing most of the point.
        for res in fan_out(work, todo):
            if res:
                path, sig, verdict = res
                if verdict is not None:
                    self._record(path, sig, verdict)
                self._checked.add(self._key(path))

    def save(self) -> None:
        """Persist what this matcher learned. Atomic and best-effort.

        Merged onto the file as it is NOW rather than overwritten with this
        matcher's view, so a Build and an audit running side by side cannot
        erase each other's work.
        """
        if not (self._new and self._cache_path and self.digest):
            return
        tmp = None
        try:
            merged = self._read_cache()
            with self._lock:
                merged.update(self._new)
                self._new = {}
            fd, tmp = tempfile.mkstemp(prefix=CACHE_BASENAME + ".", dir=str(self._cache_path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": _CACHE_VERSION, "reference": self.digest, "files": merged}, fh)
            os.replace(tmp, self._cache_path)
            tmp = None
            try:
                os.chmod(self._cache_path, 0o664)  # share the cache with the project's group
            except OSError:
                pass
        except Exception:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
