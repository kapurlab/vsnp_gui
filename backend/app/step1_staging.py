"""Step 1's two read-handling passes: Grab stages, Run trims.

**Grab** copies download/'s FASTQs into step1/<sample>/, one folder per sample,
dashing an underscored sample prefix on the way (vSNP3 splits the sample name
at the first '_', so Mg_280 and Mg_281 would both collapse to "Mg"). It runs as
a background job rather than inline in the request: staging a few hundred GB is
minutes of work, and a POST held open that long is what the OOD /rnode proxy
cuts. As a job its progress streams to the log the GUI already polls.

**Run** optionally trims those staged reads before the batch aligns them, when
the Step 1 panel's trim box is ticked — it sits with the other per-run options
(Debug, Assemble unmapped, Nanopore, Force re-run) and behaves like them. A
sample whose reads are over the cap is replaced by its first reads and renamed
<sample>-trimN, folder and FASTQs both: vSNP3 takes the sample name from the
FASTQ filename, so that is what carries the mark into the VCF, the SNP table
and the tree label. The untouched originals stay in download/. A sample under
the cap is left exactly as it is.

Trimming keeps the FIRST N reads of a file (what `seqtk head` / `head -n` do),
not a spread-out sample: a head slice needs one pass over only the part of the
file it keeps, and it keeps a pair trivially in sync — record i of R1 and
record i of R2 are the same fragment, so taking the same count from both leaves
every header matched. Library fragments are distributed randomly across the
flowcell, so genome coverage stays even; what a head slice does NOT average
over is per-tile quality, which is an acceptable trade for a depth knob.

Single-file inputs (ONT long reads, single-end Illumina, an unpaired SRA dump)
go through both passes as a group of one — records are whole regardless of
length, so a 100 kb ONT read is never cut in half.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Greedy prefix (.+) binds the read marker to the RIGHTMOST _R1/_R2 (or _1/_2),
# so a sample ID that itself ends in _1/_2 (Mg_2_R1 -> Mg-2) isn't mis-split on
# the first such token. Non-greedy (.+?) would latch onto the first _1/_2 and
# let the lane group swallow the real _R1, collapsing Mg_2 back to "Mg".
_FASTQ_SAMPLE_RE = re.compile(r"(.+)(?:_R?[12])(?:_[^./]+)?\.fastq\.gz$")

# The mark a trimmed sample carries, so an already-staged ERR015582-trim200 can
# be recognised as ERR015582 (anchored at the end: a sample legitimately named
# "…-trim200" mid-string is untouched).
_TRIM_TAG_RE = re.compile(r"-trim\d+$")

# Same split, but keeping the three pieces so R1 and R2 of one fragment library
# can be recognised as members of the same group: prefix + marker + tail, where
# tail is the optional lane suffix (_001) plus the extension.
_READ_MARKER_RE = re.compile(r"^(.+)(_R?[12])((?:_[^./]+)?\.fastq\.gz)$")

# gzip level 1, not the default 9. These are working copies handed straight to
# bwa, and level 1 compresses several times faster for ~8% more bytes — at a
# fixed MB cap that costs ~8% of the reads, which is noise against a knob whose
# whole point is "roughly this much data". Trimming a batch is the slow part of
# a Grab, so the time matters more than the bytes.
_GZIP_LEVEL = 1

# How much uncompressed input to consume between output-size checks. Checking
# every record would cost a tell() per record; checking on a byte budget (rather
# than a record count) keeps the overshoot bounded for both 150 bp Illumina
# reads and 100 kb ONT reads alike.
_MIN_CHECK_BYTES = 64 << 10
_MAX_CHECK_BYTES = 8 << 20


def _size_check_interval(cap_bytes: int, written: int = 0) -> int:
    """Uncompressed bytes to read before looking at the output size again.

    Coarse while there is room left, then tightened to what is still under the
    cap (x2, since the output compresses) so the last check lands close to the
    target rather than a whole interval past it. Without the taper a 3 MB cap
    overshoots by ~10%; at the 200 MB caps this knob is really for, either way
    is a rounding error."""
    room = max(cap_bytes - written, 0) * 2
    return max(_MIN_CHECK_BYTES, min(_MAX_CHECK_BYTES, cap_bytes // 4, room))


def _log(msg: str = "") -> None:
    """Job logs are a file, not a tty, so Python would block-buffer them and the
    GUI's poll would show nothing for minutes. Flush every line."""
    print(msg, flush=True)


def human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= step
    return f"{value:.2f} TB"


def _sanitized_sample_and_name(filename: str) -> Tuple[str, str]:
    """Return (sample, on-disk filename) for a FASTQ, dashing the sample prefix.

    vSNP3 derives the sample name from the FASTQ filename by splitting at the
    FIRST '_'. An underscore *inside* the sample prefix therefore collapses every
    such sample to the shared prefix — Mg_280, Mg_281, … all become "Mg",
    silently merging distinct samples into one VCF in Step 2. Replacing the
    prefix's underscores with '-' (Mg_280 -> Mg-280) keeps each sample distinct
    while leaving the _R1/_R2 read indicator and any _001 lane suffix intact.

    Returns the original name unchanged when the prefix has no underscore.
    """
    m = _FASTQ_SAMPLE_RE.match(filename)
    sample = m.group(1) if m else filename.split(".")[0]
    safe = sample.replace("_", "-")
    if safe == sample:
        return sample, filename
    return safe, safe + filename[len(sample):]


def group_key(filename: str) -> str:
    """Key that R1 and R2 of the same library share (the name minus the read
    marker). A file with no marker at all — an ONT run, a single-end dump — is
    its own key, so it stages as a group of one."""
    m = _READ_MARKER_RE.match(filename)
    if not m:
        return filename
    return m.group(1) + m.group(3)


def read_number(filename: str) -> int:
    """1 or 2 for a marked read file, 0 for an unmarked (single-file) input."""
    m = _READ_MARKER_RE.match(filename)
    if not m:
        return 0
    return int(m.group(2)[-1])


def trimmed_name(filename: str, sample: str, tag: str) -> str:
    """Insert `tag` at the end of the sample prefix: Mg-280_R1.fastq.gz with tag
    '-trim200' becomes Mg-280-trim200_R1.fastq.gz.

    The tag has to land in the SAMPLE prefix, not after the read marker: vSNP3
    names the sample from everything before the first '_', so this is the only
    place a mark survives into the VCF, the tree labels and Step 2. The tag
    itself must stay underscore-free for the same reason."""
    if filename.startswith(sample):
        return sample + tag + filename[len(sample):]
    # Defensive: a name whose sample prefix isn't a literal prefix of the file
    # (shouldn't happen after the dash-rename pass) still gets marked.
    stem, dot, rest = filename.partition(".")
    return f"{stem}{tag}{dot}{rest}" if dot else filename + tag


def read_id(header: bytes) -> str:
    """The fragment identity in a FASTQ header, with the read-of-pair marker
    stripped, so R1's and R2's headers can be compared: '@A00123:1:X 1:N:0:AT'
    and '@A00123:1:X/2' both reduce to 'A00123:1:X'."""
    text = header.decode("utf-8", "replace").strip()
    if text.startswith("@"):
        text = text[1:]
    text = text.split()[0] if text.split() else text
    if len(text) > 2 and text[-2] in "/." and text[-1] in "12":
        text = text[:-2]
    return text


class _FastqReader:
    """One FASTQ record at a time. A record is 4 lines; a truncated tail (a file
    cut off mid-record) reads as end-of-file and is dropped rather than written
    out malformed."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = gzip.open(path, "rb")
        self.records = 0

    def next_record(self) -> Optional[bytes]:
        lines = []
        for _ in range(4):
            line = self._fh.readline()
            if not line:
                return None
            lines.append(line)
        self.records += 1
        return b"".join(lines)

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


class _GzWriter:
    """gzip writer that can report how many bytes have actually hit the disk, so
    the loop can stop at the cap. BufferedWriter.tell() counts its own buffer, so
    the only lag is zlib's pending output — tens of KB against a cap in MB."""

    def __init__(self, path: Path, level: int = _GZIP_LEVEL):
        self.path = path
        self._raw = open(path, "wb")
        self._gz = gzip.GzipFile(fileobj=self._raw, mode="wb", compresslevel=level)

    def write(self, data: bytes) -> None:
        self._gz.write(data)

    def size(self) -> int:
        return self._raw.tell()

    def close(self) -> None:
        try:
            self._gz.close()
        finally:
            self._raw.close()


def trim_reads(
    sources: List[Path],
    targets: List[Path],
    cap_bytes: int,
    progress: Optional[Callable[[int, List[int], int], None]] = None,
) -> int:
    """Write the first N records of each source to its target, stopping as soon
    as any output reaches cap_bytes or any input runs out.

    Every member gets the SAME N, which is what keeps a pair matched: record i of
    R1 and record i of R2 are the same fragment, so identical counts taken from
    the head leave every header paired exactly as it was on disk. Returns N.
    """
    readers = [_FastqReader(p) for p in sources]
    writers: List[_GzWriter] = []
    check_interval = _size_check_interval(cap_bytes)
    kept = 0
    try:
        writers = [_GzWriter(t) for t in targets]
        since_check = 0
        bucket = 0
        while True:
            records = [r.next_record() for r in readers]
            if any(rec is None for rec in records):
                break  # a member ran out — stop everywhere so the counts match
            if kept == 0 and len(records) > 1:
                _warn_on_header_mismatch(records, sources)
            for writer, record in zip(writers, records):
                writer.write(record)
                since_check += len(record)
            kept += 1
            if since_check >= check_interval:
                since_check = 0
                sizes = [w.size() for w in writers]
                biggest = max(sizes)
                if biggest >= cap_bytes:
                    break
                check_interval = _size_check_interval(cap_bytes, biggest)
                step = int(biggest * 10 // cap_bytes)
                if step > bucket:
                    bucket = step
                    if progress:
                        progress(step * 10, sizes, kept)
    finally:
        for reader in readers:
            reader.close()
        for writer in writers:
            writer.close()
    return kept


def _warn_on_header_mismatch(records: List[bytes], sources: List[Path]) -> None:
    ids = [read_id(rec.split(b"\n", 1)[0]) for rec in records]
    if len(set(ids)) == 1:
        return
    _log(
        f"  [WARN] first headers differ between {', '.join(p.name for p in sources)}: "
        f"{' vs '.join(ids)} — these files may already be out of sync. Trimming "
        "keeps the same records from each, but it cannot re-pair them."
    )


def staged_samples(step1_dir: Path) -> Dict[str, str]:
    """Map every sample already in step1/ to the folder holding it, keyed by BOTH
    its folder name and its untagged base name.

    Grab stages what the Inputs pane calls "ready to run" — a sample not yet in
    Step 1. Existence used to be answered by "is this exact file already
    staged?", which was the same question while the staged name always matched
    the download name. Trimming breaks that: ERR015582 staged as
    ERR015582-trim200 is a name no untrimmed target ever matches, so a trimmed
    Grab re-staged (and re-trimmed) every sample in the project instead of the
    one that was actually ready to run. Keying on the base name puts the two
    questions back together.

    A folder counts only if it holds reads, which is the same rule Step 1 uses
    to recognise a sample at all."""
    found: Dict[str, str] = {}
    if not step1_dir.is_dir():
        return found
    for child in sorted(step1_dir.iterdir()):
        if not child.is_dir():
            continue
        if next(child.glob("*.fastq.gz"), None) is None:
            continue
        found.setdefault(child.name, child.name)
        base = _TRIM_TAG_RE.sub("", child.name)
        found.setdefault(base, child.name)
    return found


def _partial(target: Path) -> Path:
    return target.with_name(target.name + ".partial")


def _stage_group(
    index: int,
    total: int,
    sample: str,
    members: List[Path],
    step1_dir: Path,
    already: Dict[str, str],
) -> Dict[str, int]:
    """Stage one sample group (a pair, or a single ONT/single-end file).

    Grab copies; it does not trim. Trimming happens at Run — see
    trim_staged_samples — because that is where the checkbox that asks for it
    lives, alongside the other four per-run options.
    """
    label = f"[{index}/{total}]"
    sizes = [p.stat().st_size for p in members]
    shown = ", ".join(
        f"{p.name} {human_bytes(s)}" for p, s in zip(members, sizes)
    )
    sample_dir = step1_dir / sample
    targets = [sample_dir / p.name for p in members]

    # Already in Step 1 under another name — most often <sample>-trimN, because
    # a trimmed Run renames the folder. The sample is not "ready to run", so
    # leave it alone rather than standing a second copy up beside the first.
    landed = already.get(sample)
    if landed and landed != sample:
        _log(f"{label} {sample} — already in Step 1 as {landed}, leaving it alone")
        return {"skipped": 1}
    if all(t.exists() for t in targets):
        _log(f"{label} {sample} — already staged, nothing to do")
        return {"skipped": 1}

    sample_dir.mkdir(parents=True, exist_ok=True)
    # An earlier run that was stopped mid-write leaves .partial files behind;
    # clear them so they can't accumulate.
    for target in targets:
        part = _partial(target)
        if part.exists():
            part.unlink()

    _log(f"{label} {sample} — {shown} — staging")
    created = 0
    for source, target in zip(members, targets):
        if target.exists():
            continue
        # Real COPY, not a symlink: the step1 sample folder must retain the
        # exact reads used for its alignment even if download/ is later moved
        # or deleted. copy2 follows the source (download/ entries may
        # themselves be symlinks) so we copy the actual bytes. The reads stay
        # in download/ too. Cost: ~doubles read storage.
        #
        # Only files not already staged are copied, and pre-existing symlink
        # entries from before that rule are deliberately NOT rewritten into
        # copies here — that would make the next Grab on a large project
        # re-copy hundreds of GB. Legacy symlinked samples keep their symlink
        # until re-staged; migrate them separately if needed.
        #
        # Staged via .partial so a stop mid-copy can't leave a short file
        # that the next Grab would mistake for a finished one.
        part = _partial(target)
        shutil.copy2(source, part)
        os.replace(part, target)
        created += 1
    _log(f"  [OK] {label} {sample} — {created} file(s) staged")
    return {"created": created}


def _sample_reads(sample_dir: Path) -> List[Path]:
    """The reads vsnp3 would align for this sample, in the order the wrapper
    picks them (R1 before R2, single file on its own)."""
    reads = [r for r in sorted(sample_dir.glob("*.fastq.gz"))
             if not r.name.endswith(".partial")]
    return sorted(reads, key=lambda r: (read_number(r.name), r.name))


def plan_trim(step1_dir: Path, samples: List[str], trim_mb: int) -> List[Dict[str, Any]]:
    """Decide, per selected sample, what a Run-time trim would do to it.

    Kept separate from doing it so the dispatcher can name the samples the run
    will actually produce — the folder is renamed to <sample>-trimN, and the
    provenance record and the results table have to agree with that. Reads
    nothing but sizes and names, so it is cheap to call twice.

    Each entry has: sample, new_sample, action, and (for a trim) sources and
    targets. Actions other than "trim" leave the sample exactly as it is.
    """
    cap = int(trim_mb) * 1024 * 1024 if trim_mb and int(trim_mb) > 0 else 0
    tag = f"-trim{int(trim_mb)}" if cap else ""
    plan: List[Dict[str, Any]] = []
    for name in samples:
        sample_dir = step1_dir / name
        entry: Dict[str, Any] = {"sample": name, "new_sample": name, "action": "keep"}
        reads = _sample_reads(sample_dir) if sample_dir.is_dir() else []
        if not cap:
            entry["action"] = "off"
        elif not reads:
            entry["action"] = "no-reads"
        else:
            existing = _TRIM_TAG_RE.search(name)
            already_at = existing.group(0) if existing else ""
            tagged_files = all(_TRIM_TAG_RE.search(
                _sanitized_sample_and_name(r.name)[0]) for r in reads)
            if already_at == tag:
                entry["action"] = "already"
            elif already_at:
                # Trimming an already-trimmed sample to a different size would
                # cut a cut, and the reads to do it properly from are back in
                # download/. Say so instead of compounding the trim.
                entry["action"] = "other-size"
                entry["trimmed_at"] = already_at.lstrip("-")
            elif tagged_files:
                # Files carry the tag but the folder doesn't — a trim that was
                # stopped between replacing the reads and renaming the folder.
                # Finish the rename; the reads are already the trimmed ones.
                entry["action"] = "rename-only"
                entry["new_sample"] = name + tag
            elif max(r.stat().st_size for r in reads) <= cap:
                entry["action"] = "under"
            elif (step1_dir / (name + tag)).exists():
                entry["action"] = "conflict"
            else:
                entry["action"] = "trim"
                entry["new_sample"] = name + tag
                entry["sources"] = [str(r) for r in reads]
                entry["targets"] = [
                    str(sample_dir / trimmed_name(
                        r.name, _sanitized_sample_and_name(r.name)[0], tag))
                    for r in reads
                ]
        plan.append(entry)
    return plan


def trim_staged_samples(
    step1_dir: Path, samples: List[str], trim_mb: int,
) -> List[str]:
    """Trim the staged reads of each oversized sample, in place, before the
    batch aligns them. Returns the sample folder names the run should iterate.

    The reads are REPLACED, not duplicated: the folder keeps exactly the reads
    its alignment used, which is the property it has always had, and the
    untouched originals stay in download/. The folder and its FASTQs both take
    the -trimN mark, because vSNP3 names the sample from the FASTQ filename —
    that is what carries the mark into the VCF, the SNP table and the tree.
    """
    cap = int(trim_mb) * 1024 * 1024 if trim_mb and int(trim_mb) > 0 else 0
    if not cap:
        return list(samples)

    plan = plan_trim(step1_dir, samples, trim_mb)
    todo = [e for e in plan if e["action"] == "trim"]
    _log(f"== Trimming reads to ~{trim_mb} MB per FASTQ before alignment ==")
    _log(
        f"{len(todo)} of {len(plan)} sample(s) are over the cap. A pair keeps the "
        "same record count on both sides so its headers stay matched; a single "
        "file (ONT long reads) is cut on whole reads. Trimmed samples are "
        f"renamed <sample>-trim{int(trim_mb)}; the untrimmed originals stay in "
        "download/."
    )
    done = 0
    out: List[str] = []
    for entry in plan:
        name, action = entry["sample"], entry["action"]
        if action == "trim":
            done += 1
            try:
                _trim_one(step1_dir, entry, cap, done, len(todo))
            except Exception as exc:
                # The sample keeps its untrimmed reads and its name; the batch
                # still aligns it rather than dropping it over a trim failure.
                _log(f"  [FAILED] {name} — trim failed, aligning it untrimmed: {exc}")
                for target in entry["targets"]:
                    part = _partial(Path(target))
                    if part.exists():
                        part.unlink()
                out.append(name)
                continue
        elif action == "already":
            _log(f"   {name} — already trimmed to {trim_mb} MB")
        elif action == "other-size":
            _log(
                f"   [WARN] {name} — already trimmed to {entry['trimmed_at'].replace('trim', '')} MB. "
                "Trimming it again would cut a cut, so it is left as it is; to "
                "redo it at this size, Remove it and Grab it again."
            )
        elif action == "under":
            _log(f"   {name} — under the cap, left as it is")
        elif action == "conflict":
            _log(
                f"   [WARN] {name} — {entry['sample']}-trim{int(trim_mb)} already "
                "exists; leaving this one untrimmed rather than clobbering it"
            )
        elif action == "rename-only":
            try:
                (step1_dir / name).rename(step1_dir / entry["new_sample"])
                _log(f"   {name} — reads were already trimmed; folder renamed "
                     f"to {entry['new_sample']}")
            except OSError as exc:
                _log(f"  [WARN] {name} — could not rename to "
                     f"{entry['new_sample']}: {exc}")
                out.append(name)
                continue
        elif action == "no-reads":
            _log(f"   [WARN] {name} — no reads found, nothing to trim")
        out.append(entry["new_sample"])
    _log("== Trimming done ==")
    _log("")
    return out


def _trim_one(
    step1_dir: Path, entry: Dict[str, Any], cap: int, index: int, total: int,
) -> None:
    sources = [Path(p) for p in entry["sources"]]
    targets = [Path(p) for p in entry["targets"]]
    kind = "read pairs" if len(sources) > 1 else "reads"
    sizes = ", ".join(f"{p.name} {human_bytes(p.stat().st_size)}" for p in sources)
    _log(f"[{index}/{total}] {entry['sample']} — {sizes} → trimming each to "
         f"~{human_bytes(cap)}")
    started = time.monotonic()

    def progress(percent: int, out_sizes: List[int], kept: int) -> None:
        done = ", ".join(human_bytes(s) for s in out_sizes)
        _log(f"       {percent:3d}%  {done}  ({kept:,} {kind} so far, "
             f"{time.monotonic() - started:.0f}s elapsed)")

    parts = [_partial(t) for t in targets]
    for part in parts:
        if part.exists():
            part.unlink()
    kept = trim_reads(sources, parts, cap, progress)
    # Put each trimmed file in place and drop the untrimmed one it replaces, one
    # at a time, so a kill here can't leave both readable at the top level (the
    # batch picks its R1 with `ls … | head -n1` and would take whichever sorted
    # first). download/ still holds the originals.
    for part, target, source in zip(parts, targets, sources):
        os.replace(part, target)
        if source != target and source.exists():
            source.unlink()
    final = ", ".join(f"{t.name} {human_bytes(t.stat().st_size)}" for t in targets)
    (step1_dir / entry["sample"]).rename(step1_dir / entry["new_sample"])
    _log(f"  [OK] [{index}/{total}] {entry['new_sample']} — kept the first "
         f"{kept:,} {kind} ({final}) in {time.monotonic() - started:.0f}s")


def stage(download_dir: Path, step1_dir: Path) -> Dict[str, object]:
    _log("# Grab — stage ready-to-run FASTQs into Step 1")
    _log(f"# download: {download_dir}")
    _log(f"# step1:    {step1_dir}")
    _log("")

    fastqs = sorted(download_dir.rglob("*.fastq.gz"))
    if not fastqs:
        _log("No FASTQ files found in download/ — nothing to stage.")
        return {"created": 0, "renamed": 0, "skipped": 0, "groups": 0,
                "failed": 0, "message": "No FASTQ files found"}

    # Rename underscored stems in place (Mg_280_R1 -> Mg-280_R1) BEFORE
    # grouping, so vSNP3 never sees an underscore in the sample prefix and both
    # halves of a pair are keyed off the same, already-safe name. download/
    # entries may be symlinks (rename moves the link, not the target) or real
    # files — both are safe to rename. Idempotent: a name already dashed, or a
    # re-run, is a no-op.
    renamed = 0
    resolved: List[Tuple[str, Path]] = []
    for path in fastqs:
        sample, safe_name = _sanitized_sample_and_name(path.name)
        if safe_name != path.name:
            new_path = path.with_name(safe_name)
            if new_path.exists():
                # A dashed file is already present (e.g. the project shipped
                # both Mg_280_R1 and Mg-280_R1). Renaming would clobber it or
                # leave this one orphaned, so skip this underscored duplicate —
                # the dashed file is staged on its own pass.
                _log(
                    f"[WARN] {new_path.name} already exists; skipping the "
                    f"underscored duplicate {path.name}"
                )
                continue
            path.rename(new_path)
            renamed += 1
            path = new_path
        resolved.append((sample, path))

    groups: Dict[Tuple[str, str], Dict[int, Path]] = {}
    for sample, path in resolved:
        key = (sample, group_key(path.name))
        slot = groups.setdefault(key, {})
        number = read_number(path.name)
        if number in slot:
            _log(
                f"[WARN] {path} duplicates {slot[number]} for sample {sample}; "
                "staging the first one only"
            )
            continue
        slot[number] = path

    total = len(groups)
    _log(f"{len(resolved)} FASTQ file(s) in {total} sample group(s)")
    if renamed:
        _log(f"{renamed} file(s) renamed in download/ so vSNP sees one sample per name")
    _log("")

    # Snapshot before staging anything: a sample staged by THIS run must not
    # then look "already in Step 1" to a later group in the same run.
    already = staged_samples(step1_dir)
    counters = {"created": 0, "skipped": 0, "failed": 0}
    for index, ((sample, _key), slot) in enumerate(groups.items(), start=1):
        members = [slot[n] for n in sorted(slot)]
        try:
            result = _stage_group(
                index, total, sample, members, step1_dir, already
            )
        except Exception as exc:  # one bad sample must not sink the batch
            counters["failed"] += 1
            _log(f"  [FAILED] [{index}/{total}] {sample} — {exc}")
            # A sample that failed before writing anything (a truncated .gz is
            # the usual cause) leaves an empty dir behind. Step 1 ignores a dir
            # with no reads in it, but leaving it invites the user to wonder
            # what it is.
            try:
                (step1_dir / sample).rmdir()
            except OSError:
                pass
            continue
        for name, value in result.items():
            counters[name] = counters.get(name, 0) + value

    _log("")
    _log("─" * 60)
    parts = [f"{counters['created']} file(s) staged"]
    if counters["skipped"]:
        parts.append(f"{counters['skipped']} already in Step 1")
    if renamed:
        parts.append(f"{renamed} renamed")
    if counters["failed"]:
        parts.append(f"{counters['failed']} FAILED")
    message = " • ".join(parts)
    _log(f"Grab finished: {message}")

    summary = dict(counters)
    summary.update({"renamed": renamed, "groups": total, "message": message})
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1", required=True, type=Path)
    parser.add_argument("--download", type=Path,
                        help="Grab mode: stage this folder's FASTQs into --step1")
    parser.add_argument("--trim-samples", action="store_true",
                        help="Run mode: trim the staged reads of the samples in "
                             "--samples-file, in place, before the batch aligns "
                             "them, and rewrite that file with the resulting "
                             "folder names")
    parser.add_argument("--samples-file", type=Path, default=None,
                        help="one sample folder name per line")
    parser.add_argument("--trim-mb", type=int, default=0,
                        help="cap each FASTQ at roughly this many MB (0 = no trim)")
    parser.add_argument("--summary", type=Path, default=None,
                        help="Grab mode: write the run's counters here as JSON")
    args = parser.parse_args(argv)

    if args.trim_samples:
        # A trim failure must not take the batch down with it — every sample
        # either trims or stays as it was, and the run aligns it either way.
        if not args.samples_file:
            parser.error("--trim-samples needs --samples-file")
        names = [
            line.strip()
            for line in args.samples_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        try:
            final = trim_staged_samples(args.step1, names, args.trim_mb)
        except Exception as exc:
            _log(f"[FAILED] Trim pass aborted, aligning everything untrimmed: {exc}")
            final = names
        args.samples_file.write_text("\n".join(final) + "\n", encoding="utf-8")
        return 0

    if not args.download:
        parser.error("--download is required unless --trim-samples is given")
    failed = 0
    try:
        summary = stage(args.download, args.step1)
        failed = int(summary.get("failed", 0) or 0)
    except Exception as exc:
        _log(f"[FAILED] Grab aborted: {exc}")
        summary = {"created": 0, "failed": 1, "message": f"Grab failed: {exc}"}
        failed = 1
    if args.summary:
        try:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(json.dumps(summary), encoding="utf-8")
        except OSError as exc:
            _log(f"[WARN] could not write the summary file: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
