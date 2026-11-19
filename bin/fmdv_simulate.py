#!/usr/bin/env python3
"""SYN synthetic MinKNOW FMDV amplicon run, with an answer key.

Simulated reads for demonstrating and benchmarking FMDV mode. Not a real
outbreak, and not a real sample. Every file name this script writes, and
every FASTQ header, contains SYN.

The set is fixed by --seed. --scenario selects a subset. Founders are real
complete genomes. The distant founder is not assumed: each run measures its
aligned-base identity to every genome in the 79-genome panel and stops if
the nearest is outside 15 to 20 percent.

Amplicons are cut with NanoFMDV's L primer scheme. Primer sites are the best
window on either strand by the fraction of IUPAC-compatible bases, the same
rule as bin/fmdv_clip_primers.py, and a primer under --min-fit (0.8) is left
out. The oligo sequence is kept on the amplicon ends. IUPAC is resolved to
the founder base where the two are compatible.

Reads are simulated with Badread's nanopore2023 error model (its R10.4.1
model). Adapters, junk, random reads, chimeras and glitches are off, so the
key stays interpretable. That also means this set cannot test adapter
trimming or junk filtering. See docs/FMDV_MODE.md.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T", "U": "T",
    "R": "AG", "Y": "CT", "S": "CG", "W": "AT", "K": "GT", "M": "AC",
    "B": "CGT", "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT",
}
COMP = {
    "A": "T", "C": "G", "G": "C", "T": "A", "U": "A", "R": "Y", "Y": "R",
    "S": "S", "W": "W", "K": "M", "M": "K", "B": "V", "D": "H", "H": "D",
    "V": "B", "N": "N",
}

# Measured once, then re-checked on every run. Not a guess.
DISTANT_ACCESSION = "DQ989316"
DISTANT_NEAREST = "ASIA1-NA_MF372126"
MAIN_NAME = "SAT1-I_KM268899"
OTHER_NAME = "O-EA-2_MH053318"
VP1_START_1 = 3178  # inclusive, on SAT1-I_KM268899
VP1_END_1 = 3834    # inclusive
CHUNK_READS = 4000
FMDV_READ_FRACTION = 0.35  # of all reads; bovine is the rest. Inside 0.20 to 0.50.
SCENARIO_DEPTH = 200


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
    """(fit, start 0-based, end exclusive, strand). Same scan as the clipper."""
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


def accession_of(header: str) -> str:
    tok = header.split()[0]
    last = tok.split("-")[-1].split("|")[0]
    return re.sub(r"\.\d+$", "", last)


def pool_of(primer_name: str) -> str:
    m = re.match(r"^(S|L\d+[a-z]?)", primer_name)
    return m.group(1) if m else primer_name.split("-")[0]


def load_named(panel_fa: Path) -> Dict[str, str]:
    out = {}
    for header, seq in read_fasta(panel_fa):
        out[header.split()[0]] = seq.upper().replace("U", "T")
    return out


def load_by_accession(path: Path, wanted: str) -> Tuple[str, str]:
    want = wanted.split(".")[0]
    for header, seq in read_fasta(path):
        if accession_of(header) == want:
            return header, seq.upper().replace("U", "T")
    raise SystemExit(f"accession {wanted} not found in {path}")


def aligned_identity(a: str, b: str) -> Dict:
    """Aligned-base identity ignores terminal gaps, so a short record is not
    scored as distant just because its ends are missing. Global identity is
    reported beside it and is not the selection rule."""
    import edlib
    r = edlib.align(a, b, mode="NW", task="path")
    ops = [(int(n), op) for n, op in re.findall(r"(\d+)(=|X|I|D)", r["cigar"])]
    while ops and ops[0][1] in "ID":
        ops.pop(0)
    while ops and ops[-1][1] in "ID":
        ops.pop()
    matches = mism = indels = 0
    for n, op in ops:
        if op == "=":
            matches += n
        elif op == "X":
            mism += n
        else:
            indels += n
    aln = matches + mism + indels
    return {
        "aligned_identity": round(matches / aln, 4) if aln else 0.0,
        "aligned_divergence": round(1 - (matches / aln if aln else 0), 4),
        "global_identity": round(1 - r["editDistance"] / max(len(a), len(b)), 4),
        "edit_distance": r["editDistance"],
        "matches": matches,
        "mismatches": mism,
        "internal_indels": indels,
        "aligned_bases": aln,
    }


def nearest_panel(seq: str, panel: Dict[str, str]) -> Tuple[str, Dict]:
    best_name, best = None, None
    for name, pseq in panel.items():
        st = aligned_identity(seq, pseq)
        if best is None or st["aligned_divergence"] < best["aligned_divergence"]:
            best_name, best = name, st
    return best_name, best


def place_primers(seq: str, primers: List[Tuple[str, str]], min_fit: float) -> Tuple[List[Dict], List[Dict]]:
    placed, weak = [], []
    for name, pseq in primers:
        pname = name.split()[0]
        fit, s, e, strand = best_site(seq, pseq.upper())
        entry = {
            "primer": pname, "pool": pool_of(pname), "fit": round(fit, 3),
            "start": s, "end": e, "strand": strand, "seq": pseq.upper(),
        }
        (placed if fit >= min_fit else weak).append(entry)
    return placed, weak


def cluster_sites(primers: List[Dict], window: int = 200) -> List[Dict]:
    """One site per pool and strand. Alternative oligos within `window` bp
    collapse to the best fit, so a tile is not sequenced once per oligo."""
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for p in primers:
        groups.setdefault((p["pool"], p["strand"]), []).append(p)
    sites = []
    for (pool, strand), items in groups.items():
        items = sorted(items, key=lambda x: x["start"])
        cur: List[Dict] = []
        for it in items:
            if not cur or it["start"] - cur[-1]["start"] <= window:
                cur.append(it)
            else:
                sites.append(max(cur, key=lambda x: x["fit"]))
                cur = [it]
        if cur:
            sites.append(max(cur, key=lambda x: x["fit"]))
    return sites


def resolve_oligo(window: str, primer: str) -> str:
    """IUPAC resolved to the founder base where compatible, else the first
    concrete base in the primer's IUPAC set."""
    out = []
    for g, p in zip(window, primer):
        if base_match(g, p):
            out.append(g if g in "ACGT" else IUPAC.get(p, "N")[0])
        else:
            out.append(IUPAC.get(p, "N")[0])
    return "".join(out)


def cut_amplicons(seq: str, placed: List[Dict]) -> List[Dict]:
    sites = cluster_sites(placed)
    forwards = [s for s in sites if s["strand"] == "+"]
    reverses = [s for s in sites if s["strand"] == "-"]
    amps = []
    for fwd in forwards:
        for rev in reverses:
            if fwd["pool"] != rev["pool"]:
                continue
            if rev["start"] < fwd["end"]:
                continue
            length = rev["end"] - fwd["start"]
            if not 150 <= length <= 4500:
                continue
            oligo_f = resolve_oligo(seq[fwd["start"]:fwd["end"]], fwd["seq"])
            oligo_r = resolve_oligo(revcomp(seq[rev["start"]:rev["end"]]), rev["seq"])
            top_r = revcomp(oligo_r)
            mid = seq[fwd["end"]:rev["start"]]
            amp_seq = oligo_f + mid + top_r
            amps.append({
                "pool": fwd["pool"],
                "forward": fwd["primer"],
                "reverse": rev["primer"],
                "start": fwd["start"],
                "end": rev["end"],
                "length": len(amp_seq),
                "seq": amp_seq,
                "forward_fit": fwd["fit"],
                "reverse_fit": rev["fit"],
            })
    amps.sort(key=lambda a: (a["start"], a["end"]))
    return amps


def covered_intervals(amps: Sequence[Dict], genome_len: int) -> Tuple[List[List[int]], List[List[int]]]:
    marks = [False] * genome_len
    for a in amps:
        for i in range(a["start"], min(genome_len, a["end"])):
            marks[i] = True
    def runs(flag: bool) -> List[List[int]]:
        out, i = [], 0
        while i < genome_len:
            if marks[i] != flag:
                i += 1
                continue
            j = i
            while j < genome_len and marks[j] == flag:
                j += 1
            out.append([i + 1, j])  # 1-based inclusive
            i = j
        return out
    return runs(True), runs(False)


def safe_positions(genome_len: int, placed: List[Dict], margin: int = 15) -> List[int]:
    blocked = [False] * genome_len
    for p in placed:
        for i in range(max(0, p["start"] - margin), min(genome_len, p["end"] + margin)):
            blocked[i] = True
    return [i for i in range(genome_len) if not blocked[i]]


def different_base(rng: random.Random, base: str) -> str:
    choices = [b for b in "ACGT" if b != base]
    return rng.choice(choices)


def build_tree(founder: str, placed: List[Dict], seed: int, covered: Optional[List[int]] = None) -> Dict:
    """A short chain. Each edge is 3 substitutions, inside the 2 to 10 range.
    One pair is identical. One tip carries a 3-nt deletion in an insert.
    Changes are restricted to positions an amplicon actually covers, and to
    the insert, so clipping does not remove them and a 3' scheme gap cannot
    hide a SNP."""
    rng = random.Random(seed)
    # 80 bp clears samtools ampliconclip --tolerance 50, so a planted
    # substitution is not removed with the primer.
    safe = safe_positions(len(founder), placed, margin=80)
    if covered is not None:
        covered_set = set(covered)
        safe = [p for p in safe if p in covered_set]
    vp1 = [p for p in safe if (VP1_START_1 - 1) <= p <= (VP1_END_1 - 1)]
    # Deletion in the L1a/L1b overlap, clear of primers, so two amplicons carry it.
    del_pool = [p for p in safe if 800 <= p <= 1300 and p + 2 < len(founder)
                and all(q in safe for q in (p, p + 1, p + 2))]
    if len(vp1) < 3 or len(del_pool) < 1:
        raise SystemExit("not enough safe positions for the transmission tree")
    deletion = rng.choice(del_pool)
    forbidden = {deletion, deletion + 1, deletion + 2}
    edges = []
    for n_snps, label in ((3, "SYN-02"), (3, "SYN-04"), (3, "SYN-05")):
        vp1_pool = [p for p in vp1 if p not in forbidden]
        other_pool = [p for p in safe if p not in forbidden and p not in vp1]
        # keep SNPs at least 5 bp apart
        picked = []
        rng.shuffle(vp1_pool)
        rng.shuffle(other_pool)
        for p in vp1_pool:
            if all(abs(p - q) >= 5 for q in picked):
                picked.append(p)
                break
        for p in other_pool:
            if len(picked) >= n_snps:
                break
            if all(abs(p - q) >= 5 for q in picked):
                picked.append(p)
        if len(picked) < n_snps:
            raise SystemExit(f"could not place {n_snps} SNPs for {label}")
        snps = [{"pos0": p, "pos1": p + 1, "ref": founder[p], "alt": different_base(rng, founder[p])}
                for p in sorted(picked)]
        forbidden.update(picked)
        edges.append({"to": label, "snps": snps})
    # Apply along the chain. SYN-03 copies SYN-02.
    genomes = {"SYN-01": founder}
    cur = founder
    parent = {"SYN-02": "SYN-01", "SYN-03": "SYN-02", "SYN-04": "SYN-02", "SYN-05": "SYN-04"}
    changes = {"SYN-01": [], "SYN-03": []}
    for edge in edges:
        b = list(cur)
        for snp in edge["snps"]:
            b[snp["pos0"]] = snp["alt"]
        cur = "".join(b)
        if edge["to"] == "SYN-05":
            cur = cur[:deletion] + cur[deletion + 3:]
        genomes[edge["to"]] = cur
        changes[edge["to"]] = edge["snps"]
    genomes["SYN-03"] = genomes["SYN-02"]
    # founder-coordinate cumulative SNPs per sample
    cumulative = {
        "SYN-01": [],
        "SYN-02": edges[0]["snps"],
        "SYN-03": edges[0]["snps"],
        "SYN-04": edges[0]["snps"] + edges[1]["snps"],
        "SYN-05": edges[0]["snps"] + edges[1]["snps"] + edges[2]["snps"],
    }
    return {
        "genomes": genomes,
        "parent": parent,
        "snps_from_parent": changes,
        "cumulative_snps": cumulative,
        "deletion": {
            "sample": "SYN-05",
            "pos1": deletion + 1,
            "length": 3,
            "bases": founder[deletion:deletion + 3],
            "note": "3 nucleotides removed from an insert, not under a primer. "
                    "The consensus caller is substitution-only, so this deletion "
                    "is expected to be absent from the consensus.",
        },
        "identical_pair": ["SYN-02", "SYN-03"],
        "edges_substitutions": {
            "SYN-01->SYN-02": 3,
            "SYN-02->SYN-03": 0,
            "SYN-02->SYN-04": 3,
            "SYN-04->SYN-05": 3,
        },
    }


def amps_public(amps: List[Dict]) -> List[Dict]:
    return [{k: a[k] for k in ("pool", "forward", "reverse", "start", "end", "length",
                                "forward_fit", "reverse_fit")}
            for a in amps]


def write_fasta(path: Path, name: str, seq: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f">{name}\n")
        for i in range(0, len(seq), 80):
            fh.write(seq[i:i + 80] + "\n")


def parse_fastq(text: str) -> List[Tuple[str, str, str]]:
    lines = text.splitlines()
    recs = []
    i = 0
    while i + 3 < len(lines):
        if not lines[i].startswith("@"):
            i += 1
            continue
        recs.append((lines[i][1:], lines[i + 1], lines[i + 3]))
        i += 4
    return recs


def run_badread(ref_fa: Path, depth: int, seed: int, length: str) -> List[Tuple[str, str, str]]:
    cmd = [
        sys.executable, "-m", "badread", "simulate",
        "--reference", str(ref_fa),
        "--quantity", f"{depth}x",
        "--length", length,
        "--identity", "98,99.5,1.5",
        "--error_model", "nanopore2023",
        "--qscore_model", "nanopore2023",
        "--seed", str(seed),
        "--start_adapter", "0,0",
        "--end_adapter", "0,0",
        "--junk_reads", "0",
        "--random_reads", "0",
        "--chimeras", "0",
        "--glitches", "0,0,0",
    ]
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["TERM"] = "dumb"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    if proc.returncode != 0:
        _err(proc.stderr[-2000:])
        raise SystemExit(f"badread failed on {ref_fa} (exit {proc.returncode})")
    return parse_fastq(proc.stdout)


def retag(recs: List[Tuple[str, str, str]], sample_id: str, barcode: str, scenario: str,
          origin: str) -> List[Tuple[str, str, str]]:
    out = []
    for i, (header, seq, qual) in enumerate(recs, start=1):
        # SYN is in the header. The rest keeps the Badread audit fields.
        h = (f"SYN|{barcode}|{sample_id}|read{i:06d}|scenario={scenario}|origin={origin} "
             f"synthetic_not_a_real_sample {header}")
        out.append((h, seq, qual))
    return out


def write_chunks(barcode_dir: Path, barcode: str, recs: List[Tuple[str, str, str]]) -> List[str]:
    barcode_dir.mkdir(parents=True, exist_ok=True)
    names = []
    if not recs:
        return names
    for ci, start in enumerate(range(0, len(recs), CHUNK_READS)):
        chunk = recs[start:start + CHUNK_READS]
        name = f"SYN_{barcode}_chunk{ci:03d}.fastq.gz"
        path = barcode_dir / name
        with gzip.open(path, "wt") as fh:
            for header, seq, qual in chunk:
                fh.write(f"@{header}\n{seq}\n+\n{qual}\n")
        names.append(name)
    return names


def simulate_amplicons(amps: List[Dict], depth: int, seed: int, work: Path, tag: str) -> List[Tuple[str, str, str]]:
    recs = []
    work.mkdir(parents=True, exist_ok=True)
    for i, amp in enumerate(amps):
        fa = work / f"SYN_{tag}_{amp['pool']}_{i}.fasta"
        write_fasta(fa, f"SYN_{tag}_{amp['pool']}", amp["seq"])
        # Fragment length above the amplicon, so Badread returns the whole
        # linear amplicon (its rule when fragment length >= sequence length).
        got = run_badread(fa, depth, seed + i + 1, "30000,1000")
        _err(f"  {tag} {amp['pool']} {amp['start']+1}-{amp['end']} depth {depth}x -> {len(got)} reads")
        recs.extend(got)
        fa.unlink(missing_ok=True)
    return recs


def simulate_host(host_fa: Path, n_reads: int, seed: int, work: Path) -> List[Tuple[str, str, str]]:
    """Host reads from a bovine chromosome segment. Quantity is bases, sized
    so the read count lands near n_reads at the 1,500 bp fragment length."""
    if n_reads <= 0:
        return []
    bases = max(1500, n_reads * 1500)
    cmd = [
        sys.executable, "-m", "badread", "simulate",
        "--reference", str(host_fa),
        "--quantity", str(bases),
        "--length", "1500,200",
        "--identity", "98,99.5,1.5",
        "--error_model", "nanopore2023",
        "--qscore_model", "nanopore2023",
        "--seed", str(seed),
        "--start_adapter", "0,0",
        "--end_adapter", "0,0",
        "--junk_reads", "0",
        "--random_reads", "0",
        "--chimeras", "0",
        "--glitches", "0,0,0",
    ]
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["TERM"] = "dumb"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    if proc.returncode != 0:
        _err(proc.stderr[-2000:])
        raise SystemExit("badread failed on the bovine segment")
    recs = parse_fastq(proc.stdout)
    rng = random.Random(seed)
    rng.shuffle(recs)
    if len(recs) < n_reads:
        _err(f"  host: asked {n_reads}, badread returned {len(recs)}; keeping all")
        return recs
    return recs[:n_reads]


def sample_plan(depths: List[int]) -> List[Dict]:
    """Fixed barcodes. Sample ids carry no lineage name."""
    plan = [
        {"barcode": "barcode01", "sample_id": "SYN-01", "scenario": "clean",
         "genome": "SYN-01", "depth": SCENARIO_DEPTH, "mix": None, "dropout": False},
        {"barcode": "barcode02", "sample_id": "SYN-02", "scenario": "clean",
         "genome": "SYN-02", "depth": SCENARIO_DEPTH, "mix": None, "dropout": False},
        {"barcode": "barcode03", "sample_id": "SYN-03", "scenario": "clean",
         "genome": "SYN-03", "depth": SCENARIO_DEPTH, "mix": None, "dropout": False},
        {"barcode": "barcode04", "sample_id": "SYN-04", "scenario": "clean",
         "genome": "SYN-04", "depth": SCENARIO_DEPTH, "mix": None, "dropout": False},
        {"barcode": "barcode05", "sample_id": "SYN-05", "scenario": "clean",
         "genome": "SYN-05", "depth": SCENARIO_DEPTH, "mix": None, "dropout": False},
        {"barcode": "barcode06", "sample_id": "SYN-06", "scenario": "crosstalk",
         "genome": "SYN-01", "depth": SCENARIO_DEPTH, "mix": 0.03, "dropout": False},
        {"barcode": "barcode07", "sample_id": "SYN-07", "scenario": "coinfection",
         "genome": "SYN-01", "depth": SCENARIO_DEPTH, "mix": 0.30, "dropout": False},
        {"barcode": "barcode08", "sample_id": "SYN-08", "scenario": "dropout",
         "genome": "SYN-01", "depth": SCENARIO_DEPTH, "mix": None, "dropout": True},
        {"barcode": "barcode09", "sample_id": "SYN-09", "scenario": "distant",
         "genome": "distant", "depth": SCENARIO_DEPTH, "mix": None, "dropout": False},
    ]
    extra = []
    n = 12
    for depth in depths:
        if depth == SCENARIO_DEPTH:
            continue
        extra.append({
            "barcode": f"barcode{n:02d}", "sample_id": f"SYN-{n:02d}",
            "scenario": "clean-depth", "genome": "SYN-01", "depth": depth,
            "mix": None, "dropout": False,
        })
        n += 1
    # Keep 20x then 2000x in barcode order if both were requested out of order.
    extra.sort(key=lambda s: s["depth"])
    # Re-number extras so barcodes stay contiguous and stable for a given depth set.
    for i, s in enumerate(extra, start=12):
        s["barcode"] = f"barcode{i:02d}"
        s["sample_id"] = f"SYN-{i:02d}"
    return plan + extra


def wanted(plan: List[Dict], scenario: str) -> List[Dict]:
    if scenario == "all":
        return plan
    if scenario == "clean":
        return [s for s in plan if s["scenario"] in ("clean", "clean-depth")]
    return [s for s in plan if s["scenario"] == scenario or
            (scenario == "depths" and s["scenario"] == "clean-depth")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", required=True, type=Path, help="79-genome panel FASTA")
    ap.add_argument("--primers", required=True, type=Path, help="NanoFMDV L primer FASTA")
    ap.add_argument("--collection", type=Path, default=None,
                    help="complete-genome FASTA to take the distant founder from")
    ap.add_argument("--distant-fasta", type=Path, default=None,
                    help="single-sequence FASTA of the distant founder, if the collection is not local")
    ap.add_argument("--distant-accession", default=DISTANT_ACCESSION)
    ap.add_argument("--host-fasta", required=True, type=Path,
                    help="bovine chromosome segment used as the read background")
    ap.add_argument("--out", required=True, type=Path, help="MinKNOW run folder to write")
    ap.add_argument("--key-dir", type=Path, default=None, help="answer key directory (default: beside --out)")
    ap.add_argument("--seed", type=int, default=20261115)
    ap.add_argument("--scenario", default="all",
                    choices=["all", "clean", "crosstalk", "coinfection", "dropout", "distant", "depths"])
    ap.add_argument("--depths", default="20,200,2000",
                    help="clean-founder depths to emit, comma-separated. Other scenarios stay at 200x")
    ap.add_argument("--min-fit", type=float, default=0.8)
    ap.add_argument("--dry-run", action="store_true", help="write the answer key only, no reads")
    ap.add_argument("--work", type=Path, default=None, help="scratch directory for per-amplicon FASTA")
    a = ap.parse_args()

    depths = [int(x) for x in a.depths.split(",") if x.strip()]
    if any(d <= 0 for d in depths):
        raise SystemExit("--depths must be positive")

    panel = load_named(a.panel)
    if MAIN_NAME not in panel or OTHER_NAME not in panel:
        raise SystemExit(f"panel is missing {MAIN_NAME} or {OTHER_NAME}")
    founder = panel[MAIN_NAME]
    other = panel[OTHER_NAME]
    if a.collection:
        dheader, distant = load_by_accession(a.collection, a.distant_accession)
    elif a.distant_fasta:
        dheader, distant = read_fasta(a.distant_fasta)[0]
        distant = distant.upper()
    else:
        raise SystemExit("need --collection or --distant-fasta")

    _err(f"measuring {a.distant_accession} against {len(panel)} panel genomes")
    near_name, near_stats = nearest_panel(distant, panel)
    div = near_stats["aligned_divergence"]
    _err(f"  nearest {near_name} aligned divergence {div:.4f} "
         f"aligned identity {near_stats['aligned_identity']:.4f} "
         f"global identity {near_stats['global_identity']:.4f}")
    if not 0.15 <= div <= 0.20:
        raise SystemExit(
            f"refusing to build: {a.distant_accession} is {div:.1%} from its nearest "
            f"panel genome ({near_name}), outside 15 to 20 percent. "
            f"Pick again by measurement; do not override this check.")
    if near_name != DISTANT_NEAREST:
        _err(f"  note: nearest panel genome is {near_name}, not the previously recorded {DISTANT_NEAREST}")

    primers = [(n, s) for n, s in read_fasta(a.primers)]
    placed, weak = place_primers(founder, primers, a.min_fit)
    amps = cut_amplicons(founder, placed)
    if len(amps) < 4:
        raise SystemExit(f"only {len(amps)} amplicons placed on {MAIN_NAME}; primer placement looks wrong")
    o_placed, o_weak = place_primers(other, primers, a.min_fit)
    o_amps = cut_amplicons(other, o_placed)
    d_placed, d_weak = place_primers(distant, primers, a.min_fit)
    d_amps = cut_amplicons(distant, d_placed)
    _err(f"amplicons: main {len(amps)}, other serotype {len(o_amps)}, distant {len(d_amps)}")
    _err(f"primers below fit {a.min_fit} on main: {len(weak)}")

    covered_pos = []
    for amp in amps:
        covered_pos.extend(range(amp["start"], amp["end"]))
    tree = build_tree(founder, placed, a.seed, covered_pos)
    # Re-cut amplicons on each mutant so the deletion shortens the product and
    # SNPs sit in the insert. Primer sites were kept clear of both.
    mutant_amps = {}
    for sid, seq in tree["genomes"].items():
        pld, _ = place_primers(seq, primers, a.min_fit)
        mutant_amps[sid] = cut_amplicons(seq, pld)
        if len(mutant_amps[sid]) != len(amps):
            _err(f"  warning: {sid} placed {len(mutant_amps[sid])} amplicons, founder placed {len(amps)}")

    cov, uncovered = covered_intervals(amps, len(founder))
    drop_amps = [x for x in amps if x["pool"] in ("L3b", "L4")]
    kept = [x for x in amps if x["pool"] not in ("L3b", "L4")]
    _, drop_uncovered = covered_intervals(kept, len(founder))
    drop_bases = sum(b - a + 1 for a, b in drop_uncovered)
    scheme_unc = {tuple(iv) for iv in uncovered}
    intentional = [iv for iv in drop_uncovered if tuple(iv) not in scheme_unc]
    intentional_bases = sum(b - a + 1 for a, b in intentional)
    _err(f"dropout uncovered {drop_bases} bp of {len(founder)} ({drop_bases / len(founder):.1%}); "
         f"intentional middle gap {intentional_bases} bp ({intentional_bases / len(founder):.1%})")

    key_dir = a.key_dir or a.out.parent / (a.out.name + "_SYN_answer_key")
    key_dir.mkdir(parents=True, exist_ok=True)
    plan = wanted(sample_plan(depths), a.scenario)

    expected = []
    for s in plan:
        if s["scenario"] == "distant":
            call = {
                "chooser_genome_expected": near_name,
                "chooser_note": "VP1 vote, not whole-genome identity, decides the call. "
                                "The nearest whole genome is recorded. Distant should be true "
                                "if read identity to the chosen genome is under 0.90.",
                "distant": True, "mixed": False,
            }
        elif s["mix"] == 0.30:
            call = {"chooser_genome_expected": MAIN_NAME, "distant": False, "mixed": True,
                    "mix_fraction": 0.30, "other_serotype": OTHER_NAME}
        elif s["mix"] == 0.03:
            call = {"chooser_genome_expected": MAIN_NAME, "distant": False, "mixed": False,
                    "mix_fraction": 0.03, "mix_note": "under the 5 percent mixed flag, even if the read count exceeds 50",
                    "other_serotype": OTHER_NAME}
        else:
            call = {"chooser_genome_expected": MAIN_NAME, "distant": False, "mixed": False}
        if s["dropout"]:
            call["zero_coverage_intervals_1based"] = drop_uncovered
            call["zero_coverage_fraction"] = round(drop_bases / len(founder), 4)
        if s["genome"] == "SYN-05":
            call["deletion_expected_in_consensus"] = False
        expected.append({"sample_id": s["sample_id"], "barcode": s["barcode"], **call,
                         "scenario": s["scenario"], "depth": s["depth"]})

    truth = {
        "synthetic": True,
        "label": "SYN",
        "warning": "Simulated reads. Not a real outbreak and not a real sample. Do not cite as one.",
        "seed": a.seed,
        "scenario": a.scenario,
        "depths": depths,
        "main_founder": {"name": MAIN_NAME, "length": len(founder), "role": "outbreak cluster"},
        "other_serotype": {"name": OTHER_NAME, "length": len(other),
                           "role": "barcode crosstalk at 3 percent and co-infection at 30 percent"},
        "distant_founder": {
            "accession": a.distant_accession,
            "header": dheader.split("|")[0][:200] if a.collection else dheader[:200],
            "length": len(distant),
            "nearest_panel_genome": near_name,
            "identity": near_stats,
            "selection_rule": "aligned-base identity (matches / aligned bases), terminal gaps ignored. "
                              "Global identity is reported and was not the rule. "
                              "A candidate was kept only if the nearest of the 79 panel genomes "
                              "was 15 to 20 percent away.",
        },
        "primers": {
            "set": a.primers.name,
            "min_fit": a.min_fit,
            "placed_on_main": len(placed),
            "below_fit_on_main": [w["primer"] for w in weak],
            "amplicons_on_main": amps_public(amps),
            "scheme_uncovered_1based": uncovered,
            "scheme_note": "The 3' RT primer does not meet min_fit on these GenBank records "
                           "(no polyA tail), so the terminal stretch is uncovered on every sample. "
                           "That gap is the scheme, not the dropout scenario.",
        },
        "transmission_tree": {
            "parent": tree["parent"],
            "edges_substitutions": tree["edges_substitutions"],
            "identical_pair": tree["identical_pair"],
            "deletion": tree["deletion"],
            "cumulative_snps_founder_coordinates": tree["cumulative_snps"],
            "note": "Substitutions are in the insert, not under a primer, so they survive clipping. "
                    "One substitution on each edge is inside VP1 (3178-3834 on the founder).",
        },
        "dropout": {
            "rule": "Drop the L3b and L4 amplicons. On the main founder that opens a middle gap of about a third of the genome. The scheme's uncovered 3' end is extra and is present on every sample.",
            "amplicons_dropped": [x["pool"] + f":{x['start']+1}-{x['end']}" for x in drop_amps],
            "intentional_gap_1based_inclusive": intentional,
            "intentional_gap_bases": intentional_bases,
            "intentional_gap_fraction": round(intentional_bases / len(founder), 4),
            "uncovered_1based_inclusive": drop_uncovered,
            "uncovered_bases": drop_bases,
            "uncovered_fraction": round(drop_bases / len(founder), 4),
        },
        "badread": {
            "version_note": "Badread nanopore2023 error model and qscore model (R10.4.1).",
            "identity": "98,99.5,1.5",
            "length": "30000,1000 so each linear amplicon is read in full; host fragments 1500,200",
            "adapters": "off (0,0)",
            "junk_random_chimeras_glitches": 0,
            "depth_definition": "per amplicon, Badread --quantity {depth}x on that amplicon alone. "
                                "Overlaps are deeper. The 20x, 200x and 2000x labels are this per-amplicon depth "
                                "on the clean founder. Other scenarios are 200x.",
        },
        "host_background": {
            "fasta": str(a.host_fasta),
            "source": "Bos taurus chromosome 1, NC_037328.1 positions 1000001-1100000, ARS-UCD2.0. "
                      "No bovine read set was in the local reference folder, so the background is simulated from this public segment.",
            "fmdv_read_fraction_target": FMDV_READ_FRACTION,
        },
        "samples": expected,
        "cannot_test": [
            "adapter trimming (adapters were left off)",
            "junk, random, chimera and glitch filters (those rates were 0)",
            "the 3-nt deletion in the consensus (the caller is substitution-only; the key expects a miss)",
            "a real MinKNOW barcode classification error beyond the planted crosstalk fraction",
            "live GUI scores, unless this folder is imported on the lab server",
        ],
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (key_dir / "SYN_truth.json").write_text(json.dumps(truth, indent=1) + "\n")
    table = key_dir / "SYN_truth_table.tsv"
    with open(table, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["sample_id", "barcode", "scenario", "depth", "expected_genome",
                    "distant", "mixed", "notes"])
        for s in expected:
            notes = []
            if s["sample_id"] in ("SYN-02", "SYN-03"):
                notes.append("identical pair" if s["sample_id"] == "SYN-03" else "3 SNPs from founder")
            if s["sample_id"] == "SYN-04":
                notes.append("3 further SNPs from SYN-02")
            if s["sample_id"] == "SYN-05":
                notes.append("3 further SNPs from SYN-04 plus a 3-nt deletion at founder "
                             f"pos {tree['deletion']['pos1']}")
            if s.get("mix_fraction"):
                notes.append(f"{s['mix_fraction']:.0%} reads from {OTHER_NAME}")
            if s.get("zero_coverage_fraction"):
                notes.append(f"uncovered fraction {s['zero_coverage_fraction']}")
            w.writerow([s["sample_id"], s["barcode"], s["scenario"], s["depth"],
                        s["chooser_genome_expected"], s["distant"], s["mixed"], "; ".join(notes)])
    # SNP table
    with open(key_dir / "SYN_snp_table.tsv", "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["sample_id", "founder_pos_1based", "ref", "alt", "in_vp1", "from_parent"])
        edge_pos = {
            "SYN-02": {snp["pos1"] for snp in tree["snps_from_parent"]["SYN-02"]},
            "SYN-04": {snp["pos1"] for snp in tree["snps_from_parent"]["SYN-04"]},
            "SYN-05": {snp["pos1"] for snp in tree["snps_from_parent"]["SYN-05"]},
        }
        for sid, snps in tree["cumulative_snps"].items():
            for snp in snps:
                w.writerow([sid, snp["pos1"], snp["ref"], snp["alt"],
                            VP1_START_1 <= snp["pos1"] <= VP1_END_1,
                            snp["pos1"] in edge_pos.get(sid, set())])
    _err(f"answer key: {key_dir / 'SYN_truth.json'}")
    if a.dry_run:
        _err("dry run: no reads written")
        for amp in amps:
            _err(f"  amp {amp['pool']:4} {amp['start']+1:5}-{amp['end']:<5} {amp['length']:4} bp "
                 f"{amp['forward'].split('-')[-1]} / {amp['reverse'].split('-')[-1]}")
        _err(f"deletion founder pos {tree['deletion']['pos1']} bases {tree['deletion']['bases']}")
        for sid, snps in tree["cumulative_snps"].items():
            _err(f"  {sid} snps {[s['pos1'] for s in snps]}")
        return 0

    out = a.out
    fastq_pass = out / "fastq_pass"
    if fastq_pass.exists():
        raise SystemExit(f"{fastq_pass} already exists; refuse to overwrite a run folder")
    work = a.work or (out.parent / "SYN_badread_work")
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "SYN_THIS_IS_SIMULATED.txt").write_text(
        "SYN synthetic MinKNOW folder. These are not real samples and not a real outbreak.\n"
        "Built by bin/fmdv_simulate.py. Answer key is beside this folder.\n"
        f"seed {a.seed}. scenario {a.scenario}.\n"
    )

    # Cache amplicon reads per (genome tag, depth, dropout) so identical samples
    # and the depth series do not resimulate the same product.
    cache: Dict[Tuple, List[Tuple[str, str, str]]] = {}

    def genome_amps(tag: str) -> List[Dict]:
        if tag == "distant":
            return d_amps
        if tag == "other":
            return o_amps
        return mutant_amps[tag]

    sheet_rows = []
    run_counts = []
    for s in plan:
        _err(f"sample {s['sample_id']} {s['barcode']} {s['scenario']} {s['depth']}x")
        tag = s["genome"]
        amps_s = genome_amps(tag)
        if s["dropout"]:
            amps_s = [x for x in amps_s if x["pool"] not in ("L3b", "L4")]
        key = (tag, s["depth"], s["dropout"])
        tag_salt = {"SYN-01": 11, "SYN-02": 22, "SYN-03": 33, "SYN-04": 44, "SYN-05": 55,
                    "distant": 77, "other": 88}.get(tag, 99)
        if key not in cache:
            cache[key] = simulate_amplicons(
                amps_s, s["depth"], a.seed + s["depth"] * 100 + tag_salt,
                work, f"{tag}_d{s['depth']}")
        main_reads = list(cache[key])
        mix_reads: List[Tuple[str, str, str]] = []
        if s["mix"]:
            okey = ("other", s["depth"], False)
            if okey not in cache:
                cache[okey] = simulate_amplicons(o_amps, s["depth"], a.seed + 17 + s["depth"],
                                                 work, f"other_d{s['depth']}")
            n_mix = int(round(len(main_reads) * s["mix"] / (1 - s["mix"])))
            rng = random.Random(a.seed + int(s["mix"] * 1000) + s["depth"])
            pool = list(cache[okey])
            rng.shuffle(pool)
            mix_reads = pool[:n_mix]
        fmdv = main_reads + mix_reads
        n_host = int(round(len(fmdv) * (1 - FMDV_READ_FRACTION) / FMDV_READ_FRACTION))
        _err(f"  FMDV reads {len(fmdv)} (mix {len(mix_reads)}); host target {n_host}")
        host = simulate_host(a.host_fasta, n_host, a.seed + 5000 + s["depth"] + int(s["barcode"][-2:]), work)
        fmdv_tagged = retag(main_reads, s["sample_id"], s["barcode"], s["scenario"], tag)
        mix_tagged = retag(mix_reads, s["sample_id"], s["barcode"], s["scenario"], "other-serotype")
        host_tagged = retag(host, s["sample_id"], s["barcode"], s["scenario"], "bovine-NC_037328.1")
        all_recs = fmdv_tagged + mix_tagged + host_tagged
        rng = random.Random(a.seed + int(s["barcode"][-2:]))
        rng.shuffle(all_recs)
        names = write_chunks(fastq_pass / s["barcode"], s["barcode"], all_recs)
        frac = len(fmdv) / len(all_recs) if all_recs else 0
        _err(f"  wrote {len(all_recs)} reads in {len(names)} chunk(s), FMDV fraction {frac:.2f}")
        sheet_rows.append((s["barcode"], s["sample_id"], s["scenario"], s["depth"], "SYN"))
        run_counts.append({
            "sample_id": s["sample_id"], "barcode": s["barcode"],
            "fmdv_reads": len(fmdv), "other_serotype_reads": len(mix_reads),
            "bovine_reads": len(host), "fmdv_read_fraction": round(frac, 4),
            "chunks": names,
        })

    sheet_path = out / "sample_sheet_SYN.csv"
    with open(sheet_path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["barcode", "sample_id", "scenario", "depth", "synthetic"])
        w.writerows(sheet_rows)
    truth["run_counts"] = run_counts
    truth["run_folder"] = str(out)
    (key_dir / "SYN_truth.json").write_text(json.dumps(truth, indent=1) + "\n")
    _err(f"run folder: {out}")
    _err(f"sample sheet: {sheet_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
