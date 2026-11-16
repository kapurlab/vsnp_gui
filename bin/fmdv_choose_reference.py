#!/usr/bin/env python3
"""Choose the lineage reference for ONE sample, and make its vsnp3 folder.

Called by the Step 1 batch, once per sample directory, in FMDV mode. Prints
exactly one line to stdout: the reference folder to hand vsnp3 as -t. All
other output goes to stderr and to <sample_dir>/fmdv_reference.json.

Why per sample: within a serotype, FMDV topotypes differ by up to 21%, and
reads stop mapping somewhere past 10 to 15%; measured 14 November 2026, a
topotype 15 to 20% from the reference lost a third to half of the genome to
zero coverage. So the closest available lineage is chosen for each sample,
inside whatever scope the user set (FMDV, a serotype, a lineage, or one
genome). A poor best match is flagged, not refused: a sample from a lineage
with no genome in the library has no close genome anywhere and must still run.

How: competitive minimap2, after Shaw et al. 2025 (NanoFMDV, BMC Genomics
26:770). A subsample of reads is mapped to the 79 VP1 sequences; the genome
whose VP1 collects the most aligned bases, primary alignments only, is the
reference. VP1 is the serotype- and topotype-determining region, so only reads
that carry discriminating sequence vote; the conserved 3' half of the genome,
which splits almost at random between serotypes, does not. If too few reads
reach any VP1 the vote falls back to the complete genomes. The same reads
mapped to the 79 complete genomes give the serotype composition, a mixture
flag, and the identity of the reads to the chosen genome ("distant").

Why not k-mers: the sourmash version of this chooser (k=21, scaled=10, rank
by f_match) was right on SAT1 but sent two serotype A samples and one O
sample to Asia1 on 14 November 2026. Their true lineages were 15% or more
from the nearest library genome, where exact 21-mers mostly fail, and a
1 to 2% trace of Asia1 barcode crosstalk with a near-identical library genome
out-scored them on breadth. Alignment tolerates the divergence and the vote
is weighted by reads, so crosstalk stays a minority.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fmdv_reflib  # noqa: E402

SUBSAMPLE = 20000       # reads used for the vote; a few seconds of minimap2
MIN_MAPQ = 10           # primary alignments only, and unambiguous ones
MIN_ALN = 100           # aligned bases for a read to count
MIN_VOTES = 20          # reads on the winning VP1 before the vote is trusted
DISTANT_BELOW = 0.90    # mean identity of reads to the chosen genome
MIXED_ABOVE = 0.05      # share of FMDV reads from a second serotype
MIXED_MIN_READS = 50


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def _subsample(seqkit: str, reads: List[Path], work: Path, n: int) -> Tuple[Path, float]:
    """First n reads of each input file into one FASTQ, and their mean length."""
    work.mkdir(parents=True, exist_ok=True)
    out = work / "reads.fastq"
    with open(out, "w") as fh:
        for r in reads:
            subprocess.run([seqkit, "head", "-n", str(n), str(r)], check=True, stdout=fh,
                           stderr=subprocess.DEVNULL)
    st = _run([seqkit, "stats", "-T", str(out)]).stdout.strip().splitlines()
    mean_len = float(st[1].split("\t")[6]) if len(st) > 1 else 0.0
    return out, mean_len


def _map(minimap2: str, preset: str, threads: int, panel: Path, reads: Path) -> Dict[str, Dict]:
    """Primary alignments per target: distinct reads, matching bases, aligned
    block, from PAF. Secondary alignments are suppressed; supplementary ones
    count their own bases but not a second read."""
    # -c: base-level alignment, so column 10 is true matches and the de tag
    # is gap-compressed divergence. Without it both are chaining estimates
    # (identities of 0.2 to 0.4 on correct references, measured 14 Nov 2026).
    p = subprocess.run([minimap2, "-c", "-t", str(threads), *preset.split(), "--secondary=no",
                        str(panel), str(reads)], capture_output=True, text=True, check=True)
    per: Dict[str, Dict] = defaultdict(lambda: {"reads": set(), "match": 0, "block": 0, "div": 0.0})
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) < 12:
            continue
        if int(f[11]) < MIN_MAPQ or int(f[10]) < MIN_ALN:
            continue
        de = next((float(x[5:]) for x in f[12:] if x.startswith("de:f:")), None)
        block = int(f[10])
        t = per[f[5]]
        t["reads"].add(f[0]); t["match"] += int(f[9]); t["block"] += block
        t["div"] += (de if de is not None else 1 - int(f[9]) / block) * block
    return {k: {"reads": len(v["reads"]), "match": v["match"], "block": v["block"],
                "identity": (1 - v["div"] / v["block"]) if v["block"] else 0.0}
            for k, v in per.items()}


def _top(table: Dict[str, Dict], allowed: Optional[set] = None, n: int = 5) -> List[Dict]:
    rows = [{"safe": k, **v} for k, v in table.items() if allowed is None or k in allowed]
    rows.sort(key=lambda r: (-r["match"], -r["reads"]))
    return [{"safe": r["safe"], "reads": r["reads"], "bases": r["match"],
             "identity": round(r["identity"], 4)} for r in rows[:n]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", required=True, nargs="+", help="FASTQ file(s) for the sample")
    ap.add_argument("--lib", required=True, help="FMDV reference library directory")
    ap.add_argument("--scope", default="FMDV",
                    help="FMDV | <SEROTYPE> | <SEROTYPE>/<lineage> | genome:<safe>")
    ap.add_argument("--sample-dir", required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--subsample", type=int, default=SUBSAMPLE)
    ap.add_argument("--preset", default="auto", help="minimap2 preset for the genome panel: auto | map-ont | sr")
    ap.add_argument("--vp1-preset", default="", help="minimap2 options for the VP1 panel, e.g. '-x map-ont -k13 -w5' "
                    "(default: that for short reads, -x <preset> for long reads)")
    ap.add_argument("--distant-below", type=float, default=DISTANT_BELOW)
    ap.add_argument("--mixed-above", type=float, default=MIXED_ABOVE)
    a = ap.parse_args()

    lib = Path(a.lib).expanduser().resolve()
    sample_dir = Path(a.sample_dir).resolve()
    idx = fmdv_reflib.load(lib)
    candidates = fmdv_reflib.genomes_in_scope(idx, a.scope)
    if not candidates:
        _err(f"fmdv: scope {a.scope!r} matches no genome in {lib}")
        return 2
    allowed = {c["safe"]: c for c in candidates}
    by_safe = {g["safe"]: g for g in idx["genomes"]}

    def serotype_of(safe: str) -> str:
        return by_safe.get(safe, {}).get("serotype") or safe.split("-")[0]

    minimap2, seqkit = shutil.which("minimap2"), shutil.which("seqkit")
    if not (minimap2 and seqkit):
        _err("fmdv: minimap2 and seqkit must be on PATH (both are in the vsnp3 env)"); return 2
    vp1_panel, gen_panel = lib / "panels" / "vp1.fasta", lib / "panels" / "genomes.fasta"
    if not gen_panel.is_file():
        _err(f"fmdv: {gen_panel} missing; run fmdv_reflib.py panels"); return 2

    result: Dict = {"scope": a.scope, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "method": "minimap2 VP1 panel vote, genome panel composition (after NanoFMDV)"}

    work = sample_dir / "fmdv_choose"
    reads, mean_len = _subsample(seqkit, [Path(r) for r in a.reads], work, a.subsample)
    short = 0 < mean_len < 300
    preset = a.preset if a.preset != "auto" else ("sr" if short else "map-ont")
    # Short reads against the VP1 panel need a more sensitive seed than the
    # sr preset gives: on a lineage 12% from the nearest panel VP1 (O/Cathay,
    # Illumina 2x150, measured 15 November 2026) sr placed one read in 20,000
    # and map-ont none, while map-ont with k=13 and w=5 placed 523, all on the
    # right genome. The genome panel keeps the platform preset: it gives the
    # identity figure and the sample's overall FMDV read count, and sr maps
    # the same divergent reads to whole genomes well enough for both.
    vp1_preset = a.vp1_preset or ("-x map-ont -k13 -w5" if short else f"-x {preset}")
    result.update({"preset": preset, "vp1_preset": vp1_preset,
                   "reads_used": a.subsample, "mean_read_length": round(mean_len)})

    gen = _map(minimap2, f"-x {preset}", a.threads, gen_panel, reads)
    vp1 = _map(minimap2, vp1_preset, a.threads, vp1_panel, reads) if vp1_panel.is_file() else {}

    # Composition over ALL genomes (not just the scope): a sample whose reads
    # are mostly another serotype must be visible even under a serotype scope.
    # Serotype is read off VP1, not the whole genome: FMDV recombines in the
    # non-structural region, and a serotype A strain's 3' half can sit closer
    # to an O genome than to the library's A genome. On whole genomes the
    # Pirbright A samples read as 21% "O"; on VP1 they do not.
    def composition(table: Dict[str, Dict], key: str = "reads"):
        n_all = sum(v[key] for v in table.values())
        by_sero: Dict[str, int] = defaultdict(int)
        for k, v in table.items():
            by_sero[serotype_of(k)] += v[key]
        ranked = sorted(by_sero.items(), key=lambda x: -x[1])
        return n_all, ranked, ({s: round(n / n_all, 4) for s, n in ranked} if n_all else {})

    fmdv_reads, _, share_genome = composition(gen)
    vp1_reads, ranked, share = composition(vp1)
    share_table = vp1
    # The mixture call comes from VP1 or not at all. The whole-genome
    # composition is not a serotype composition: the non-structural half
    # recombines across serotypes, and on it a clean SAT1 sample reads as
    # SAT1 49%, SAT2 38%, SAT3 12%, and an O/PanAsia-2 sample puts more reads
    # on an Asia1 genome than on any O genome (both measured 15 November
    # 2026). It used to stand in when VP1 had under 50 reads, and then
    # declared an O/Cathay sample 19% "A". Now too few capsid reads means the
    # composition is reported as undetermined, with the genome figure kept
    # beside it for information.
    composition_basis = "vp1" if vp1_reads >= MIXED_MIN_READS else "undetermined"
    second = ranked[1] if len(ranked) > 1 else None
    mixed = bool(composition_basis == "vp1" and second and second[1] >= MIXED_MIN_READS
                 and second[1] / vp1_reads >= a.mixed_above)
    # The same composition weighted by matching bases, which is what the vote
    # below counts. The two can rank serotypes differently on a mixture: a
    # serotype can bring more reads but fewer aligned bases, and then the
    # read-weighted share names one serotype while f_match names another.
    # Reporting both is what makes that readable instead of contradictory.
    _, _, share_bases = composition(share_table, "match")
    result.update({"fmdv_reads": fmdv_reads, "vp1_reads": vp1_reads, "serotype_share": share,
                   "composition_basis": composition_basis,
                   "serotype_share_bases": share_bases,
                   "serotype_share_genome": share_genome, "mixed": mixed,
                   "top_vp1": _top(vp1), "top_genome": _top(gen)})
    if composition_basis == "undetermined":
        _err(f"fmdv: {sample_dir.name}: only {vp1_reads} reads reached any VP1, so the serotype "
             f"composition and the mixture flag are undetermined (whole-genome shares are kept "
             f"for information only)")
    if mixed:
        _err(f"fmdv: WARNING {sample_dir.name} looks mixed: "
             + ", ".join(f"{s} {share[s]:.0%}" for s in list(share)[:3])
             + ". Barcode crosstalk sits at 1 to 2%; this is more.")

    if a.scope.startswith("genome:") and len(candidates) == 1:
        chosen = candidates[0]
        result.update({"chosen": chosen, "pinned": True, "vote": None, "f_match": None, "runner_up": None})
    else:
        vp1_in = _top(vp1, set(allowed), n=10)
        gen_in = _top(gen, set(allowed), n=10)
        if vp1_in and vp1_in[0]["reads"] >= MIN_VOTES:
            vote, rows = "vp1", vp1_in
        elif gen_in and gen_in[0]["reads"] >= MIN_VOTES:
            vote, rows = "genome", gen_in
            _err(f"fmdv: fewer than {MIN_VOTES} reads reached any VP1 in scope; voting on complete genomes")
        else:
            best = (vp1_in or gen_in or [{"reads": 0}])[0]["reads"]
            _err(f"fmdv: no genome in scope {a.scope!r} collects {MIN_VOTES} reads "
                 f"(best {best}; {fmdv_reads} reads map to FMDV at all); cannot choose a reference")
            result.update({"chosen": None, "error": "no in-scope match"})
            (sample_dir / "fmdv_reference.json").write_text(json.dumps(result, indent=1))
            return 3
        best, runner = rows[0], (rows[1] if len(rows) > 1 else None)
        total = sum(r["bases"] for r in rows) or 1
        chosen = allowed[best["safe"]]
        # f_match keeps its slot for the GUI: now the chosen genome's share of
        # the in-scope vote (aligned bases), 0 to 1.
        result.update({"chosen": chosen, "pinned": False, "vote": vote,
                       "f_match": round(best["bases"] / total, 4), "vote_reads": best["reads"],
                       "runner_up": ({"safe": runner["safe"], "share": round(runner["bases"] / total, 4),
                                      "reads": runner["reads"]} if runner else None)})

    ident = gen.get(chosen["safe"], {}).get("identity")
    result["identity"] = round(ident, 4) if ident is not None else None
    result["distant"] = bool(ident is not None and ident < a.distant_below)
    if result["distant"]:
        _err(f"fmdv: WARNING reads are only {ident:.1%} identical to the closest available "
             f"lineage {chosen['safe']}; expect reduced coverage in the capsid. "
             f"The sample may belong to a lineage with no genome in the library.")

    folder = fmdv_reflib.materialize(lib, chosen["safe"])
    result["reference_folder"] = str(folder)
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (sample_dir / "fmdv_reference.json").write_text(json.dumps(result, indent=1))
    try:
        reads.unlink()
    except OSError:
        pass
    _err(f"fmdv: {sample_dir.name} -> {chosen['serotype']}/{chosen['lineage'] or 'NA'} "
         f"{chosen['isolate']} ({chosen['accession']})"
         + (f", {result['vote']} vote share {result['f_match']:.2f}, identity {result['identity']}"
            if not result.get("pinned") else ", pinned")
         + (", composition " + " ".join(f"{s}={v:.0%}" for s, v in list(share.items())[:3]) if share else ""))
    print(str(folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
