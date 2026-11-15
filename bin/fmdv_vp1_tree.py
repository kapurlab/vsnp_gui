#!/usr/bin/env python3
"""Place a project's samples among the FMDV lineages on a VP1 tree.

VP1 is the unit of FMDV epidemiology: serotypes, topotypes and lineages are
defined on it (Samuel and Knowles 2001, J Gen Virol 82:609; Knowles et al.
2016, Rev Sci Tech 35:741), and recombination, which is almost entirely in
the non-structural half of the genome (Ferretti et al. 2020, PLoS Pathog
16:e1008235), does not reach it. A whole-genome tree on one reference puts
samples of several serotypes in no clade at all; a VP1 tree puts them where
the reference laboratories would.

Input: step1/<sample>/<sample>_VP1.fasta (written by fmdv_consensus.py) for
every sample with enough of VP1 called, plus the library's VP1 panel
(lib/panels/vp1.fasta, 79 lineage genomes) and, when present, a context set
lib/vp1_context/<SEROTYPE>.fasta of GenBank VP1 sequences for that
serotype (fmdv_vp1_context.py). The context set is not used whole: identical
sequences collapse to one, a backbone keeps up to --per-topotype per
topotype (East African records first, then longest), and each sample's
--neighbours nearest sequences by k-mer Jaccard are added from the full set.
The backbone shows the lineages, the neighbours the fine structure around
the sample, and an O tree stays at hundreds of taxa rather than thousands.
VP1_context_selection.tsv in the output says why each context sequence is
there. With --serotype the samples and panel are restricted to one
serotype, which is the tree a report wants; without it everything goes in.

Output, under <project>/step2/<stamp>_VP1-tree[-SEROTYPE]/VP1/: the input sequences,
the mafft alignment, VP1.tre (Newick) and vp1_tree.json. The folder sits
where the Step 2 tree viewer looks for .tre files.

Tools: mafft (the fork's own env, conda_setup/environment.yml); the tree
from raxmlHPC (vsnp3 env) when on PATH, else iqtree2 or iqtree, else
FastTree. Nothing in a shared environment is changed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


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


def serotype_of(safe_name: str) -> str:
    """SAT1-I_KM268899 -> SAT1; ASIA1-ASIA-Sindh-08_KM268898 -> ASIA1."""
    return safe_name.split("-", 1)[0].split("_", 1)[0].upper()


def tree_label(name: str) -> str:
    """Newick-safe: letters, digits, dot, dash, underscore."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name.split()[0])


# Records from these countries rank first inside each topotype's cap, so the
# region the trial works in is always represented in the backbone.
EAST_AFRICA = ["ETHIOPIA", "KENYA", "UGANDA", "TANZANIA", "SUDAN", "SOUTH_SUDAN", "ERITREA", "SOMALIA",
               "RWANDA", "BURUNDI", "DJIBOUTI"]
KMER = 11


def read_context_tsv(path: Path) -> Dict[str, Dict[str, str]]:
    """name -> row of the context TSV written by fmdv_vp1_context.py; empty when absent."""
    if not path.is_file():
        return {}
    rows: Dict[str, Dict[str, str]] = {}
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if f and f[0]:
                rows[f[0]] = dict(zip(header, f))
    return rows


def topotype_of(safe_name: str) -> str:
    """SAT1-I_KM268899 -> I; ASIA1-ASIA-Sindh-08_KM268898 -> ASIA-Sindh-08; SAT1-NA_X -> NA."""
    head = safe_name.rsplit("_", 1)[0]
    return head.split("-", 1)[1] if "-" in head else "NA"


def kmers(seq: str, k: int = KMER) -> set:
    s = re.sub(r"[^ACGT]", "", seq.upper())
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def select_context(sero: str, records: List[Tuple[str, str]], meta: Dict[str, Dict[str, str]],
                   sample_seqs: List[Tuple[str, str]], per_topotype: int, neighbours: int,
                   regional: List[str], collapse: bool) -> Tuple[List[Tuple[str, str]], List[Dict], Dict]:
    """Backbone plus neighbourhood from one serotype's context set.

    Backbone: identical sequences collapsed to one, then up to per_topotype
    per topotype, regional records first and longest first within. Then the
    `neighbours` nearest context sequences to each sample by k-mer Jaccard,
    from the whole (collapsed) set, so fine structure near the sample is
    kept whatever the cap removed. Returns the chosen records, one row per
    chosen record saying why, and counts.
    """
    # Collapse identical sequences.
    by_seq: Dict[str, List[str]] = {}
    seq_of: Dict[str, str] = {}
    for name, seq in records:
        key = seq.upper() if collapse else name
        by_seq.setdefault(key, []).append(name)
        seq_of[name] = seq
    reps: List[str] = []
    collapsed_into: Dict[str, List[str]] = {}
    for key, names in by_seq.items():
        names.sort()
        reps.append(names[0])
        if len(names) > 1:
            collapsed_into[names[0]] = names[1:]

    def country(n: str) -> str:
        return meta.get(n, {}).get("country", "").upper().replace(" ", "_")

    def is_regional(n: str) -> bool:
        c = country(n)
        return any(c == r or c.startswith(r + "_") or c.startswith(r + "(") for r in regional)

    chosen: Dict[str, List[str]] = {}
    per: Dict[str, List[str]] = {}
    for n in reps:
        per.setdefault(meta.get(n, {}).get("topotype") or topotype_of(n), []).append(n)
    for topo, names in per.items():
        names.sort(key=lambda n: (not is_regional(n), -len(seq_of[n]), n))
        for n in (names[:per_topotype] if per_topotype else names):
            chosen.setdefault(n, []).append("backbone:regional" if is_regional(n) else "backbone")

    if neighbours and sample_seqs:
        rep_kmers = {n: kmers(seq_of[n]) for n in reps}
        for sname, sseq in sample_seqs:
            sk = kmers(sseq)
            if not sk:
                continue
            scored = sorted(((len(sk & rk) / len(sk | rk), n) for n, rk in rep_kmers.items()), reverse=True)
            for score, n in scored[:neighbours]:
                chosen.setdefault(n, []).append(f"neighbour:{sname}:{score:.3f}")

    out = [(n, seq_of[n]) for n in reps if n in chosen]
    rows = [{"name": n, "serotype": sero, "topotype": meta.get(n, {}).get("topotype") or topotype_of(n),
             "country": country(n), "region_source": meta.get(n, {}).get("region_source", ""),
             "why": ";".join(chosen[n]), "identical_to": ",".join(collapsed_into.get(n, []))} for n, _ in out]
    counts = {"context_total": len(records), "after_collapse": len(reps), "topotypes": len(per),
              "backbone": sum(1 for n in chosen if any(w.startswith("backbone") for w in chosen[n])),
              "regional": sum(1 for n in chosen if "backbone:regional" in chosen[n]),
              "neighbours": sum(1 for n in chosen if any(w.startswith("neighbour") for w in chosen[n])),
              "selected": len(out)}
    return out, rows, counts


def collect_samples(project: Path, serotype: Optional[str], min_called: float) -> Tuple[List[Tuple[str, str]], List[Dict]]:
    seqs: List[Tuple[str, str]] = []
    report: List[Dict] = []
    for d in sorted((project / "step1").iterdir()) if (project / "step1").is_dir() else []:
        if not d.is_dir():
            continue
        js = d / "fmdv_consensus.json"
        fa = d / f"{d.name}_VP1.fasta"
        if not (js.is_file() and fa.is_file()):
            continue
        try:
            meta = json.loads(js.read_text())
        except (OSError, ValueError):
            continue
        sero = serotype_of(meta.get("reference", ""))
        called = float(meta.get("vp1_called_percent") or 0.0)
        entry = {"sample": d.name, "reference": meta.get("reference", ""), "serotype": sero,
                 "vp1_called_percent": called, "included": True, "reason": ""}
        if serotype and sero != serotype:
            entry.update(included=False, reason=f"serotype {sero}")
        elif called < min_called:
            entry.update(included=False, reason=f"VP1 {called}% called, below {min_called}%")
        report.append(entry)
        if entry["included"]:
            recs = read_fasta(fa)
            if recs:
                seqs.append((d.name, recs[0][1]))
    return seqs, report


def collect_panel(lib: Path, serotype: Optional[str], samples_by_sero: Dict[str, List[Tuple[str, str]]],
                  per_topotype: int, neighbours: int, regional: List[str], collapse: bool
                  ) -> Tuple[List[Tuple[str, str]], List[str], List[Dict], Dict[str, Dict]]:
    seqs: List[Tuple[str, str]] = []
    sources: List[str] = []
    panel = lib / "panels" / "vp1.fasta"
    if panel.is_file():
        sources.append(str(panel))
        for name, seq in read_fasta(panel):
            safe = name.split()[0]
            if serotype and serotype_of(safe) != serotype:
                continue
            seqs.append((safe, seq))
    # The GenBank comparator set per serotype (fmdv_vp1_context.py): a backbone
    # subset plus each sample's nearest neighbours, so the tree shows the
    # lineages and the fine structure around the sample without every record.
    ctx_dir = lib / "vp1_context"
    wanted = [serotype] if serotype else sorted({serotype_of(n) for n, _ in seqs})
    rows: List[Dict] = []
    selection: Dict[str, Dict] = {}
    for s in wanted:
        ctx = ctx_dir / f"{s}.fasta"
        if not ctx.is_file():
            continue
        sources.append(str(ctx))
        records = [(name.split()[0], seq) for name, seq in read_fasta(ctx)]
        meta = read_context_tsv(ctx_dir / f"{s}.tsv")
        chosen, why, counts = select_context(s, records, meta, samples_by_sero.get(s, []),
                                             per_topotype, neighbours, regional, collapse)
        seqs.extend(chosen)
        rows.extend(why)
        selection[s] = counts
    return seqs, sources, rows, selection


def run(cmd: List[str], log: Path, cwd: Optional[Path] = None) -> int:
    with open(log, "a") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=cwd).returncode


def build_tree(aln: Path, out_dir: Path, log: Path, threads: int, bootstraps: int) -> Tuple[str, Optional[Path]]:
    """Newick path from whichever tree builder is on PATH, in order of preference."""
    raxml = shutil.which("raxmlHPC-PTHREADS-AVX2") or shutil.which("raxmlHPC-PTHREADS") or shutil.which("raxmlHPC")
    if raxml:
        cmd = [raxml, "-s", str(aln), "-n", "VP1", "-m", "GTRGAMMA", "-p", "12345", "-w", str(out_dir)]
        if "PTHREADS" in raxml:
            cmd += ["-T", str(max(2, threads))]
        if bootstraps > 0:
            cmd += ["-f", "a", "-x", "12345", "-#", str(bootstraps)]
        if run(cmd, log, out_dir) == 0:
            best = out_dir / ("RAxML_bipartitions.VP1" if bootstraps > 0 else "RAxML_bestTree.VP1")
            if best.is_file():
                return "raxmlHPC", best
    iqtree = shutil.which("iqtree2") or shutil.which("iqtree")
    if iqtree:
        cmd = [iqtree, "-s", str(aln), "-m", "GTR+G", "-T", str(threads), "-pre", str(out_dir / "VP1"), "-quiet"]
        if bootstraps > 0:
            cmd += ["-B", str(max(1000, bootstraps))]
        if run(cmd, log, out_dir) == 0 and (out_dir / "VP1.treefile").is_file():
            return "iqtree", out_dir / "VP1.treefile"
    fasttree = shutil.which("FastTree") or shutil.which("fasttree")
    if fasttree:
        nwk = out_dir / "VP1.fasttree"
        with open(nwk, "w") as fh, open(log, "a") as lg:
            if subprocess.run([fasttree, "-nt", "-gtr", str(aln)], stdout=fh, stderr=lg).returncode == 0:
                return "FastTree", nwk
    return "", None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="project folder (holds step1/ and step2/)")
    ap.add_argument("--lib", required=True, help="FMDV reference library")
    ap.add_argument("--serotype", help="restrict samples and panel to one serotype, e.g. SAT1")
    ap.add_argument("--min-called", type=float, default=50.0, help="skip samples with less of VP1 called (%%)")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--bootstraps", type=int, default=100, help="0 for a best tree only")
    ap.add_argument("--per-topotype", type=int, default=20,
                    help="context backbone: at most this many sequences per topotype, regional first then "
                         "longest first (0 = all)")
    ap.add_argument("--neighbours", type=int, default=20,
                    help="context sequences nearest each sample (k-mer Jaccard) added whatever the cap (0 = none)")
    ap.add_argument("--regional", action="append",
                    help="country that ranks first inside each topotype's cap (repeatable; default East Africa)")
    ap.add_argument("--no-collapse", action="store_true", help="keep identical context sequences as separate taxa")
    ap.add_argument("--out", help="output folder (default step2/<stamp>_VP1-tree[-SEROTYPE])")
    a = ap.parse_args()

    project, lib = Path(a.project).expanduser().resolve(), Path(a.lib).expanduser().resolve()
    serotype = a.serotype.upper() if a.serotype else None
    mafft = shutil.which("mafft")
    if not mafft:
        _err("vp1_tree: mafft is not on PATH. It is in the fork's own env: "
             "mamba env create -f conda_setup/environment.yml, then add that env's bin to PATH.")
        return 2

    samples, report = collect_samples(project, serotype, a.min_called)
    sero_of_sample = {e["sample"]: e["serotype"] for e in report}
    samples_by_sero: Dict[str, List[Tuple[str, str]]] = {}
    for name, seq in samples:
        samples_by_sero.setdefault(sero_of_sample.get(name, ""), []).append((name, seq))
    regional = [r.upper().replace(" ", "_") for r in (a.regional or EAST_AFRICA)]
    panel, sources, context_rows, selection = collect_panel(
        lib, serotype, samples_by_sero, a.per_topotype, a.neighbours, regional, not a.no_collapse)
    for s_, c in selection.items():
        _err(f"vp1_tree: {s_} context {c['context_total']} records, {c['after_collapse']} distinct, "
             f"{c['backbone']} backbone ({c['regional']} regional) + {c['neighbours']} neighbours "
             f"= {c['selected']} used")
    if not samples:
        _err("vp1_tree: no sample has a VP1 consensus to place (run fmdv_consensus.py first, or lower --min-called)")
        return 2
    if len(samples) + len(panel) < 4:
        _err("vp1_tree: fewer than four sequences, no tree to build")
        return 2

    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    # Stamp first: the Step 2 run list accepts <stamp><label> folders only.
    out_root = Path(a.out).expanduser() if a.out else project / "step2" / (f"{stamp}_VP1-tree" + (f"-{serotype}" if serotype else ""))
    out_dir = out_root / "VP1"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "vp1_tree.log"

    labels: Dict[str, str] = {}
    unaligned = out_dir / "VP1_input.fasta"
    with open(unaligned, "w") as fh:
        for name, seq in samples + panel:
            lab = tree_label(name)
            if lab in labels:
                continue
            labels[lab] = name
            fh.write(f">{lab}\n{seq}\n")

    if context_rows:
        with open(out_dir / "VP1_context_selection.tsv", "w") as fh:
            cols = ["name", "serotype", "topotype", "country", "region_source", "why", "identical_to"]
            fh.write("\t".join(cols) + "\n")
            for r in context_rows:
                fh.write("\t".join(r[c] for c in cols) + "\n")

    aligned = out_dir / "VP1_aligned.fasta"
    with open(aligned, "w") as fh, open(log, "a") as lg:
        lg.write(f"$ {mafft} --auto --thread {a.threads} {unaligned}\n")
        rc = subprocess.run([mafft, "--auto", "--thread", str(a.threads), str(unaligned)], stdout=fh, stderr=lg).returncode
    if rc != 0:
        _err(f"vp1_tree: mafft failed, see {log}")
        return 1

    method, nwk = build_tree(aligned, out_dir, log, a.threads, a.bootstraps)
    if nwk is None:
        _err(f"vp1_tree: no tree builder succeeded (raxmlHPC, iqtree2, FastTree), see {log}")
        return 1
    tre = out_dir / f"VP1_{stamp}.tre"
    shutil.copyfile(nwk, tre)

    summary = {
        "project": str(project), "serotype": serotype or "all", "stamp": stamp,
        "samples_included": [s for s, _ in samples], "samples": report,
        "panel_sequences": len(panel), "panel_sources": sources,
        "context_selection": selection,
        "context_rules": {"per_topotype": a.per_topotype, "neighbours": a.neighbours, "regional": regional,
                          "collapse_identical": not a.no_collapse, "kmer": KMER},
        "aligner": "mafft --auto", "tree": method, "bootstraps": a.bootstraps,
        "alignment": str(aligned), "newick": str(tre),
    }
    (out_dir / "vp1_tree.json").write_text(json.dumps(summary, indent=1))
    # The keys the Step 2 run list reads (status, started_at, reference), so
    # the folder shows as a finished run and not as one still running.
    (out_root / "run_metadata.json").write_text(json.dumps({
        "kind": "vp1_tree", "status": "ok", "started_at": started_at,
        "dispatch_state": {"reference": f"VP1 panel ({serotype or 'all serotypes'})"},
        "stamp": stamp, "serotype": serotype or "all",
        "comparison_samples": [s for s, _ in samples], "panel_sequences": len(panel), "tree": method,
    }, indent=1))
    _err(f"vp1_tree: {len(samples)} samples + {len(panel)} panel sequences, {method}, {tre}")
    print(str(tre))
    return 0


if __name__ == "__main__":
    sys.exit(main())
