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
serotype. With --serotype the samples and panel are restricted to one
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


def collect_panel(lib: Path, serotype: Optional[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
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
    # Hook for a GenBank comparator set per serotype: included when present,
    # nothing happens when absent. Built as a follow-up (634 SAT1 VP1-sized
    # records exist in GenBank against 70 genomes).
    ctx_dir = lib / "vp1_context"
    wanted = [serotype] if serotype else sorted({serotype_of(n) for n, _ in seqs})
    for s in wanted:
        ctx = ctx_dir / f"{s}.fasta"
        if ctx.is_file():
            sources.append(str(ctx))
            seqs.extend((name.split()[0], seq) for name, seq in read_fasta(ctx))
    return seqs, sources


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
    panel, sources = collect_panel(lib, serotype)
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
