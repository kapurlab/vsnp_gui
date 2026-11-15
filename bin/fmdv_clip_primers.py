#!/usr/bin/env python3
"""Clip amplicon primers from ONT reads before vsnp3 sees them.

Adopted from NanoFMDV (Shaw et al. 2025): the primer binding sites are
found on the CHOSEN reference, not fixed in advance, because the tiled
scheme binds differently on every lineage; then samtools ampliconclip
removes the primer-derived bases from each read end. Without this, every
position under a primer reads as the reference in the consensus and in
the vsnp3 VCF whatever the virus carries, and depth there is inflated.

Steps, per sample:
  1. BED of primer sites on the reference: for each primer, the best match
     on either strand by the fraction of IUPAC-compatible bases (the rule
     goprime_comp.py applies), cached once per reference and primer set in
     reference_options/<ref>/primers_<set>.bed. A primer whose best match is
     below --min-fit is left out of the BED and listed in the JSON, instead
     of clipping a random stretch of genome.
  2. minimap2 map-ont to the reference, primary alignments only.
  3. samtools ampliconclip --hard-clip --both-ends --tolerance 50
     --filter-len 50 (NanoFMDV's settings, minus --no-excluded, which
     would drop unmapped reads and with them the mapping rate), back out with
     samtools fastq, gzipped, into <sample dir>/fmdv_clip/.
  4. fmdv_clip.json beside them: reads in and out, primers placed, primers
     below the fit floor, the BED used.

Prints the clipped FASTQ path; the Step 1 batch hands that to vsnp3 as -r1.
Nothing about the reads' coordinates changes, so vsnp3's VCF and the
consensus stay on the reference as before.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T", "U": "T",
    "R": "AG", "Y": "CT", "S": "CG", "W": "AT", "K": "GT", "M": "AC",
    "B": "CGT", "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT",
}
COMP = {"A": "T", "C": "G", "G": "C", "T": "A", "U": "A", "R": "Y", "Y": "R", "S": "S", "W": "W",
        "K": "M", "M": "K", "B": "V", "D": "H", "H": "D", "V": "B", "N": "N"}


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_fasta(path: Path) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    name, seq = None, []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    out.append((name, "".join(seq)))
                name, seq = line[1:].strip(), []
            else:
                seq.append(line.strip())
    if name is not None:
        out.append((name, "".join(seq)))
    return out


def revcomp(s: str) -> str:
    return "".join(COMP.get(c, "N") for c in reversed(s.upper()))


def base_match(ref_base: str, primer_base: str) -> bool:
    return bool(set(IUPAC.get(ref_base, "")) & set(IUPAC.get(primer_base, "")))


def best_site(seq: str, primer: str) -> Tuple[float, int, int, str]:
    """(fit, start 0-based, end exclusive, strand) of the best window for a
    primer on either strand. The same scan goprime_comp.py does: every
    window, fraction of IUPAC-compatible bases, best wins."""
    plen, best = len(primer), (0.0, 0, 0, "?")
    rc = revcomp(primer)
    for i in range(0, len(seq) - plen + 1):
        w = seq[i:i + plen]
        if w.count("N") >= 3:
            continue
        for cand, strand in ((primer, "+"), (rc, "-")):
            m = sum(1 for a, b in zip(w, cand) if base_match(a, b))
            fit = m / plen
            if fit > best[0]:
                best = (fit, i, i + plen, strand)
    return best


def primer_bed(ref_fa: Path, primers_fa: Path, cache_dir: Path, min_fit: float) -> Tuple[Path, Dict]:
    """The BED for this reference and primer set, from the cache or built."""
    tag = hashlib.sha1(primers_fa.read_bytes()).hexdigest()[:8]
    bed = cache_dir / f"primers_{primers_fa.stem}_{tag}.bed"
    meta = bed.with_suffix(".json")
    if bed.is_file() and meta.is_file():
        try:
            return bed, json.loads(meta.read_text())
        except (OSError, ValueError):
            pass
    chrom, seq = read_fasta(ref_fa)[0]
    chrom = chrom.split()[0]
    seq = seq.upper()
    rows, placed, weak = [], [], []
    for name, pseq in read_fasta(primers_fa):
        pname = name.split()[0]
        fit, s, e, strand = best_site(seq, pseq.upper())
        entry = {"primer": pname, "fit": round(fit, 3), "start": s + 1, "end": e, "strand": strand}
        if fit >= min_fit:
            rows.append(f"{chrom}\t{s}\t{e}\t{pname}\t{int(round(fit * 100))}\t{strand}")
            placed.append(entry)
        else:
            weak.append(entry)
    info = {"reference": ref_fa.name, "primer_set": primers_fa.name, "min_fit": min_fit,
            "placed": placed, "below_fit": weak, "bed": str(bed)}
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        bed.write_text("\n".join(rows) + ("\n" if rows else ""))
        meta.write_text(json.dumps(info, indent=1))
    except OSError:
        tmp = Path(".") / bed.name
        tmp.write_text("\n".join(rows) + ("\n" if rows else ""))
        bed = tmp
        info["bed"] = str(bed)
    return bed, info


def count_reads_gz(path: Path) -> int:
    n = 0
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as fh:
        for _ in fh:
            n += 1
    return n // 4


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reads", required=True)
    ap.add_argument("--ref-dir", required=True, help="the vsnp3 reference folder the chooser printed")
    ap.add_argument("--primers", required=True, help="primer FASTA (IUPAC allowed)")
    ap.add_argument("--sample-dir", required=True)
    ap.add_argument("--min-fit", type=float, default=0.8)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()

    reads, ref_dir, primers = Path(a.reads), Path(a.ref_dir), Path(a.primers)
    sample_dir = Path(a.sample_dir).resolve()
    ref_fa = next(iter(ref_dir.glob("*.fasta")), None)
    tools = {t: shutil.which(t) for t in ("minimap2", "samtools")}
    if not ref_fa or not primers.is_file() or not reads.is_file() or not all(tools.values()):
        _err(f"clip: need reads, a reference FASTA in {ref_dir}, {primers}, minimap2 and samtools")
        return 2

    bed, info = primer_bed(ref_fa, primers, ref_dir, a.min_fit)
    out_dir = sample_dir / "fmdv_clip"
    out_dir.mkdir(exist_ok=True)
    sample = sample_dir.name
    bam = out_dir / f"{sample}_top.bam"
    clipped_bam = out_dir / f"{sample}_clip.bam"
    out_fq = out_dir / f"{sample}_clipped.fastq.gz"
    log = out_dir / "clip.log"
    with open(log, "w") as lg:
        p1 = subprocess.Popen([tools["minimap2"], "-t", str(a.threads), "-ax", "map-ont", "--secondary=no",
                               str(ref_fa), str(reads)], stdout=subprocess.PIPE, stderr=lg)
        # Secondary and supplementary alignments out, UNMAPPED reads kept: they
        # pass through ampliconclip untouched and come out again below, so the
        # mapping rate vsnp3 reports (and the QC threshold on it) still means
        # what it did before clipping.
        p2 = subprocess.Popen([tools["samtools"], "view", "-b", "-F", "2304", "-"], stdin=p1.stdout, stdout=subprocess.PIPE, stderr=lg)
        p3 = subprocess.run([tools["samtools"], "sort", "-@", str(a.threads), "-o", str(bam), "-"], stdin=p2.stdout, stderr=lg)
        p1.stdout.close(); p2.stdout.close()
        if p3.returncode != 0:
            _err(f"clip: alignment failed, see {log}"); return 1
        subprocess.run([tools["samtools"], "index", str(bam)], stderr=lg, check=False)
        c1 = subprocess.Popen([tools["samtools"], "ampliconclip", "--hard-clip", "--both-ends", "--tolerance", "50",
                               "--filter-len", "50", "-b", str(bed), str(bam)],
                              stdout=subprocess.PIPE, stderr=lg)
        c2 = subprocess.run([tools["samtools"], "sort", "-@", str(a.threads), "-o", str(clipped_bam), "-"], stdin=c1.stdout, stderr=lg)
        c1.stdout.close()
        if c2.returncode != 0:
            _err(f"clip: ampliconclip failed, see {log}"); return 1
        # samtools writes to a real file descriptor, so gzip in a second
        # process rather than through a Python gzip handle.
        with open(out_fq, "wb") as fq:
            f1 = subprocess.Popen([tools["samtools"], "fastq", str(clipped_bam)], stdout=subprocess.PIPE, stderr=lg)
            f2 = subprocess.run(["gzip", "-1", "-c"], stdin=f1.stdout, stdout=fq, stderr=lg)
            f1.stdout.close()
            f1.wait()
        if f1.returncode != 0 or f2.returncode != 0:
            _err(f"clip: samtools fastq failed, see {log}"); return 1
    for p in (bam, bam.with_suffix(".bam.bai"), clipped_bam):
        try:
            p.unlink()
        except OSError:
            pass
    n_in, n_out = count_reads_gz(reads), count_reads_gz(out_fq)
    result = {"sample": sample, "reads_in": n_in, "reads_out": n_out, "reference": ref_fa.stem,
              "primer_set": primers.name, "primers_placed": len(info["placed"]),
              "primers_below_fit": [w["primer"] for w in info["below_fit"]], "min_fit": a.min_fit,
              "bed": str(bed), "clipped_reads": str(out_fq), "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (sample_dir / "fmdv_clip.json").write_text(json.dumps(result, indent=1))
    _err(f"clip: {sample} {n_in} reads in, {n_out} out (primers clipped, unmapped kept), "
         f"{len(info['placed'])} primers placed, {len(info['below_fit'])} below fit {a.min_fit}")
    print(str(out_fq))
    return 0


if __name__ == "__main__":
    rc = main()
    # The Step 1 batch reads this path with $(...), so the pipe is closed by
    # the time the interpreter flushes stdout at exit. Without this, every
    # sample's log ends in a BrokenPipeError that is not an error.
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    sys.exit(rc)
