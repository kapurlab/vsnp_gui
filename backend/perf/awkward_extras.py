#!/usr/bin/env python3
"""Make a fixture project awkward: legacy layouts, links, hidden and scaffold
dirs, unfinished samples, edits, a staged-only run, a post-hoc group."""
import json, os, shutil, sys, time
from pathlib import Path
proj = Path(sys.argv[1])
s1 = proj / "step1"
def touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p
base = sorted(d for d in s1.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))[0]
zc = next(base.glob("alignment_*/*_zc.vcf"))
# legacy vSNP2 layout
touch(s1 / "legacy01" / "legacy01_1.fastq.gz"); touch(s1 / "legacy01" / "legacy01_2.fastq.gz")
shutil.copy2(zc, s1 / "legacy01" / "alignment" / "legacy01_zc.vcf") if (s1 / "legacy01" / "alignment").mkdir(parents=True, exist_ok=True) is None else None
# a symlinked sample dir living outside step1
outside = proj / "outside" / "linked01"
touch(outside / "linked01_R1.fastq.gz"); touch(outside / "linked01_R2.fastq.gz")
shutil.copytree(base / [d for d in os.listdir(base) if d.startswith("alignment_")][0], outside / "alignment_owl_25-003495-001")
touch(outside / ".provenance" / "exit_code", b"0\n")
os.symlink(outside, s1 / "linked01")
# reads that are links into download/
touch(proj / "download" / "dl01_R1.fastq.gz"); touch(proj / "download" / "dl01_R2.fastq.gz")
(s1 / "dl01").mkdir(); os.symlink(proj / "download" / "dl01_R1.fastq.gz", s1 / "dl01" / "dl01_R1.fastq.gz")
os.symlink(proj / "download" / "dl01_R2.fastq.gz", s1 / "dl01" / "dl01_R2.fastq.gz")
# not started, misfit (no reads), error, running (log only), edited, hidden, scaffold with a workbook
touch(s1 / "notstarted01" / "notstarted01_R1.fastq.gz", b"x" * 60000); touch(s1 / "notstarted01" / "notstarted01_R2.fastq.gz", b"x" * 60000)
(s1 / "misfit01").mkdir(); touch(s1 / "misfit01" / "notes.txt")
err = s1 / "error01"; touch(err / "error01_1.fastq.gz"); touch(err / ".provenance" / "exit_code", b"1\n"); touch(err / "run_step1.log", b"Error: exit 1\n")
touch(s1 / "running01" / "running01_1.fastq.gz"); touch(s1 / "running01" / "run_step1.log", b"== Running\n")
ed = sorted(d for d in s1.iterdir() if d.is_dir() and d.name.startswith("owl-"))[3]
touch(ed / "vcf_edits" / f"{ed.name}_patchlog.jsonl", b"{}\n"); shutil.copy2(next(ed.glob("alignment_*/*_zc.vcf")), ed / "vcf_edits" / f"{ed.name}_zc.vcf.gz")
touch(s1 / ".hidden01" / "hidden_1.fastq.gz"); touch(s1 / "_provenance" / "odd_stats.xlsx")
touch(s1 / "stray_stats.xlsx"); touch(s1 / "stray.txt")
os.symlink("/nonexistent/x", s1 / "danglinglink")
# a staged-only run, and a group with post-hoc outputs, a lock and a legacy posthoc/ folder
run = sorted(d for d in (proj / "step2").iterdir() if d.is_dir() and d.name[:4] == "2026")[-1]
staged = proj / "step2" / "2026-09-11_08-00-00_staged-only"; staged.mkdir()
for i in range(20): shutil.copy2(zc, staged / f"s{i}_zc.vcf")
touch(staged / "run_metadata.json", json.dumps({"started_at": "2026-09-11T08:00:00", "status": "running"}).encode())
g = sorted(d for d in run.iterdir() if d.is_dir() and d.name.startswith("HPAI"))[0]
touch(g / "snp_matrix.csv"); touch(g / "kdp.png"); touch(g / "closest_neighbor.png")
touch(g / "stats.json", b'{"status": "ok", "message": ""}'); touch(g / ".snp_analysis.lock", b"gone-job")
g2 = sorted(d for d in run.iterdir() if d.is_dir() and d.name.startswith("HPAI"))[1]
touch(g2 / "posthoc" / "kdp.png"); touch(g2 / "posthoc" / "stats.json", b'{"status": "error", "message": "boom"}')
touch(g2 / ".hidden.fasta", b">x\nA\n"); os.symlink("/nonexistent/dead.tre", g2 / "dead.tre"); (g2 / "sub").mkdir(); touch(g2 / "sub" / "x.txt")
old = time.time() - 60
for p in sorted(s1.rglob("*"), key=lambda p: -len(p.parts)):
    try: os.utime(p, (old, old), follow_symlinks=False)
    except OSError: pass
print("awkward extras added to", proj)
