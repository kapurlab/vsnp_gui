#!/usr/bin/env python3
"""
An SRA download fills the project's metadata sheet when the reads are in.

bin/sample_metadata.py keeps <project>/sample_metadata.tsv: a `sample` column
and any other columns, filled from sources (public run records, a sheet the lab
already has) without ever overwriting a value that is already there. The SRA
download runs its `sra` source for the runs it fetched.

What is pinned here:
  * the step runs INSIDE the generated download script, before the script's
    own `exit`. The script ends in an explicit exit, so a step appended after
    it would never run; that is checked by running the real generated script;
  * the step cannot change the download's exit code, even when it fails;
  * paths and accessions are quoted, and no step is emitted for no accessions;
  * the NCBI contact email and API key travel in the job's environment and are
    not written into the script;
  * merging keeps what the sheet already holds, fills blanks, and reports a
    value the source disagrees with;
  * the lookup itself, against a saved record and with no network.
Run from anywhere: sys.path is derived from __file__.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))
from app import main as backend_main  # noqa: E402
from app.sra import build_download_script  # noqa: E402
import sample_metadata as sm  # noqa: E402

BIOSAMPLE = """<BioSampleSet><BioSample accession="SAMN00000001">
<Ids><Id db="BioSample" is_primary="1">SAMN00000001</Id><Id db_label="Sample name">LAB-7</Id><Id db="SRA">SRS1</Id></Ids>
<Description><Organism taxonomy_name="Mycobacterium bovis"/></Description>
<Attributes>
<Attribute attribute_name="strain" harmonized_name="strain">LAB-7</Attribute>
<Attribute attribute_name="geo_loc_name" harmonized_name="geo_loc_name">India: Karnataka, Bengaluru</Attribute>
<Attribute attribute_name="host" harmonized_name="host">missing</Attribute>
<Attribute attribute_name="Herd size">120</Attribute>
</Attributes></BioSample></BioSampleSet>"""


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)


def fake_get(url: str) -> str:
    if "ebi.ac.uk" in url:
        return json.dumps([{"run_accession": "SRR1234567", "sample_accession": "SAMN00000001",
                            "instrument_model": "Illumina MiSeq", "library_layout": "PAIRED",
                            "base_count": "1000", "read_count": "10"}])
    if "esearch.fcgi" in url:
        return json.dumps({"esearchresult": {"idlist": ["1"]}})
    if "efetch.fcgi" in url:
        return BIOSAMPLE
    raise AssertionError(url)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sra_meta_test_"))
    try:
        # --- the step sits before the script's exit, and really runs -------
        print("[the step runs inside the download script]")
        dl = tmp / "dl"; dl.mkdir()
        script = build_download_script(dl, ["SRR0000001"], False,
                                       after_download="touch ran_after\nfalse || true\n")
        check(script.index("touch ran_after") < script.rindex("exit 0"),
              "the step is placed before the script's final exit")
        path = dl / "download_sra.sh"; path.write_text(script, encoding="utf-8")
        # Every downloader fails at once and nothing sleeps, so the script runs
        # to its summary in a moment and the one accession counts as failed.
        fake = tmp / "fakebin"; fake.mkdir()
        for tool, rc in (("curl", 1), ("wget", 1), ("fasterq-dump", 1), ("prefetch", 1),
                         ("enaDataGet", 1), ("sleep", 0)):
            (fake / tool).write_text(f"#!/bin/sh\nexit {rc}\n"); (fake / tool).chmod(0o755)
        env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"}
        proc = subprocess.run(["bash", str(path)], cwd=dl, capture_output=True, text=True,
                              timeout=180, env=env)
        check((dl / "ran_after").is_file(), f"the step ran: {proc.stdout[-300:]} {proc.stderr[-300:]}")
        check(proc.returncode == 1,
              f"the download's own failure is still the exit code, step or no step (rc={proc.returncode})")
        plain = build_download_script(dl, ["SRR0000001"], False)
        check("ran_after" not in plain and plain.count("exit 0") == script.count("exit 0"),
              "with no step the script is as it was")

        # --- a third file of unpaired reads is named in the summary --------
        print("[unpaired reads beside a pair are named]")
        have = tmp / "have"; have.mkdir()
        for name in ("SRR0000002_1.fastq.gz", "SRR0000002_2.fastq.gz", "SRR0000002.fastq.gz"):
            (have / name).write_bytes(b"")
        (have / "download_sra.sh").write_text(build_download_script(have, ["SRR0000002"], False),
                                              encoding="utf-8")
        proc = subprocess.run(["bash", str(have / "download_sra.sh")], cwd=have, capture_output=True,
                              text=True, timeout=180, env=env)
        check(proc.returncode == 0, f"reads already here, so the run succeeds (rc={proc.returncode})")
        check("not used by Step 1: SRR0000002.fastq.gz" in proc.stdout,
              f"the summary names the unpaired file: {proc.stdout[-300:]}")
        check("Reads with no mate" not in subprocess.run(
            ["bash", str(path)], cwd=dl, capture_output=True, text=True, timeout=180, env=env).stdout,
            "a run with no such file gets no note")

        # --- what the backend puts there ---------------------------------
        print("[the backend's step]")
        project = tmp / "my project"; (project / "download").mkdir(parents=True)
        step = backend_main._sample_metadata_step(project, ["SRR1234567", "ERR7654321"])
        check("sample_metadata.py" in step and "fetch --source sra" in step, f"runs the sra source: {step}")
        check(f"'{project}'" in step, "a project path with a space is quoted")
        check("SRR1234567 ERR7654321" in step, "names the runs that were downloaded")
        check(step.rstrip().endswith("|| true"), "a failed lookup cannot fail the job")
        check(backend_main._sample_metadata_step(project, []) == "", "no runs, no step")

        print("[the real step, in a real shell, for a sample that is not a run]")
        step = backend_main._sample_metadata_step(project, ["farm12"])
        proc = subprocess.run(["bash", "-c", step], capture_output=True, text=True, timeout=120)
        check(proc.returncode == 0, f"exits 0: {proc.stderr[-300:]}")
        check(not (project / sm.SHEET_NAME).exists(), "nothing to look up, so no sheet is written")

        print("[the NCBI contact travels in the environment only]")
        env = backend_main._ncbi_contact_env({"ncbi": {"email": "lab@example.org", "api_key": "k123"}})
        check(env == {"NCBI_EMAIL": "lab@example.org", "NCBI_API_KEY": "k123"}, f"from the config: {env}")
        check("lab@example.org" not in step and "k123" not in step, "neither is written into the script")

        # --- the lookup, with no network ---------------------------------
        print("[public record -> sheet]")
        for name in ("SRR1234567_1.fastq.gz", "SRR1234567_2.fastq.gz", "farm12_R1.fastq.gz"):
            (project / "download" / name).write_text("")
        result = sm.fetch(project, "sra", http_get=fake_get, log=lambda _m: None)
        check(result["applicable"] == 1 and result["fetched"] == 1, f"one run looked up: {result}")
        header, rows = sm.load_sheet(project)
        row = rows["SRR1234567"]
        check(row["strain"] == "LAB-7" and row["organism"] == "Mycobacterium bovis", f"record attributes: {row}")
        check(row["geo_country"] == "India" and row["geo_region"] == "Karnataka", "location split into country and region")
        check(row["herd_size"] == "120", "an attribute NCBI has no standard name for is kept under the submitter's")
        check("host" not in header, 'a "missing" placeholder is not a value')
        check("farm12" not in rows, "a locally named sample is not looked up")

        print("[what is in the sheet stays]")
        rows["SRR1234567"]["strain"] = "our-name"
        sm.save_sheet(project, header, rows)
        result = sm.fetch(project, "sra", http_get=fake_get, log=lambda _m: None)
        _, rows = sm.load_sheet(project)
        check(rows["SRR1234567"]["strain"] == "our-name", "a value in the sheet is not overwritten")
        check(any(c["column"] == "strain" and c["offered"] == "LAB-7" for c in result["conflicts"]),
              "the differing public value is reported")

        print("[a sheet the lab already has]")
        lab = tmp / "lims.csv"
        lab.write_text("Lab ID,Reads,Herd\nL-9,farm12,H9\nL-1,SRR1234567,H7\nL-0,not-here,H0\n")
        result = sm.import_sheet(project, lab, log=lambda _m: None)
        _, rows = sm.load_sheet(project)
        check(result["key_column"] == "Reads" and result["unmatched_rows"] == 1,
              f"the sample column is found by matching: {result['key_column']}")
        check(rows["farm12"]["Herd"] == "H9" and rows["SRR1234567"]["Herd"] == "H7", "its columns are merged in")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
