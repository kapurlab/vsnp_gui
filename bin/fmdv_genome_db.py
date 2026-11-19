#!/usr/bin/env python3
"""Build a Step 2 database from the collection's complete genomes of one serotype.

Step 2 compares VCFs called on one reference. For a lineage reference the
78 other panel genomes already give one VCF per lineage
(fmdv_panel_build.sh). This adds the complete genomes of the reference's
own serotype from NanoFMDV's GenBank collection, the same file the VP1
context sets come from, so a sample is compared within its lineage against
every genome GenBank holds for it, not one per topotype.

Which genomes: the records that fmdv_vp1_context.py sliced VP1 from
(region_source sliced_from_genome in lib/vp1_context/<SEROTYPE>.tsv), at
least --min-length long and not in the panel. Using that list means each
genome passed the VP1 check and carries the same safe name as its VP1
slice in the tree, so the two views cross-reference by name.

How: each genome is written to lib/genomes_context/<name>.fasta and called
against the reference with vsnp3 step1 in FASTA mode (-f), which simulates
reads from the sequence; the _zc.vcf files are copied into
lib/vcf_db_folders/<ref>/<db name>/ beside the panel databases, with a
SOURCE.json listing every genome and saying the VCFs are reference-derived.
Simulated reads are perfect and uniform, so these VCFs pass every quality
filter by construction and carry no mixed positions. With --register the
database is appended to the fork's config as a vcf_db_folders entry,
disabled, for the user to tick.

Usage:
  fmdv_genome_db.py --source <fmdv-gbbase.fasta> --lib ~/fmdv_refs --ref SAT1-I_KM268899
  fmdv_genome_db.py ... --topotype I --parallel 8 --register
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fmdv_reflib  # noqa: E402

DEFAULT_CONFIG = Path("~/.config/fmdv_gui_xdg/vsnp_gui/config.json")


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def serotype_of(safe: str) -> str:
    return safe.split("-", 1)[0].split("_", 1)[0].upper()


def read_records(path: Path) -> Iterator[Tuple[str, str]]:
    name, seq = None, []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(seq)
                name, seq = line[1:].strip(), []
            else:
                seq.append(line.strip())
    if name is not None:
        yield name, "".join(seq)


def read_tsv(path: Path) -> List[Dict[str, str]]:
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(header, line.rstrip("\n").split("\t"))) for line in fh if line.strip()]


def accession_in_header(header: str) -> str:
    """Accession of a collection record: field 6 of a labelled header, else the first token."""
    parts = header.split("|")
    if len(parts) == 8:
        return parts[5].strip()
    if len(parts) == 2:
        return parts[1].split("_", 1)[0]
    return ""


def run_step1(genome: Path, refdir: Path, out: Path, vsnp3_step1: str) -> Tuple[str, int, Optional[Path]]:
    name = genome.stem
    existing = sorted(out.glob("alignment_*/*_zc.vcf"))
    if existing:
        return name, 0, existing[0]
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "step1.out", "w") as fh:
        rc = subprocess.run([vsnp3_step1, "-f", str(genome), "-t", str(refdir), "-n", name],
                            cwd=out, stdout=fh, stderr=subprocess.STDOUT).returncode
    vcfs = sorted(out.glob("alignment_*/*_zc.vcf"))
    return name, rc, vcfs[0] if vcfs else None


def register(config: Path, db_dir: Path, ref: str) -> str:
    if not config.is_file():
        return f"config not found at {config}; not registered"
    data = json.loads(config.read_text())
    entries = data.setdefault("vcf_db_folders", [])
    for e in entries:
        if isinstance(e, dict) and e.get("path") == str(db_dir):
            return "already registered"
    entries.append({"enabled": False, "path": str(db_dir), "reference": ref})
    tmp = config.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, config)
    return "registered, disabled"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="NanoFMDV Refs/BLAST/fmdv-gbbase.fasta")
    ap.add_argument("--lib", required=True, help="FMDV reference library")
    ap.add_argument("--ref", required=True, help="lineage reference safe name, e.g. SAT1-I_KM268899")
    ap.add_argument("--serotype", help="serotype of genomes to call (default: the reference's)")
    ap.add_argument("--topotype", action="append", help="restrict to these topotypes (repeatable)")
    ap.add_argument("--min-length", type=int, default=7000, help="shortest genome taken")
    ap.add_argument("--db-name", help="database folder name (default <SEROTYPE>_genbank)")
    ap.add_argument("--parallel", type=int, default=8, help="vsnp3 step1 runs at once")
    ap.add_argument("--register", action="store_true", help="append the database to the fork's config, disabled")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="fork config.json for --register")
    a = ap.parse_args()

    source, lib = Path(a.source).expanduser(), Path(a.lib).expanduser()
    ref = a.ref
    sero = (a.serotype or serotype_of(ref)).upper()
    db_name = a.db_name or f"{sero}_genbank"
    vsnp3_step1 = shutil.which("vsnp3_step1.py")
    if not vsnp3_step1:
        _err("genome_db: vsnp3_step1.py is not on PATH (the vsnp3 env)")
        return 2
    tsv = lib / "vp1_context" / f"{sero}.tsv"
    if not tsv.is_file():
        _err(f"genome_db: {tsv} missing; run fmdv_vp1_context.py first")
        return 2

    want_topo = {t.upper() for t in (a.topotype or [])}
    rows = [r for r in read_tsv(tsv)
            if r.get("region_source") == "sliced_from_genome" and int(r.get("source_length") or 0) >= a.min_length
            and (not want_topo or (r.get("topotype") or "NA").upper() in want_topo)]
    if not rows:
        _err("genome_db: no complete genomes match")
        return 2
    by_acc = {r["accession"]: r for r in rows}

    gen_dir = lib / "genomes_context"
    gen_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}
    for header, seq in read_records(source):
        acc = accession_in_header(header)
        if acc in by_acc and acc not in written:
            name = by_acc[acc]["name"]
            fa = gen_dir / f"{name}.fasta"
            if not fa.is_file():
                fa.write_text(f">{name}\n{seq.upper()}\n")
            written[acc] = fa
    missing = sorted(set(by_acc) - set(written))
    if missing:
        _err(f"genome_db: {len(missing)} genomes not found in the source: {missing[:5]}")
    _err(f"genome_db: {len(written)} {sero} genomes for reference {ref}")

    refdir = fmdv_reflib.materialize(lib, ref)
    build_root = lib / "panel_build" / ref
    db_dir = lib / "vcf_db_folders" / ref / db_name
    db_dir.mkdir(parents=True, exist_ok=True)

    results: List[Tuple[str, int, Optional[Path]]] = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.parallel)) as ex:
        futs = [ex.submit(run_step1, fa, refdir, build_root / fa.stem, vsnp3_step1) for fa in written.values()]
        for f in concurrent.futures.as_completed(futs):
            name, rc, vcf = f.result()
            results.append((name, rc, vcf))
            _err(f"genome_db: {name} exit={rc} {'ok' if vcf else 'no VCF'}")
    ok, failed = [], []
    for name, rc, vcf in sorted(results):
        if vcf:
            shutil.copyfile(vcf, db_dir / vcf.name)
            ok.append(name)
        else:
            failed.append(name)

    entries = []
    for name in ok:
        r = next(r for r in rows if r["name"] == name)
        entries.append({k: r.get(k, "") for k in ("name", "accession", "topotype", "country", "year", "host",
                                                   "source_length", "isolate")})
    (db_dir / "SOURCE.json").write_text(json.dumps({
        "reference": ref, "serotype": sero, "database": db_name,
        "source": "NanoFMDV (Shaw et al. 2025) GenBank-derived FMDV collection; complete genomes that passed "
                  "the VP1 check in fmdv_vp1_context.py",
        "source_file": str(source),
        "method": "vsnp3_step1.py -f <genome> -t <reference>: reads simulated from the genome, so every VCF is "
                  "reference-derived, passes quality filters by construction and has no mixed positions",
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0),
        "genomes": entries, "failed": failed,
    }, indent=1))
    note = register(Path(a.config).expanduser(), db_dir, ref) if a.register else "not registered (--register)"
    _err(f"genome_db: {len(ok)} VCFs in {db_dir}, {len(failed)} failed, {round(time.time() - t0)} s; {note}")
    print(str(db_dir))
    return 0 if ok and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
