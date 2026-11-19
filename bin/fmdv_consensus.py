#!/usr/bin/env python3
"""Build a reference-anchored consensus for one FMDV sample, and cut out VP1.

Reads what vsnp3 step1 already produced in the sample's alignment folder: the
chosen lineage reference FASTA, the zero-coverage VCF, and the deduplicated
BAM. Writes <sample>_consensus.fasta and <sample>_VP1.fasta beside them, plus
fmdv_consensus.json with the numbers.

The consensus keeps the reference's coordinates: substitutions are applied,
indels are not, and anything without enough reads becomes N. That choice is
deliberate.

  - It matches the VCF Step 2 compares, so the tree, the SNP matrix and the
    consensus cannot disagree.
  - It makes VP1 a slice at fixed coordinates rather than a search, and VP1
    is what FMDV epidemiology is reported in (Knowles et al. 2016,
    Rev Sci Tech 35:741-755; topotypes defined on VP1 by Samuel & Knowles
    2001, J Gen Virol 82:609-621).
  - ONT reads carry indel errors that a reference-anchored substitution-only
    consensus simply never inherits.

The cost is that this consensus is NOT a finished genome for GenBank: real
insertions and deletions are missing, so it is right for phylogenetics and
wrong for submission. A submission consensus needs a polisher (NanoFMDV uses
medaka), which is a separate step.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ALT_QUAL = 150        # vsnp3's ALT rule: QUAL > 150
MIN_DEPTH = 10        # below this the base is N (NanoFMDV's published -d 10)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_fasta(path: Path) -> Tuple[str, str]:
    name, seq = "", []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                name = line[1:].split()[0]
            else:
                seq.append(line.strip())
    return name, "".join(seq)


def write_fasta(path: Path, name: str, seq: str, width: int = 70) -> None:
    with open(path, "w") as fh:
        fh.write(f">{name}\n")
        for i in range(0, len(seq), width):
            fh.write(seq[i:i + width] + "\n")


def vcf_calls(vcf: Path, alt_qual: float) -> Tuple[Dict[int, str], List[int], int]:
    """(substitutions by 1-based position, zero-coverage positions, ambiguous count).

    vsnp3's zero-coverage VCF holds only what differs from a plain reference
    run: the SNP calls, and one record per position with no reads (REF=N).
    AC=1 is vsnp3's ambiguous call; it becomes N rather than a base."""
    subs: Dict[int, str] = {}
    zero: List[int] = []
    ambiguous = 0
    with open(vcf) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 8:
                continue
            pos, ref, alt, qual, info = int(f[1]), f[3], f[4], f[5], f[7]
            if ref == "N" and alt == ".":
                zero.append(pos)
                continue
            if alt in (".", "") or len(alt) != 1 or len(ref) != 1:
                continue          # indel or no call: reference coordinates stay
            if "AC=1;" in info + ";":
                subs[pos] = "N"
                ambiguous += 1
                continue
            try:
                q = float(qual)
            except ValueError:
                continue
            if q > alt_qual:
                subs[pos] = alt.upper()
            else:
                # An ALT vsnp3 recorded but did not trust (low depth keeps
                # QUAL under 150 even when every read agrees). Printing the
                # reference here would claim a base nobody observed; N says
                # "a change was seen, not proven".
                subs[pos] = "N"
                ambiguous += 1
    return subs, zero, ambiguous


def low_depth(samtools: str, bam: Path, ref: Path, length: int, min_depth: int) -> List[int]:
    """1-based positions covered by fewer than min_depth reads, from the BAM.

    `samtools depth -a` so uncovered positions are reported as 0 rather than
    left out. The VCF's zero-coverage records are a subset of this."""
    p = subprocess.run([samtools, "depth", "-a", "-J", "--reference", str(ref), str(bam)],
                       capture_output=True, text=True, check=True)
    seen = set()
    out: List[int] = []
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) < 3:
            continue
        pos, dp = int(f[1]), int(f[2])
        seen.add(pos)
        if dp < min_depth:
            out.append(pos)
    out.extend(p for p in range(1, length + 1) if p not in seen)
    return sorted(set(out))


def pileup_majority(samtools: str, bam: Path, ref: Path, min_depth: int, min_frac: float) -> Dict[int, Tuple[str, int, int]]:
    """Positions whose read majority is a single base at >= min_frac of
    depth >= min_depth: {pos: (base, count, depth)}. One mpileup pass over
    the genome. Used to find substitutions vsnp3's caller left out of the
    VCF (seen on the Pirbright samples: a 69% A at depth 2,900 with no
    record), which the consensus then reports and applies."""
    p = subprocess.run([samtools, "mpileup", "-aa", "-d", "0", "-q", "0", "-Q", "0", "-f", str(ref), str(bam)],
                       capture_output=True, text=True)
    out: Dict[int, Tuple[str, int, int]] = {}
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) < 5:
            continue
        pos, refb, bases = int(f[1]), f[2].upper(), f[4]
        # Strip indel runs and read start/end marks, then count bases.
        i, counts = 0, {"A": 0, "C": 0, "G": 0, "T": 0}
        n = len(bases)
        while i < n:
            c = bases[i]
            if c in "+-":
                j = i + 1
                while j < n and bases[j].isdigit():
                    j += 1
                i = j + int(bases[i + 1:j] or 0)
                continue
            if c == "^":
                i += 2
                continue
            if c in ".,":
                if refb in counts:
                    counts[refb] += 1
            elif c.upper() in counts:
                counts[c.upper()] += 1
            i += 1
        depth = sum(counts.values())
        if depth < min_depth:
            continue
        base, cnt = max(counts.items(), key=lambda kv: kv[1])
        if cnt / depth >= min_frac:
            out[pos] = (base, cnt, depth)
    return out


def vp1_coords(lib: Path, ref_safe: str, ref_fa: Path, aln_dir: Path) -> Optional[Dict]:
    """VP1 as a 1-based closed interval on the reference, found once per
    reference by mapping its record from lib/panels/vp1.fasta (same safe
    name) onto the reference genome with minimap2, and cached in
    reference_options/<ref>/VP1_coords.json (or beside the alignment when
    that folder is not writable)."""
    import shutil
    caches = [lib / "reference_options" / ref_safe / "VP1_coords.json", aln_dir / "VP1_coords.json"]
    for c in caches:
        if c.is_file():
            try:
                return json.loads(c.read_text())
            except (OSError, ValueError):
                pass
    panel = lib / "panels" / "vp1.fasta"
    minimap2 = shutil.which("minimap2")
    if not (panel.is_file() and minimap2):
        _err(f"consensus: VP1 skipped ({panel} or minimap2 missing)")
        return None
    record = None
    name, seq = "", []
    with open(panel) as fh:
        for line in fh:
            if line.startswith(">"):
                if name == ref_safe:
                    break
                name, seq = line[1:].split()[0], []
            else:
                seq.append(line.strip())
    if name == ref_safe and seq:
        record = "".join(seq)
    if record is None:
        _err(f"consensus: VP1 skipped (no {ref_safe} in {panel})")
        return None
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".fasta", delete=False) as tmp:
        tmp.write(f">{ref_safe}_VP1\n{record}\n")
        tmp_path = tmp.name
    try:
        p = subprocess.run([minimap2, "-c", "--secondary=no", str(ref_fa), tmp_path],
                           capture_output=True, text=True)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    hits = [ln.split("\t") for ln in p.stdout.splitlines() if ln.strip()]
    if not hits:
        _err(f"consensus: VP1 skipped ({ref_safe} VP1 did not map onto its own genome)")
        return None
    best = max(hits, key=lambda f: int(f[9]))
    coords = {"reference": ref_safe, "start": int(best[7]) + 1, "end": int(best[8]),
              "panel_length": len(record), "matches": int(best[9]), "block": int(best[10]),
              "source": str(panel)}
    for c in caches:
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            c.write_text(json.dumps(coords, indent=1))
            break
        except OSError:
            continue
    return coords


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample-dir", required=True, help="step1/<sample>")
    ap.add_argument("--lib", help="FMDV reference library, for the VP1 panel")
    ap.add_argument("--min-depth", type=int, default=MIN_DEPTH)
    ap.add_argument("--alt-qual", type=float, default=ALT_QUAL)
    ap.add_argument("--pileup-min-depth", type=int, default=20)
    ap.add_argument("--pileup-min-frac", type=float, default=0.75)
    ap.add_argument("--no-pileup-check", action="store_true")
    a = ap.parse_args()

    sample_dir = Path(a.sample_dir).resolve()
    sample = sample_dir.name
    aln = next((d for d in sorted(sample_dir.glob("alignment_*")) if d.is_dir()), None)
    if aln is None:
        _err(f"consensus: no alignment_* folder in {sample_dir}"); return 2
    ref_fa = next(iter(aln.glob("*.fasta")), None)
    vcf = next(iter(aln.glob("*_zc.vcf")), None)
    bam = next((b for b in aln.glob("*.bam") if "unmapped" not in b.name), None)
    if not (ref_fa and vcf and bam):
        _err(f"consensus: need a FASTA, a *_zc.vcf and a BAM in {aln}"); return 2

    import shutil
    samtools = shutil.which("samtools")
    if not samtools:
        _err("consensus: samtools not on PATH (it is in the vsnp3 env)"); return 2

    chrom, ref_seq = read_fasta(ref_fa)
    bases = list(ref_seq.upper())
    subs, zero, ambiguous = vcf_calls(vcf, a.alt_qual)
    masked = low_depth(samtools, bam, ref_fa, len(bases), a.min_depth)

    for pos, b in subs.items():
        if 1 <= pos <= len(bases):
            bases[pos - 1] = b
    for pos in set(masked) | set(zero):
        if 1 <= pos <= len(bases):
            bases[pos - 1] = "N"
    # Pileup check: where the reads clearly say one base and the consensus
    # so far says another (a substitution the VCF did not carry, or a
    # reference base kept for want of a record), take the reads' base and
    # record the position, so the VCF's misses are visible, not silent.
    overrides: List[Dict] = []
    if not a.no_pileup_check:
        # Plurality at 50% or more that contradicts the consensus base: at
        # >= pileup_min_frac the reads' base replaces it; below that the
        # position becomes N, because a base the reads contradict half the
        # time is not a call either way.
        for pos, (b, cnt, depth) in pileup_majority(samtools, bam, ref_fa, a.pileup_min_depth, 0.5).items():
            if 1 <= pos <= len(bases) and bases[pos - 1] != "N" and bases[pos - 1] != b:
                new = b if cnt / depth >= a.pileup_min_frac else "N"
                overrides.append({"pos": pos, "from": bases[pos - 1], "to": new, "count": cnt, "depth": depth,
                                  "in_vcf": pos in subs})
                bases[pos - 1] = new
    cons = "".join(bases)

    out_fa = aln.parent / f"{sample}_consensus.fasta"
    write_fasta(out_fa, f"{sample} ref={aln.name.replace('alignment_', '')}", cons)
    n_count = cons.count("N")
    vp1: Dict = {}
    if a.lib:
        coords = vp1_coords(Path(a.lib).expanduser(), aln.name.replace("alignment_", ""), ref_fa, aln)
        if coords:
            seg = cons[coords["start"] - 1:coords["end"]]
            vp1_fa = aln.parent / f"{sample}_VP1.fasta"
            write_fasta(vp1_fa, f"{sample} VP1 {chrom}:{coords['start']}-{coords['end']}", seg)
            vp1 = {"vp1_start": coords["start"], "vp1_end": coords["end"], "vp1_length": len(seg),
                   "vp1_called_percent": round(100.0 * (len(seg) - seg.count("N")) / len(seg), 2) if seg else 0.0,
                   "vp1_fasta": str(vp1_fa)}
    result = {
        "sample": sample,
        "reference": aln.name.replace("alignment_", ""),
        "chrom": chrom,
        "method": "reference-anchored: vsnp3 substitutions, N below min_depth, no indels",
        "min_depth": a.min_depth,
        "alt_qual": a.alt_qual,
        "length": len(cons),
        "n_count": n_count,
        "called_percent": round(100.0 * (len(cons) - n_count) / len(cons), 2) if cons else 0.0,
        "substitutions": len([v for v in subs.values() if v != "N"]),
        "ambiguous_to_n": ambiguous,
        "pileup_overrides": overrides,
        "consensus": str(out_fa),
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **vp1,
    }
    (sample_dir / "fmdv_consensus.json").write_text(json.dumps(result, indent=1))
    _err(f"consensus: {sample} {len(cons)} nt, {result['called_percent']}% called, "
         f"{result['substitutions']} substitutions, {ambiguous} ambiguous to N, {len(overrides)} pileup overrides"
         + (f", VP1 {vp1['vp1_length']} nt {vp1['vp1_called_percent']}% called" if vp1 else ""))
    print(str(out_fa))
    return 0


if __name__ == "__main__":
    sys.exit(main())
