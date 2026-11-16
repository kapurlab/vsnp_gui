#!/usr/bin/env python3
"""Type a sample's VP1 consensus by blastn against the GenBank VP1 sets.

NanoFMDV's typing step, on the same data: the sample's VP1 consensus is
searched against every record of lib/vp1_context/<SEROTYPE>.fasta (the
NanoFMDV GenBank VP1 set, sliced per serotype by fmdv_vp1_context.py, with
serotype, topotype, lineage, country, year and isolate per record) plus the
79-genome panel VP1s, and the best hit by bitscore names the serotype, the
topotype and the lineage. This is the lineage answer the reads vote cannot
give: the chooser names the nearest of 79 complete genomes (O/ME-SA/Ind-2001
e for an Ind-2001d sample, because the library has no d genome); the typing
hit names the nearest of 9,000 VP1 records, which is the sublineage.

Agreement among the top hits is reported beside the best hit, so a VP1 that
sits between lineages shows as such rather than as a confident call.

Writes <sample_dir>/fmdv_vp1_type.json. Nothing else is changed. Run once
per sample at the end of Step 1 (never fails the sample), or over a whole
project with --project to type samples already run.

Needs blastn and makeblastdb on PATH (BLAST+). The combined database is
built once under lib/vp1_context/_typing/ and rebuilt when a context set is
newer than it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MIN_CALLED = 50.0       # percent of VP1 called before typing is attempted
TOP_N = 20              # hits kept, and the agreement window
NEAR_BEST = 3.0         # an annotated hit this close (percent identity) to the best may name the lineage
MIN_IDENTITY = 70.0     # below this the best hit names no serotype (VP1 across serotypes is 60 to 70%)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_fasta(path: Path) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    name, buf = None, []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if name is not None:
                out.append((name, "".join(buf)))
            name, buf = line[1:].strip(), []
        else:
            buf.append(line.strip())
    if name is not None:
        out.append((name, "".join(buf)))
    return out


def topotype_of_name(safe_name: str) -> str:
    """SAT1-I_KM268899 -> I; O-ME-SA-IND-2001-D_MG893527 -> ME-SA-IND-2001-D."""
    head = safe_name.rsplit("_", 1)[0]
    return head.split("-", 1)[1] if "-" in head else ""


def serotype_of_name(safe_name: str) -> str:
    return safe_name.split("-", 1)[0].split("_", 1)[0]


def split_lineage(topotype_field: str) -> Tuple[str, str]:
    """'ME-SA/IND-2001/D' -> ('ME-SA', 'ME-SA/IND-2001/D'); 'I' -> ('I', 'I')."""
    t = (topotype_field or "").strip()
    if not t:
        return "", ""
    return t.split("/", 1)[0], t


def load_metadata(lib: Path) -> Dict[str, Dict[str, str]]:
    """name -> row for every context record, from the per-serotype TSVs."""
    meta: Dict[str, Dict[str, str]] = {}
    ctx = lib / "vp1_context"
    for tsv in sorted(ctx.glob("*.tsv")):
        with open(tsv) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if f and f[0]:
                    meta[f[0]] = dict(zip(header, f))
    return meta


def ensure_db(lib: Path, makeblastdb: str) -> Path:
    """One BLAST database of every context VP1 plus the panel VP1s, rebuilt
    when any source is newer than it. Returns the database prefix."""
    ctx = lib / "vp1_context"
    sources = sorted(ctx.glob("*.fasta"))
    panel = lib / "panels" / "vp1.fasta"
    if panel.is_file():
        sources.append(panel)
    if not sources:
        raise FileNotFoundError(f"no VP1 sets under {ctx} (run fmdv_vp1_context.py)")
    out = ctx / "_typing"
    out.mkdir(exist_ok=True)
    fasta = out / "all_vp1.fasta"
    prefix = out / "all_vp1"
    newest = max(p.stat().st_mtime for p in sources)
    marker = out / "all_vp1.nsq"
    if marker.is_file() and marker.stat().st_mtime >= newest and fasta.is_file():
        return prefix
    seen: set = set()
    with open(fasta, "w") as fh:
        for src in sources:
            for name, seq in read_fasta(src):
                key = name.split()[0]
                if key in seen or not seq:
                    continue
                seen.add(key)
                fh.write(f">{key}\n{seq}\n")
    subprocess.run([makeblastdb, "-in", str(fasta), "-dbtype", "nucl", "-out", str(prefix)],
                   check=True, capture_output=True, text=True)
    return prefix


def blast(blastn: str, db: Path, query: Path, threads: int) -> List[Dict]:
    cols = ["sseqid", "pident", "length", "qlen", "slen", "bitscore", "evalue", "qcovs", "mismatch", "gapopen"]
    p = subprocess.run([blastn, "-task", "blastn", "-query", str(query), "-db", str(db),
                        "-outfmt", "6 " + " ".join(cols), "-max_target_seqs", "50",
                        "-num_threads", str(threads), "-evalue", "1e-20"],
                       check=True, capture_output=True, text=True)
    hits: List[Dict] = []
    seen: set = set()
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) < len(cols):
            continue
        row = dict(zip(cols, f))
        if row["sseqid"] in seen:       # one HSP per subject: the first is the best
            continue
        seen.add(row["sseqid"])
        hits.append({"name": row["sseqid"], "identity": float(row["pident"]),
                     "aligned": int(row["length"]), "coverage": float(row["qcovs"]),
                     "bitscore": float(row["bitscore"]), "evalue": float(row["evalue"])})
    hits.sort(key=lambda h: -h["bitscore"])
    return hits


def describe(name: str, meta: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    m = meta.get(name)
    if m:
        topo, lineage = split_lineage(m.get("topotype", ""))
        return {"name": name, "serotype": m.get("serotype", "") or serotype_of_name(name),
                "topotype": topo or topotype_of_name(name), "lineage": lineage or topotype_of_name(name),
                "accession": m.get("accession", ""), "isolate": m.get("isolate", ""),
                "country": m.get("country", ""), "year": m.get("year", ""), "host": m.get("host", ""),
                "source": "genbank"}
    topo = topotype_of_name(name)
    return {"name": name, "serotype": serotype_of_name(name), "topotype": topo.split("-")[0] if topo else "",
            "lineage": topo, "accession": name.rsplit("_", 1)[-1], "isolate": "", "country": "", "year": "",
            "host": "", "source": "panel"}


def type_sample(sample_dir: Path, lib: Path, db: Path, meta: Dict[str, Dict[str, str]],
                blastn: str, threads: int, min_called: float) -> Dict:
    sample = sample_dir.name
    out: Dict = {"sample": sample, "method": "blastn of the VP1 consensus against the GenBank VP1 sets and the panel",
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "", "serotype": "", "topotype": "",
                 "lineage": "", "best": None, "top": [], "agreement": None}
    cons = sample_dir / "fmdv_consensus.json"
    fa = sample_dir / f"{sample}_VP1.fasta"
    called = None
    if cons.is_file():
        try:
            called = float(json.loads(cons.read_text()).get("vp1_called_percent") or 0.0)
        except (OSError, ValueError):
            called = None
    out["vp1_called_percent"] = called
    if not fa.is_file():
        out["status"] = "untyped: no VP1 consensus"
        return out
    if called is not None and called < min_called:
        out["status"] = f"untyped: VP1 {called:.1f}% called, below {min_called:.0f}%"
        return out
    ref = sample_dir / "fmdv_reference.json"
    if ref.is_file():
        try:
            d = json.loads(ref.read_text())
            c = d.get("chosen") or {}
            out["chooser"] = {"serotype": c.get("serotype"), "lineage": c.get("lineage"), "safe": c.get("safe"),
                              "mixed": bool(d.get("mixed")), "distant": bool(d.get("distant"))}
        except (OSError, ValueError):
            pass
    hits = blast(blastn, db, fa, threads)
    if not hits:
        out["status"] = "untyped: no blastn hit"
        return out
    top = []
    for h in hits[:TOP_N]:
        top.append({**describe(h["name"], meta), "identity": round(h["identity"], 2),
                    "coverage": h["coverage"], "aligned": h["aligned"], "bitscore": h["bitscore"]})
    best = top[0]
    out["top"] = top
    out["best"] = best
    if best["identity"] < MIN_IDENTITY:
        out["status"] = f"untyped: best hit {best['identity']:.1f}% identical, below {MIN_IDENTITY:.0f}%"
        return out
    # The nearest record names the serotype and is reported as the nearest
    # relative. The lineage comes from the nearest ANNOTATED record: a fifth
    # of the O set carries no topotype (whole-genome deposits, often the
    # sample's own study), and three of the first six trial samples hit one
    # of those at 100% and would have been typed "NA". An annotated hit
    # within NEAR_BEST percent identity of the best names the lineage; if
    # none is that close the lineage is left unassigned and the nearest
    # annotated record is still reported.
    def annotated(t: Dict) -> bool:
        return bool(t["lineage"]) and t["lineage"].upper() != "NA"
    typed_by = next((t for t in top if annotated(t)), None)
    if typed_by and typed_by["identity"] < best["identity"] - NEAR_BEST:
        out["nearest_annotated"] = typed_by
        typed_by = None
    out["typed_by"] = typed_by
    ann = [t for t in top if annotated(t)]
    n_ann = len(ann)
    agree = {"n": len(top), "n_annotated": n_ann,
             "serotype": sum(1 for t in top if t["serotype"] == best["serotype"]) / len(top),
             "topotype": (sum(1 for t in ann if typed_by and t["topotype"] == typed_by["topotype"]) / n_ann) if n_ann else None,
             "lineage": (sum(1 for t in ann if typed_by and t["lineage"] == typed_by["lineage"]) / n_ann) if n_ann else None}
    out.update({"status": "typed", "serotype": best["serotype"],
                "topotype": typed_by["topotype"] if typed_by else "",
                "lineage": typed_by["lineage"] if typed_by else "",
                "identity": best["identity"], "coverage": best["coverage"],
                "lineage_identity": typed_by["identity"] if typed_by else None,
                "agreement": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in agree.items()}})
    ch = out.get("chooser") or {}
    if ch.get("serotype") and ch["serotype"] != best["serotype"]:
        out["note"] = (f"VP1 typing says {best['serotype']} but the reads vote put the sample on "
                       f"{ch['serotype']} ({ch.get('safe')}); a mixed sample, or a consensus built on the wrong serotype")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample-dir", help="one Step 1 sample folder")
    g.add_argument("--project", help="type every sample folder under <project>/step1")
    ap.add_argument("--lib", required=True, help="FMDV reference library")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--min-called", type=float, default=MIN_CALLED)
    ap.add_argument("--force", action="store_true", help="retype samples that already have fmdv_vp1_type.json")
    a = ap.parse_args()

    lib = Path(a.lib).expanduser().resolve()
    blastn, makeblastdb = shutil.which("blastn"), shutil.which("makeblastdb")
    if not (blastn and makeblastdb):
        _err("vp1_type: blastn and makeblastdb must be on PATH (BLAST+; conda_setup/environment.yml has it)")
        return 2
    try:
        db = ensure_db(lib, makeblastdb)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        _err(f"vp1_type: cannot build the VP1 typing database: {e}")
        return 2
    meta = load_metadata(lib)

    if a.sample_dir:
        dirs = [Path(a.sample_dir).resolve()]
    else:
        step1 = Path(a.project).expanduser().resolve() / "step1"
        dirs = sorted(d for d in step1.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))
                      and (d / f"{d.name}_VP1.fasta").is_file())
    typed = 0
    for d in dirs:
        target = d / "fmdv_vp1_type.json"
        if target.is_file() and not a.force and a.project:
            continue
        try:
            res = type_sample(d, lib, db, meta, blastn, a.threads, a.min_called)
        except subprocess.CalledProcessError as e:
            res = {"sample": d.name, "status": f"untyped: blastn failed ({e.stderr.strip()[:200]})"}
        res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        target.write_text(json.dumps(res, indent=1))
        typed += 1
        if res.get("status") == "typed":
            b = res["best"]; t = res.get("typed_by") or {}
            la = res["agreement"].get("lineage")
            _err(f"vp1_type: {d.name} -> {res['serotype']} {res['lineage'] or 'lineage unassigned'}"
                 + (f" (by {t.get('accession')} {t.get('country')} {t.get('year')} at {t.get('identity')}%)" if t else "")
                 + f"; nearest {b['accession']} {b['isolate'][:40]} {b['country']} {b['year']} at {res['identity']:.1f}% "
                 f"over {res['coverage']:.0f}% of VP1"
                 + (f"; lineage agreement {la:.0%} of {res['agreement']['n_annotated']} annotated" if la is not None else "")
                 + (f"; NOTE {res['note']}" if res.get("note") else ""))
        else:
            _err(f"vp1_type: {d.name}: {res.get('status')}")
    _err(f"vp1_type: {typed} sample(s) typed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
