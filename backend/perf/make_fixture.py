#!/usr/bin/env python3
"""Synthetic vsnp_gui projects laid out exactly like real Step 1 output.

  make_fixture.py <root> [--owl 8171] [--small 500] [--renamed 0.40]

<root>/config/vsnp_gui/config.json      per-user config pointing at the data
<root>/data/vsnp3/dependencies/...      reference registry -> <root>/data/refs
<root>/data/refs/<REF>/<REF>.fasta      the project reference (8 segments)
<root>/data/site/refs/vsnp3/vcf_db_folders/   (empty: no panels)
<root>/data/projects/owl, small         the projects

Every sample carries what a real one does (copied from a real project's
sample dir): reads, .provenance/, a timestamped *_stats.xlsx and report,
run logs, run_metadata.json, _provenance/, and alignment_<REF>/ with the
FASTA, BAM, both VCFs and unmapped reads. A fraction of the owl samples are
aligned under alignment_25-003495-001/ — a renamed copy of the reference
with identical contigs — the way the Ames owl project's 3,204 are.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import random
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openpyxl import Workbook

REF = "owl_25-003495-001"
REF_RENAMED = "25-003495-001"
SEGS = [("seg1", 2341), ("seg2", 2341), ("seg3", 2233), ("seg4", 1778),
        ("seg5", 1565), ("seg6", 1413), ("seg7", 1027), ("seg8", 890)]
STATS_HEADER = [
    "sample", "date", "FASTA/s", "Reference", "FASTQ_R1", "R1 File Size", "R1 Read Count",
    "R1 Length Sum", "R1 Min Length", "R1 Ave Length", "R1 Max Length", "R1 Passing Q20",
    "R1 Passing Q30", "R1 Read Quality Ave", "FASTQ_R2", "R2 File Size", "R2 Read Count",
    "R2 Length Sum", "R2 Min Length", "R2 Ave Length", "R2 Max Length", "R2 Passing Q20",
    "R2 Passing Q30", "R2 Read Quality Ave", "Groups", "Aligner", "Mapped Paired Reads",
    "Mapped Single Reads", "Unmapped Reads", "Unmapped Percent", "Unmapped Assembled Contigs",
    "Duplicate Paired Reads", "Duplicate Single Reads", "Duplicate Percent of Mapped Reads",
    "BAM/Reference File", "Reference Length", "Genome with Coverage", "Average Depth",
    "No Coverage Bases", "Percent Ref with Zero Coverage", "Ambiguous SNPs", "Quality SNPs",
]


def fasta_text(name: str) -> str:
    rnd = random.Random(7)
    out = []
    for seg, ln in SEGS:
        out.append(f">{seg} {name} segment\n")
        seq = "".join(rnd.choice("ACGT") for _ in range(ln))
        out.append("\n".join(seq[i:i + 70] for i in range(0, ln, 70)) + "\n")
    return "".join(out)


def vcf_text(sample: str, ref: str, sample_dir: str, nrec: int) -> str:
    rnd = random.Random(hash(sample) & 0xFFFF)
    lines = [
        "##fileformat=VCFv4.2", "##fileDate=20260910", "##source=freeBayes v1.3.10",
        f"##reference={sample_dir}/{ref}.fasta",
    ]
    lines += [f"##contig=<ID={s},length={n}>" for s, n in SEGS]
    lines += [
        "##phasing=none",
        f'##commandline="freebayes -E -1 -e 1 -u --strict-vcf -f {sample_dir}/{ref}.fasta {sample}_nodup.bam"',
        '##INFO=<ID=NS,Number=1,Type=Integer,Description="Number of samples with data">',
        '##INFO=<ID=DP,Number=1,Type=Integer,Description="Total read depth at the locus">',
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}",
    ]
    for _ in range(nrec):
        seg, ln = rnd.choice(SEGS)
        lines.append(f"{seg}\t{rnd.randint(1, ln)}\t.\tA\tG\t{rnd.randint(20, 3000)}\tPASS\tNS=1;DP={rnd.randint(5, 90)}\tGT\t1/1")
    return "\n".join(lines) + "\n"


def fastq_gz(path: Path, n: int = 40) -> None:
    rnd = random.Random(len(str(path)))
    buf = []
    for i in range(n):
        seq = "".join(rnd.choice("ACGT") for _ in range(150))
        buf.append(f"@r{i}\n{seq}\n+\n{'I' * 150}\n")
    with gzip.open(path, "wt") as fh:
        fh.write("".join(buf))


def stats_xlsx(path: Path, sample: str, ref: str, ts: str) -> None:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append(STATS_HEADER)
    n = (hash(sample) & 0xFFFF) + 1000
    ws.append([
        sample, ts, f"{ref}.fasta", f"{ref} Forced", f"{sample}_1.fastq.gz", "71.5 MB", f"{n:,}",
        f"{n * 237:,}", "35", "236.9", "251", "95.94%", "93.31%", "32.0304", f"{sample}_2.fastq.gz",
        "84.5 MB", f"{n:,}", f"{n * 237:,}", "35", "237.2", "251", "88.27%", "81.9%", "25.8778",
        "HPAI_D1-1", "BWA", f"{n * 2:,}", "1,560", "14,755", "1.5%", "skipped assembly", "4,828",
        "567", "0.6%", f"{sample}_nodup.bam made with {ref}", "13,588", "97.68%", f"{40 + n % 60}.2",
        "1,026", "2.3%", str(n % 90), str(n % 900),
    ])
    wb.save(path)


def make_sample(step1: Path, sample: str, ref: str, ts: str) -> None:
    d = step1 / sample
    (d / ".provenance").mkdir(parents=True)
    (d / "_provenance").mkdir()
    fastq_gz(d / f"{sample}_1.fastq.gz")
    fastq_gz(d / f"{sample}_2.fastq.gz")
    (d / ".provenance" / "exit_code").write_text("0\n")
    (d / ".provenance" / "read_type").write_text("paired")
    (d / ".provenance" / "started_at").write_text("1757520000.1\n")
    (d / ".provenance" / "finished_at").write_text("1757520090.2\n")
    (d / ".provenance" / "vsnp_gui_step1_run_cmd.txt").write_text(f"vsnp3_step1.py -r1 {sample}_1.fastq.gz -r2 {sample}_2.fastq.gz -t {ref}\n")
    (d / "_provenance" / "conda_env.yaml").write_text("name: vsnp3\n")
    (d / "_provenance" / "pip_freeze.txt").write_text("openpyxl==3.1\n")
    (d / f"{sample}_{ts}_report.pdf").write_bytes(b"%PDF-1.4 stub\n" * 40)
    stats_xlsx(d / f"{sample}_{ts}_stats.xlsx", sample, ref, ts)
    (d / f"{sample}_run_log.txt").write_text("vsnp3 step1 log\n" * 30)
    (d / "run_step1.log").write_text(f"== Running step1 in {sample} ==\nStart: 2026-09-10T16:00:00Z\nComplete: 2026-09-10T16:01:30Z\nDuration: 90s\n")
    (d / "run_metadata.json").write_text(json.dumps({
        "schema_version": 2, "step": "step1", "run_id": f"run-{sample}", "started_at": "2026-09-10T16:00:00.000000+00:00",
        "finished_at": "2026-09-10T16:01:30.000000+00:00", "duration_seconds": 90.0, "status": "ok", "exit_code": 0,
    }, indent=2))
    a = d / f"alignment_{ref}"
    (a / "unmapped_reads").mkdir(parents=True)
    (a / f"{ref}.fasta").write_text(fasta_text(ref))
    (a / f"{ref}.fasta.fai").write_text("".join(f"{s}\t{n}\t0\t70\t71\n" for s, n in SEGS))
    (a / f"{sample}_filtered_hapall_annotated.vcf").write_text(vcf_text(sample, ref, str(d), 8))
    (a / f"{sample}_nodup.bam").write_bytes(os.urandom(2048))
    (a / f"{sample}_nodup.bam.bai").write_bytes(os.urandom(96))
    (a / f"{sample}_zc.vcf").write_text(vcf_text(sample, ref, str(d), 14 + hash(sample) % 30))
    fastq_gz(a / "unmapped_reads" / f"{sample}_unmapped_R1.fastq.gz", 4)
    fastq_gz(a / "unmapped_reads" / f"{sample}_unmapped_R2.fastq.gz", 4)


def summary_html(groups: list[str], samples: list[str], ts: str) -> str:
    rows = []
    for i, s in enumerate(samples):
        gs = [groups[i % len(groups)], groups[(i * 7) % len(groups)]]
        rows.append("<tr><td>" + s + "</td>" + "".join(f"<td>{g}</td>" for g in dict.fromkeys(gs)) + "</tr>")
    return (f"<html><body><h2>vSNP step 2 summary {ts}</h2><p>Groupings with {len(samples)} listed</p>"
            "<table><tr><th>sample</th><th>groups</th><tr>" + "\n".join(rows) + "</table></body></html>")


def make_run(step2: Path, run_id: str, ref: str, groups: list[str], samples: list[str], results: bool = True) -> None:
    r = step2 / run_id
    (r / "_provenance").mkdir(parents=True)
    ts = run_id
    (r / "run_metadata.json").write_text(json.dumps({
        "schema_version": 2, "step": "step2", "started_at": f"{ts[:10]}T{ts[11:].replace('-', ':')}",
        "status": "ok" if results else "running", "dispatch_state": {"reference": {"name": ref}},
    }))
    (r / "vsnp_gui_step2_run_cmd.txt").write_text("vsnp3_step2.py -a -t " + ref + "\n")
    (r / "vcf_starting_files.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (r / f"vcf_validation_log-{ts}.txt").write_text("ok\n")
    (r / "edited_samples.json").write_text('{"edited_samples": [], "edited_count": 0}')
    if not results:
        for s in samples[:50]:
            (r / f"{s}_zc.vcf").write_text("##fileformat=VCFv4.2\n")
        return
    (r / f"vSNP_step2_summary-{ts}.html").write_text(summary_html(groups, samples, ts))
    (r / "remove_by_name.xlsx").write_bytes(b"PK stub")
    rnd = random.Random(3)
    for gi, g in enumerate(groups):
        gd = r / g
        gd.mkdir()
        n = 3 + (gi * 5) % 40
        members = samples[gi::max(1, len(samples) // n)][:n]
        cols = 50 + gi * 20
        fa = "".join(f">{'root' if i == 0 else m}\n{''.join(rnd.choice('ACGT') for _ in range(cols))}\n"
                     for i, m in enumerate(["root"] + members))
        (gd / f"{g}-{ts}.fasta").write_text(fa)
        (gd / f"{g}-{ts}.tre").write_text("(" + ",".join(f"{m}:0.01" for m in members) + ");\n")
        (gd / f"{g}-{ts}_labeled.tre").write_text("(" + ",".join(f"L_{m}:0.01" for m in members) + ");\n")
        (gd / f"{g}-{ts}_sorted_table.xlsx").write_bytes(b"PK stub sorted")
        (gd / f"{g}-{ts}_cascade_table.xlsx").write_bytes(b"PK stub cascade")


def make_project(root: Path, name: str, n: int, renamed_fraction: float, runs: int, ngroups: int) -> None:
    p = root / "data" / "projects" / name
    (p / "download").mkdir(parents=True)
    step1 = p / "step1"
    step1.mkdir()
    (step1 / "_provenance").mkdir()
    (step1 / "_provenance" / "batch.txt").write_text("provenance\n")
    (p / "project.json").write_text(json.dumps({
        "name": name, "reference": REF, "display_name": f"{name}_{REF}", "created_at": "2026-09-01T10:00:00",
    }, indent=2))
    samples = [f"{name}-{i:05d}" for i in range(1, n + 1)]
    refs = {s: (REF_RENAMED if (i % 5 in (1, 3) and random.Random(i).random() < renamed_fraction * 2.5) else REF)
            for i, s in enumerate(samples)}
    ts = "2026-09-10_16-00-00"
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda s: make_sample(step1, s, refs[s], ts), samples))
    print(f"  {name}: {n} samples in {time.time() - t0:.0f} s ({sum(1 for r in refs.values() if r == REF_RENAMED)} under {REF_RENAMED})")
    db = p / "step2" / "vcf_database"
    db.mkdir(parents=True)
    with open(db / ".vcf_source_manifest.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["filename", "source_type", "source_path"])
        for s in samples:
            src = step1 / s / f"alignment_{refs[s]}" / f"{s}_zc.vcf"
            shutil.copy2(src, db / f"{s}_zc.vcf")
            w.writerow([f"{s}_zc.vcf", "step1", str(src)])
    groups = [f"HPAI_D1-1_Group-{i}" for i in range(1, ngroups + 1)]
    stamps = ["2026-09-08_09-12-33", "2026-09-09_14-40-01", "2026-09-10_10-51-44"][-runs:]
    for i, stamp in enumerate(stamps):
        make_run(p / "step2", stamp, REF, groups if i == len(stamps) - 1 else groups[:5], samples)
    (p / "step2" / ".current_run").write_text(stamps[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--owl", type=int, default=8171)
    ap.add_argument("--small", type=int, default=500)
    ap.add_argument("--renamed", type=float, default=0.40)
    ap.add_argument("--groups", type=int, default=37)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if root.exists():
        shutil.rmtree(root)
    (root / "config" / "vsnp_gui").mkdir(parents=True)
    data = root / "data"
    (data / "vsnp3" / "dependencies").mkdir(parents=True)
    (data / "vsnp3" / "bin").mkdir()
    (data / "vsnp3" / "dependencies" / "reference_options_paths.txt").write_text(f"{data / 'refs'}\n")
    (data / "refs" / REF).mkdir(parents=True)
    (data / "refs" / REF / f"{REF}.fasta").write_text(fasta_text(REF))
    (data / "site" / "refs" / "vsnp3" / "vcf_db_folders").mkdir(parents=True)
    (root / "config" / "vsnp_gui" / "config.json").write_text(json.dumps({
        "projects_root": str(data / "projects"),
        "path_overrides": {"vsnp3_path": str(data / "vsnp3")},
        "saved_project_roots": [], "saved_vsnp3_paths": [], "step1_max_parallel": 3,
        "vcf_db_folders": [], "disabled_vcf_db_paths": [],
    }, indent=2))
    make_project(root, "owl", args.owl, args.renamed, runs=3, ngroups=args.groups)
    make_project(root, "small", args.small, 0.0, runs=1, ngroups=5)
    print(f"fixture at {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
