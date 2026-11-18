"""Import a MinKNOW run folder as one FASTQ per sample.

vsnp3_step1.py takes one read file per sample and the Inputs page lists
<project>/download/ one file per sample. MinKNOW writes the opposite shape:
fastq_pass/barcodeNN/ with the reads of each barcode split into chunk files
of about 4,000 reads. The barcode-to-sample mapping has always been done
upstream of vsnp3; the lab's existing implementation is in mhc_gui, and this
module lifts its shape (an in-run sample_sheet.csv with barcode and
sample_id columns, barcode names normalised to barcodeNN) without the MHC
amplicon column.

Two phases. ``preview`` scans a run folder and returns, per barcode, the
chunk files found, their total size, and the sample name the sheet proposes.
``run_commit`` (the job the commit route starts) concatenates each chosen
barcode's chunks into <download>/<sample>.fastq.gz. gzip members
concatenate, so the output is a valid single gzip stream and no read is
decompressed. Nothing in the run folder is written.

Layouts recognised, in this order:

1. <run>/fastq_pass/barcodeNN/*.fastq.gz (a MinKNOW output folder), or the
   same under one or two levels of experiment/sample/run folders;
2. <run>/barcodeNN/*.fastq.gz (fastq_pass itself, or a copy of its contents);
3. a flat folder of files whose names carry barcodeNN.

Run as ``python -m app.ont_import commit <plan.json>`` the module performs
the concatenation and prints one line per sample, so it can run as a
background job through the same JobManager as Step 1.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Any recognisable barcode label: barcode07, Barcode7, NB07, BC7, 7. Lifted
# from mhc_gui's validate_sheet.normalize_barcode.
_BARCODE_LOOSE_RE = re.compile(r"^\s*(?:barcode|nb|bc)?\s*0*(\d{1,2})\s*$", re.IGNORECASE)
_BARCODE_DIR_RE = re.compile(r"^barcode(\d{2})$", re.IGNORECASE)
_BARCODE_IN_NAME_RE = re.compile(r"barcode(\d{2})", re.IGNORECASE)
_FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_barcode(value: str) -> Optional[str]:
    """barcodeNN for any recognisable label, or None."""
    m = _BARCODE_LOOSE_RE.match(value or "")
    if not m:
        return None
    n = int(m.group(1))
    if not 1 <= n <= 96:
        return None
    return f"barcode{n:02d}"


def safe_sample_name(value: str) -> str:
    """The on-disk sample name for a sheet sample_id.

    vsnp3 names a sample from everything before the first underscore of its
    read file, so an underscore inside the name would merge samples (see
    step1_staging._sanitized_sample_and_name, which applies the same rule to
    anything already in download/). Underscores become dashes here, at
    import, so the name the user sees in the preview is the name Step 1 will
    use. Other characters outside [A-Za-z0-9.-] become dashes too.
    """
    s = (value or "").strip().replace("_", "-")
    s = _SAFE_NAME_RE.sub("-", s).strip("-.")
    return s


def _fastqs(d: Path) -> List[Path]:
    try:
        return sorted(p for p in d.iterdir()
                      if p.is_file() and p.name.lower().endswith(_FASTQ_SUFFIXES))
    except OSError:
        return []


def _barcode_dirs(d: Path) -> Dict[str, Path]:
    """barcodeNN -> directory, plus 'unclassified' if present, under d."""
    out: Dict[str, Path] = {}
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return out
    for p in entries:
        if not p.is_dir():
            continue
        m = _BARCODE_DIR_RE.match(p.name)
        if m:
            out[f"barcode{int(m.group(1)):02d}"] = p
        elif p.name.lower() == "unclassified":
            out["unclassified"] = p
    return out


def _find_fastq_pass(run_dir: Path, depth: int = 4) -> Optional[Path]:
    """The first fastq_pass folder at or under run_dir, searched breadth-first
    to `depth` levels: MinKNOW writes experiment/sample/run_id/fastq_pass, so
    from the experiment folder it sits four levels down."""
    if run_dir.name == "fastq_pass":
        return run_dir
    level = [run_dir]
    for _ in range(depth):
        nxt: List[Path] = []
        for d in level:
            try:
                kids = sorted(p for p in d.iterdir() if p.is_dir())
            except OSError:
                continue
            for k in kids:
                if k.name == "fastq_pass":
                    return k
                nxt.append(k)
        level = nxt
    return None


def scan_run_dir(run_dir: Path) -> Dict:
    """Barcodes and their chunk files. Returns {layout, source, barcodes:
    {barcodeNN: [paths]}, unclassified: [paths]}; an empty `barcodes` means
    no layout matched."""
    fp = _find_fastq_pass(run_dir)
    if fp is not None:
        dirs = _barcode_dirs(fp)
        if any(k != "unclassified" for k in dirs):
            layout, source = "fastq_pass", fp
        else:
            dirs = {}
    else:
        dirs = {}
    if not dirs:
        dirs = _barcode_dirs(run_dir)
        if any(k != "unclassified" for k in dirs):
            layout, source = "barcode_dirs", run_dir
        else:
            dirs = {}
    barcodes: Dict[str, List[Path]] = {}
    unclassified: List[Path] = []
    if dirs:
        for bc, d in dirs.items():
            files = _fastqs(d)
            if bc == "unclassified":
                unclassified = files
            else:
                barcodes[bc] = files
    else:
        # Flat folder: barcodeNN somewhere in each file name.
        layout, source = "flat", run_dir
        for f in _fastqs(run_dir):
            m = _BARCODE_IN_NAME_RE.search(f.name)
            if m:
                barcodes.setdefault(f"barcode{int(m.group(1)):02d}", []).append(f)
            elif "unclassified" in f.name.lower():
                unclassified.append(f)
    return {"layout": layout if barcodes else "", "source": str(source),
            "barcodes": dict(sorted(barcodes.items())), "unclassified": unclassified}


def read_sample_sheet(run_dir: Path) -> Tuple[Dict[str, str], str, List[str]]:
    """(barcodeNN -> sample_id, sheet file name, problems) from the run folder.

    Accepts sample_sheet*.csv or .tsv (the latest dated name wins, as in
    mhc_gui). Columns `barcode` and `sample_id` are required; every other
    column is ignored. A header is matched case-insensitively.
    """
    cands = [p for p in list(run_dir.glob("sample_sheet*.csv")) + list(run_dir.glob("sample_sheet*.tsv"))
             if p.is_file()]
    if not cands:
        return {}, "", []

    def key(p: Path):
        m = re.search(r"(\d{8})", p.name)
        try:
            return (m.group(1) if m else "00000000", p.stat().st_mtime)
        except OSError:
            return ("00000000", 0.0)

    sheet = max(cands, key=key)
    problems: List[str] = []
    mapping: Dict[str, str] = {}
    try:
        text = sheet.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        return {}, sheet.name, [f"{sheet.name}: cannot be read ({e})"]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}, sheet.name, [f"{sheet.name}: empty"]
    delim = "\t" if lines[0].count("\t") >= lines[0].count(",") else ","
    rows = list(csv.reader(lines, delimiter=delim))
    header = [h.strip().lower() for h in rows[0]]
    try:
        bi, si = header.index("barcode"), header.index("sample_id")
    except ValueError:
        return {}, sheet.name, [f"{sheet.name}: header must contain barcode and sample_id (found: {', '.join(header)})"]
    seen_names: Dict[str, str] = {}
    for n, row in enumerate(rows[1:], start=2):
        if len(row) <= max(bi, si):
            problems.append(f"line {n}: too few columns")
            continue
        raw_bc, sid = row[bi].strip(), row[si].strip()
        bc = normalize_barcode(raw_bc)
        if bc is None:
            problems.append(f"line {n}: unrecognised barcode '{raw_bc}'")
            continue
        if not sid:
            problems.append(f"line {n}: {bc} has no sample_id")
            continue
        if bc in mapping:
            problems.append(f"line {n}: {bc} listed twice")
            continue
        safe = safe_sample_name(sid)
        if not safe:
            problems.append(f"line {n}: sample_id '{sid}' has no usable characters")
            continue
        if safe in seen_names:
            problems.append(f"line {n}: sample '{sid}' collides with {seen_names[safe]} (same name once underscores become dashes)")
            continue
        seen_names[safe] = bc
        mapping[bc] = sid
    return mapping, sheet.name, problems


def preview(run_dir: Path, download_dir: Path) -> Dict:
    """What an import of run_dir would write, before anything is written."""
    scan = scan_run_dir(run_dir)
    sheet, sheet_name, problems = read_sample_sheet(run_dir)

    def size(files: List[Path]) -> int:
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total

    existing = set()
    if download_dir.is_dir():
        existing = {p.name for p in download_dir.iterdir()}

    rows = []
    for bc, files in scan["barcodes"].items():
        sid = sheet.get(bc, "")
        name = safe_sample_name(sid) if sid else bc
        rows.append({
            "barcode": bc,
            "files": len(files),
            "bytes": size(files),
            "sheet_sample_id": sid,
            "sample": name,
            "renamed": bool(sid) and name != sid,
            "exists": f"{name}.fastq.gz" in existing,
        })
    for bc in sorted(set(sheet) - set(scan["barcodes"])):
        problems.append(f"{bc} ({sheet[bc]}) is in the sheet but has no reads in the run")
    unc = scan["unclassified"]
    return {
        "run_dir": str(run_dir),
        "layout": scan["layout"],
        "source": scan["source"],
        "sheet": sheet_name,
        "sheet_problems": problems,
        "rows": rows,
        "unclassified": {"files": len(unc), "bytes": size(unc), "exists": "unclassified.fastq.gz" in existing},
    }


def build_plan(run_dir: Path, download_dir: Path, samples: List[Dict[str, str]],
               include_unclassified: bool) -> Tuple[List[Dict], List[str]]:
    """Resolve the user's (barcode, sample) choices against the run folder.
    Returns (plan entries, errors). Nothing is written."""
    scan = scan_run_dir(run_dir)
    errors: List[str] = []
    plan: List[Dict] = []
    seen: Dict[str, str] = {}
    for s in samples:
        bc = normalize_barcode(str(s.get("barcode", "")))
        if bc is None:
            errors.append(f"unrecognised barcode '{s.get('barcode', '')}'")
            continue
        files = scan["barcodes"].get(bc)
        if not files:
            errors.append(f"{bc}: no reads in the run")
            continue
        name = safe_sample_name(str(s.get("sample", ""))) or bc
        if name in seen:
            errors.append(f"{bc} and {seen[name]} would both be named {name}")
            continue
        seen[name] = bc
        plan.append({"barcode": bc, "sample": name, "files": [str(f) for f in files],
                     "out": str(download_dir / f"{name}.fastq.gz")})
    if include_unclassified:
        if scan["unclassified"]:
            plan.append({"barcode": "unclassified", "sample": "unclassified",
                         "files": [str(f) for f in scan["unclassified"]],
                         "out": str(download_dir / "unclassified.fastq.gz")})
        else:
            errors.append("no unclassified reads in the run")
    for e in plan:
        if Path(e["out"]).exists():
            errors.append(f"{Path(e['out']).name} is already in download/; remove it first or choose another name")
    return plan, errors


def run_commit(plan_path: Path) -> int:
    """Concatenate each planned sample's chunks into its output. Writes to a
    .part file first and renames on completion, so a killed job never leaves
    a truncated file that looks finished."""
    plan = json.loads(plan_path.read_text())
    failed = 0
    for e in plan:
        out = Path(e["out"])
        part = out.with_name(out.name + ".part")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            n = 0
            with open(part, "wb") as dst:
                for f in e["files"]:
                    with open(f, "rb") as src:
                        n += shutil.copyfileobj(src, dst, 1024 * 1024) or 0
            part.replace(out)
            print(f"{e['barcode']} -> {out.name}: {len(e['files'])} files, {out.stat().st_size} bytes", flush=True)
        except OSError as exc:
            failed += 1
            print(f"{e['barcode']} -> {out.name}: FAILED ({exc})", flush=True)
            try:
                part.unlink()
            except OSError:
                pass
    print(f"done: {len(plan) - failed} written, {failed} failed", flush=True)
    return 1 if failed else 0


SAMPLE_SHEET_TEMPLATE = (
    "barcode,sample_id\n"
    "barcode01,SAMPLE_1\n"
    "barcode02,SAMPLE_2\n"
    "barcode03,NEG_CONTROL\n"
)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "commit":
        sys.exit(run_commit(Path(sys.argv[2])))
    print("usage: python -m app.ont_import commit <plan.json>", file=sys.stderr)
    sys.exit(2)
