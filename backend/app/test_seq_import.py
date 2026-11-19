"""Importing sequences that are not reads (app.seq_import).

What these pin:
  * a GenBank flat file is parsed without Biopython: accession, length,
    definition, the source feature (country before the colon of
    /geo_loc_name, the year of /collection_date, host, isolate) and the sequence;
  * a NanoFMDV-style header and a sidecar sheet fill the metadata; a bare
    header gives a name with underscores turned to dashes;
  * a VP1-length record is taken whole when the panel hit covers it, a genome
    has VP1 cut out by the context builder's own slicer, a short record and a
    record with no panel hit are refused with a reason;
  * a sample lands in step1/<name>/ with the four files the typer and the
    tree read, a context record is appended to fmdv_context/<SEROTYPE>.*;
  * a name already in step1/ and an accession already in the library or the
    project refuse the plan;
  * the fetch caches every record before use and reports a missing one per
    accession, and the accession parser drops versions and junk.

    python3 backend/app/test_seq_import.py   (no BLAST+ needed: blastn is faked)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import seq_import as si  # noqa: E402

ctx = si.ctx
FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


# A 657-nt "VP1" template and a second one 10% different, as the panel.
import random  # noqa: E402
random.seed(7)
VP1_A = "".join(random.choice("ACGT") for _ in range(657))
VP1_B = list(VP1_A)
for i in range(0, 657, 10):
    VP1_B[i] = "ACGT"[("ACGT".index(VP1_B[i]) + 1) % 4]
VP1_B = "".join(VP1_B)
FLANK5 = "".join(random.choice("ACGT") for _ in range(3000))
FLANK3 = "".join(random.choice("ACGT") for _ in range(4500))
GENOME = FLANK5 + VP1_A + FLANK3          # VP1 at 3001..3657
PANEL = [("SAT1-I_KM268899", VP1_A), ("O-EA-3_MN000001", VP1_B)]


def fake_blast(blastn, queries, subjects, threads, tag):
    """What blastn would say for these sequences: a query hits a subject when
    the subject is a substring of it or equal to it. Columns as blast_best_hits
    reads them: qseqid sseqid pident length qstart qend sstart send qlen slen bitscore."""
    best = {}
    for qn, q in queries:
        for sn, s in subjects:
            pos = q.find(s)
            if pos < 0:
                if q == s[:len(q)]:        # a partial record over the start of the subject
                    best[qn] = [qn, sn, "100.0", str(len(q)), "1", str(len(q)), "1", str(len(q)), str(len(q)), str(len(s)), "900"]
                continue
            best[qn] = [qn, sn, "100.0", str(len(s)), str(pos + 1), str(pos + len(s)), "1", str(len(s)),
                        str(len(q)), str(len(s)), "1200"]
            break
    return best


si.blast_best_hits = fake_blast
ctx.blast_best_hits = fake_blast

GB = """LOCUS       MN000002                7000 bp    RNA     linear   VRL 01-JAN-2020
DEFINITION  Foot-and-mouth disease virus - type SAT 1 isolate ETH/1/2019,
            complete genome.
ACCESSION   MN000002
VERSION     MN000002.1
FEATURES             Location/Qualifiers
     source          1..7000
                     /organism="Foot-and-mouth disease virus - type SAT 1"
                     /mol_type="genomic RNA"
                     /isolate="ETH/1/2019"
                     /host="Bos taurus"
                     /geo_loc_name="Ethiopia: Oromia"
                     /collection_date="12-Mar-2019"
                     /note="serotype SAT1; topotype
                     I"
     CDS             100..6900
                     /product="polyprotein"
ORIGIN
"""


def gb_record(seq: str) -> str:
    lines = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        lines.append(f"{i + 1:>9} " + " ".join(chunk[j:j + 10].lower() for j in range(0, len(chunk), 10)))
    return GB + "\n".join(lines) + "\n//\n"


def make_lib(root: Path) -> Path:
    lib = root / "lib"
    (lib / "panels").mkdir(parents=True)
    (lib / "panels" / "vp1.fasta").write_text("".join(f">{n}\n{s}\n" for n, s in PANEL))
    (lib / "vp1_context").mkdir()
    (lib / "vp1_context" / "SAT1.fasta").write_text(f">SAT1-II_AB111111\n{VP1_A}\n")
    (lib / "vp1_context" / "SAT1.tsv").write_text(
        "\t".join(ctx.TSV_COLUMNS) + "\n" + "\t".join(
            ["SAT1-II_AB111111", "SAT1", "II", "KENYA", "2010", "BOVINE", "AB111111", "657", "K/1/2010", "vp1_record"]
            + [""] * 7) + "\n")
    (lib / "lineages.json").write_text(json.dumps({"genomes": [{"safe": "SAT1-I_KM268899", "accession": "KM268899"}]}))
    return lib


def test_genbank_parsing():
    recs = si.parse_genbank_text(gb_record(GENOME[:7000]))
    check(len(recs), 1, "one record")
    r = recs[0]
    check(r["accession"], "MN000002", "accession")
    check(r["version"], "MN000002.1", "version")
    check(r["length"], 7000, "LOCUS length")
    check(r["definition"].endswith("complete genome."), True, "definition joined across lines")
    check(len(r["sequence"]), 7000, "sequence length")
    check(r["sequence"], GENOME[:7000], "sequence upper-cased from ORIGIN")
    m = si.genbank_metadata(r)
    check(m["country"], "Ethiopia", "country before the colon")
    check(m["year"], "2019", "year from collection_date")
    check(m["host"], "Bos taurus", "host")
    check(m["isolate"], "ETH/1/2019", "isolate")
    check(m["serotype"], "SAT1", "serotype from the definition when /serotype is absent")
    check(m["topotype"], "", "lineage is never inferred from the note")
    check(si.is_genbank_text("  LOCUS x"), True, "flat file recognised")
    check(si.is_genbank_text(">x\nACGT"), False, "fasta is not a flat file")


def test_fasta_and_sidecar():
    fa = f">SAT1|IX|ETHIOPIA|2007|BOVINE|FJ798154|639|ETH/3/2007_G001\n{VP1_A}\n>my_sample_1 some words\n{VP1_B}\n>MN000003.1 Foot-and-mouth\n{VP1_A}\n"
    side, probs = si.read_sidecar_tsv("sample\trole\tlineage\tcountry\tyear\nmy-sample-1\tcontext\tEA-3\tKenya\t2021\n")
    check(probs, [], "sidecar parses")
    recs = si.records_from_fasta(fa, "x.fasta", side)
    check([r["name"] for r in recs], ["ETH-3-2007-G001", "my-sample-1", "MN000003"], "names: isolate, dashed header, bare accession")
    check(recs[0]["meta"]["country"], "ETHIOPIA", "labelled header country")
    check(recs[0]["meta"]["topotype"], "IX", "labelled header topotype")
    check(recs[1]["meta"]["topotype"], "EA-3", "sidecar lineage")
    check(recs[1]["meta"]["year"], "2021", "sidecar year")
    check(recs[1]["role"], "context", "sidecar role")
    check(recs[2]["meta"]["accession"], "MN000003", "bare accession header, version dropped")
    _, probs = si.read_sidecar_tsv("name\tcountry\nx\ty\n")
    check(bool(probs), True, "sheet without a sample column is reported")
    check(si.parse_accessions("MN000002.1, kc440883\nnotanacc  MN000002"), (["MN000002", "KC440883"], ["notanacc"]),
          "accessions parsed, versions dropped, duplicates collapsed, junk reported")
    check(si.classify(300), "short", "classify short")
    check(si.classify(657), "vp1", "classify vp1")
    check(si.classify(1800), "P1", "classify P1")
    check(si.classify(8100), "genome", "classify genome")
    check(si.classify(900), "other", "classify other")


def test_locate_preview_commit():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lib = make_lib(root)
        project = root / "proj"
        (project / "step1" / "EXISTING").mkdir(parents=True)
        (project / "download").mkdir()
        (project / "project.json").write_text(json.dumps({"mode": "fmdv"}))
        fa = (f">lab_consensus_1\n{GENOME}\n"          # genome: slice
              f">vp1only\n{VP1_B}\n"                  # VP1 record: whole
              f">short\n{VP1_A[:300]}\n"              # refused: short
              f">EXISTING\n{VP1_A}\n"                 # refused at commit: name in step1/
              f">notfmdv\n{'ACGT' * 200}\n")          # refused: no panel hit
        recs = si.records_from_fasta(fa, "lab.fasta")
        gb = si.records_from_genbank(gb_record(GENOME[:7000]), "MN000002")
        gb[0]["role"] = "context"
        pv = si.preview(recs + gb, project, lib, "sample", blastn="/fake/blastn")
        rows = {r["name"]: r for r in pv["rows"]}
        check(sorted(rows), ["EXISTING", "MN000002", "lab-consensus-1", "notfmdv", "short", "vp1only"], "one row per record")
        g = rows["lab-consensus-1"]
        check(g["class"], "genome", "genome classified")
        check(g["serotype"], "SAT1", "serotype from the panel hit")
        check((g["vp1"]["start"], g["vp1"]["end"], g["vp1"]["length"]), (3001, 3657, 657), "VP1 sliced at its coordinates")
        check(g["vp1"]["region_source"], "imported_sliced_from_genome", "slice provenance")
        check(g["problem"], "", "genome has no problem")
        v = rows["vp1only"]
        check(v["serotype"], "O", "VP1 record's serotype from its panel hit")
        check(v["vp1"]["region_source"], "imported_vp1", "VP1 record taken whole")
        check("under 600" in rows["short"]["problem"], True, "short record refused")
        check("no hit" in rows["notfmdv"]["problem"], True, "non-FMDV record refused")
        check(rows["EXISTING"]["exists"], "name already in step1/", "existing name flagged in the preview")
        c = rows["MN000002"]
        check((c["role"], c["country"], c["year"], c["accession"]), ("context", "Ethiopia", "2019", "MN000002"), "GenBank row")
        check(c["vp1"]["start"], 3001, "GenBank genome sliced")

        # Commit: the genome as a sample, the VP1 record as a sample, the
        # GenBank record as context; the refused rows and the collision.
        edited = [dict(r, lineage=("I" if r["name"] == "lab-consensus-1" else r["lineage"]),
                       country=("Ethiopia" if r["name"] == "lab-consensus-1" else r["country"]))
                  for r in pv["rows"] if r["name"] in ("lab-consensus-1", "vp1only", "MN000002", "EXISTING", "short")]
        plan, errors = si.build_plan(pv["token"], edited, project, lib)
        check(any("EXISTING is already in step1/" in e for e in errors), True, "name collision refused")
        check(any("no VP1" in e for e in errors), True, "a refused row cannot be committed")
        ok_rows = [r for r in edited if r["name"] in ("lab-consensus-1", "vp1only", "MN000002")]
        plan, errors = si.build_plan(pv["token"], ok_rows, project, lib)
        check(errors, [], "plan builds")
        check([e["role"] for e in plan["entries"]], ["sample", "sample", "context"], "roles kept")
        check(plan["entries"][2]["name"], "SAT1-NA_MN000002", "context record named like the collection")
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan))
        rc = si.run_commit(plan_path)
        check(rc, 0, "commit succeeds")
        d = project / "step1" / "lab-consensus-1"
        for f in ("lab-consensus-1_imported.fasta", "lab-consensus-1_VP1.fasta", "fmdv_import.json", "fmdv_consensus.json"):
            check((d / f).is_file(), True, f"{f} written")
        cons = json.loads((d / "fmdv_consensus.json").read_text())
        check((cons["source"], cons["serotype"], cons["reference"], cons["vp1_length"], cons["vp1_called_percent"]),
              ("imported", "SAT1", "", 657, 100.0), "fmdv_consensus.json for the tree")
        imp = json.loads((d / "fmdv_import.json").read_text())
        check(imp["metadata"]["country"], "Ethiopia", "edited metadata kept")
        check(imp["metadata"]["topotype"], "I", "edited lineage kept")
        vp1 = (d / "lab-consensus-1_VP1.fasta").read_text().splitlines()
        check("".join(vp1[1:]), VP1_A, "VP1 FASTA holds the slice")
        check(list((project / "step1").glob("*/*.fastq.gz")), [], "no reads inside step1/ folders")
        # A genome becomes simulated paired reads in download/; a VP1 record does not.
        check(imp["reads"], "simulated", "genome import marked simulated")
        check(sorted(p.name for p in (project / "download").glob("lab-consensus-1_R*.fastq.gz")),
              ["lab-consensus-1_R1.fastq.gz", "lab-consensus-1_R2.fastq.gz"], "paired reads written for the genome")
        check(list((project / "download").glob("vp1only*")), [], "no reads for a VP1 record")
        check(json.loads((project / "step1" / "vp1only" / "fmdv_import.json").read_text())["reads"], "none", "VP1 record marked no reads")
        check(imp["simulated_reads"]["covered_bases"], len(GENOME), "covered bases reported")
        tsv = (project / "fmdv_context" / "SAT1.tsv").read_text().splitlines()
        check(tsv[0].split("\t")[:3], ["name", "serotype", "topotype"], "context TSV header")
        row = dict(zip(tsv[0].split("\t"), tsv[1].split("\t")))
        check((row["name"], row["topotype"], row["country"], row["year"], row["accession"], row["region_source"]),
              ("SAT1-NA_MN000002", "NA", "Ethiopia", "2019", "MN000002", "imported_sliced_from_genome"), "context TSV row")
        check((project / "fmdv_context" / "SAT1.fasta").read_text().startswith(">SAT1-NA_MN000002\n"), True, "context FASTA")
        check(si.project_context_counts(project), {"SAT1": 1}, "project context counted")
        check((project / ".seq_import" / f"{pv['token']}.json").exists(), False, "stash removed after commit")

        # A second import of the same accession is refused; so is a library one.
        pv2 = si.preview(si.records_from_genbank(gb_record(GENOME[:7000]), "MN000002"), project, lib, "context", blastn="/fake/blastn")
        check("already in this project's context" in pv2["rows"][0]["in_context"], True, "project duplicate flagged")
        plan, errors = si.build_plan(pv2["token"], pv2["rows"], project, lib)
        check(any("already in this project" in e for e in errors), True, "project duplicate refused")
        r3 = si.records_from_fasta(f">KM268899\n{VP1_A}\n", "p.fasta")
        pv3 = si.preview(r3, project, lib, "context", blastn="/fake/blastn")
        check("already in the library" in pv3["rows"][0]["in_context"], True, "library accession flagged")
        plan, errors = si.build_plan(pv3["token"], pv3["rows"], project, lib)
        check(any("already in the library" in e for e in errors), True, "library accession refused as context")
        as_sample = [dict(pv3["rows"][0], role="sample", name="published-isolate")]
        plan, errors = si.build_plan(pv3["token"], as_sample, project, lib)
        check(errors, [], "the same accession is accepted as a sample")
        check(plan["entries"][0]["name"], "published-isolate", "sample keeps its name")

        # No blastn: every record is refused with the reason, nothing crashes.
        pv4 = si.preview(si.records_from_fasta(f">x\n{VP1_A}\n", "x.fasta"), project, lib, "sample", blastn=None)
        check("blastn" in pv4["rows"][0]["problem"], True, "missing blastn is the stated problem")


def test_simulator_skips_n():
    import gzip
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 2,000 called bases, a 30-N mask, 1,500 called bases, a short 100-base island after more N
        seq = GENOME[:2000] + "N" * 30 + GENOME[2000:3500] + "N" * 5 + GENOME[3500:3600]
        r1, r2 = root / "x_R1.fastq.gz", root / "x_R2.fastq.gz"
        stats = si.simulate_reads(seq, "x", r1, r2, depth=20)
        check(stats["covered_bases"], 3500, "only the two long segments are covered")
        check(stats["segments_too_short"], 1, "the 100-base island is skipped")
        with gzip.open(r1, "rt") as f1, gzip.open(r2, "rt") as f2:
            a = f1.read().splitlines(); b = f2.read().splitlines()
        reads1 = a[1::4]; reads2 = b[1::4]
        check(len(reads1), stats["pairs"], "R1 count")
        check(len(reads2), stats["pairs"], "R2 count")
        check(all("N" not in r for r in reads1 + reads2), True, "no read carries an N")
        check(all(len(r) <= 250 for r in reads1), True, "read length")
        check(a[0].startswith("@SIMULATED:x:"), True, "header says simulated")
        # Every R1 is a substring of a called segment, never spanning the mask.
        segs = [GENOME[:2000], GENOME[2000:3500]]
        check(all(any(r in sg for sg in segs) for r in reads1), True, "reads lie within called segments")
        rc = str.maketrans("ACGT", "TGCA")
        check(all(any(r.translate(rc)[::-1] in sg for sg in segs) for r in reads2), True, "R2 is the reverse complement of called sequence")
        # depth about right: pairs * 2 * 250 / 3500 ~ 20
        check(abs(stats["pairs"] * 500 / 3500 - 20) < 1.0, True, "depth near the target")
        # deterministic
        stats2 = si.simulate_reads(seq, "x", root / "y_R1.fastq.gz", root / "y_R2.fastq.gz", depth=20)
        check(gzip.open(root / "y_R1.fastq.gz", "rt").read() == gzip.open(r1, "rt").read(), True, "same name, same reads")


def test_fetch_cache_and_errors():
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "cache"
        calls = []

        def fetch(ids, email, key):
            calls.append(list(ids))
            return "".join(gb_record(GENOME[:7000]).replace("MN000002", a) for a in ids if a != "MN999999")

        res = si.fetch_genbank(["MN000002", "MN999999"], cache, fetch=fetch, delay=0)
        check("path" in res["MN000002"], True, "fetched record cached")
        check((cache / "MN000002.gb").is_file(), True, "cache file written")
        check("error" in res["MN999999"], True, "missing record reported")
        res2 = si.fetch_genbank(["MN000002"], cache, fetch=fetch, delay=0)
        check(res2["MN000002"]["cached"], True, "second call served from the cache")
        check(len(calls), 1, "no second network call")

        def dead(ids, email, key):
            raise OSError("no route to host")
        res3 = si.fetch_genbank(["MN000004"], cache, fetch=dead, delay=0)
        check("no route" in res3["MN000004"]["error"], True, "network error reported per accession")


def test_library_lookup():
    with tempfile.TemporaryDirectory() as td:
        lib = make_lib(Path(td))
        where = si.library_accessions(lib)
        check(where["KM268899"], "panel genome SAT1-I_KM268899", "panel accession known")
        check(where["AB111111"].startswith("library context"), True, "context accession known")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILS:
        print(f"{len(FAILS)} failure(s):")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("test_seq_import: all checks passed")
