"""In FMDV mode, the reads vsnp3 is given are the primer-clipped ones.

The Step 1 batch chose the reference, clipped the primers, set R1 to the
clipped file, wrote fmdv_clip.json saying so, and then ran vsnp3 on the
UNCLIPPED reads: the vsnp3 command had been assembled, with R1 in it, a few
lines earlier. Every FMDV sample since the mode was written ran unclipped
while its folder said otherwise (found 2026-11-20 from a run's own provenance:
`-r1 SRR34371563.fastq.gz` beside a 59 MB clipped file nobody read).

This writes a real batch script through the dispatch and reads its order, and
checks the two cases the clip is not for: paired reads and short reads.

Run directly:  python test_fmdv_clip_reaches_vsnp3.py   (from backend/)
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))


def assert_true(cond, label):
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def write_fastq(path: Path, n: int = 4, length: int = 50) -> None:
    with gzip.open(path, "wt") as fh:
        for i in range(n):
            fh.write(f"@r{i}\n{'A' * length}\n+\n{'I' * length}\n")
    # past the junk-size floor (step1_min_fastq_bytes)
    with open(path, "ab") as fh:
        fh.write(gzip.compress(os.urandom(70 * 1024)))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp).resolve()
        os.environ["XDG_CONFIG_HOME"] = str(t / "config")
        os.environ["VSNP_GUI_SITE_ROOT"] = str(t / "site")
        os.environ["HOME"] = str(t / "home")
        (t / "home").mkdir()
        lib = t / "site" / "refs" / "fmdv"
        (lib / "reference_options").mkdir(parents=True)
        (lib / "primers").mkdir()
        (lib / "primers" / "nanofmdv_L.fasta").write_text(">p1_LEFT\nACGTACGTACGTACGTACGT\n")
        (lib / "lineages.json").write_text(json.dumps({"genomes": [], "tree": {}}))
        projects = t / "projects"
        proj = projects / "P"
        ont = proj / "step1" / "ONT1"
        pair = proj / "step1" / "PAIR1"
        ont.mkdir(parents=True); pair.mkdir(parents=True)
        write_fastq(ont / "ONT1.fastq.gz", length=900)
        write_fastq(pair / "PAIR1_R1.fastq.gz"); write_fastq(pair / "PAIR1_R2.fastq.gz")
        (proj / "project.json").write_text(json.dumps({"name": "P", "mode": "fmdv", "fmdv_scope": "FMDV"}))

        import app.main as m
        cfg = m.load_config()
        cfg["projects_root"] = str(projects)
        m.save_config(cfg)
        started = []

        def fake_start(**kw):
            started.append(kw)
            return "job-1"
        m.job_manager.start_job = fake_start
        try:
            m.step1_run("P", m.Step1Request(fmdv_scope="FMDV", force_rerun=True))
        except Exception as exc:  # noqa: BLE001
            # A machine with no vsnp3 at all refuses before any script exists.
            if not (proj / "step1" / "run_step1.sh").is_file():
                print(f"  SKIP  the dispatch wrote no script here ({getattr(exc, 'detail', exc)})")
                return 0
        script = (proj / "step1" / "run_step1.sh").read_text()
        lines = script.splitlines()

        def at(needle: str) -> int:
            hits = [i for i, l in enumerate(lines) if needle in l]
            assert_true(len(hits) == 1, f"one line has: {needle}")
            return hits[0]

        print("the order inside one sample")
        clip = at('R1="$CLIPPED"')
        run_args = at('RUN_ARGS=(')
        np_detect = at('NP="--nanopore"')
        assert_true(clip < run_args, "R1 becomes the clipped reads before the vsnp3 command reads R1")
        assert_true(np_detect < clip, "and after the read type is known")
        assert_true('-r1 "$R1"' in lines[run_args], "the command takes R1 as it stands at that point")

        print("the clip is for long single-file reads")
        gate = next(l for l in lines if "FMDV_PRIMERS" in l and l.strip().startswith("if "))
        assert_true('[ -n "$NP" ]' in gate and '[ -z "$R2" ]' in gate,
                    "paired and short reads are not clipped")
        skip = lines[at("primers are clipped from long single-file reads only")]
        assert_true("tee -a" in skip, "and the sample's log says they went to vSNP3 as they are")
        assert_true(script.count("rm -f fmdv_clip.json") == 2,
                    "a clip record from an earlier run is removed when this run did not use a clip")
    print("test_fmdv_clip_reaches_vsnp3: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
