#!/usr/bin/env python3
"""Tables for a VP1 tree run: each sample's neighbourhood, and the variable
sites of VP1 at the nucleotide and amino acid level, sorted and in cascade.

Reads a run folder written by fmdv_vp1_tree.py (VP1/VP1_aligned.fasta, the
Newick, vp1_tree.json, VP1_context_selection.tsv) and the library's context
TSVs, and writes beside them:

  VP1/VP1_neighbours.json, VP1_neighbours.tsv
      per sample, the records within NEAR_PCT identity of its nearest, at
      most CAP and at least FLOOR, sorted by identity, with lineage,
      country, year and host. The nearest is the call a report quotes
      (NanoFMDV reports exactly that one line); the list is the sample's
      relatives, as a GenBank hit list reads.
  VP1/VP1_sorted_table_nt.xlsx, VP1_cascade_table_nt.xlsx
  VP1/VP1_sorted_table_aa.xlsx, VP1_cascade_table_aa.xlsx
      rows: the samples and the union of their neighbourhoods, in tree
      order; columns: the sites that vary among those rows. Sorted: columns
      in VP1 order. Cascade: columns ordered by how many rows carry the
      minority state, most first, so shared differences read as blocks and
      private ones trail, which is how vsnp3's cascade tables read.
      Amino acids from the VP1 frame (the panel VP1s are whole ORFs; the
      alignment keeps that frame per row once gaps are removed).
  VP1/vp1_tables.json
      what was written, with the counts.

Site numbering is the sample's own VP1 (1-based, gaps removed), the first
sample in tree order setting the scale, with the alignment column beside
it. Never fails the tree run: errors are written to vp1_tables.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

NEAR_PCT = 3.0
CAP = 20
FLOOR = 5

CODON = {}
_bases = "TCAG"
_aa = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
for _i, _a in enumerate(_aa):
    CODON[_bases[_i // 16] + _bases[(_i // 4) % 4] + _bases[_i % 4]] = _a


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_fasta(path: Path) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    name, buf = None, []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if name is not None:
                out.append((name, "".join(buf)))
            name, buf = line[1:].strip().split()[0], []
        else:
            buf.append(line.strip().upper())
    if name is not None:
        out.append((name, "".join(buf)))
    return out


def newick_leaves(text: str) -> List[str]:
    """Leaf names in the order the Newick lists them, which is the order a
    viewer draws them (top to bottom), quotes and branch lengths dropped."""
    names = []
    for tok in re.findall(r"[(,]\s*'?([^'(),:;]+)'?\s*(?=[:,)])", text):
        tok = tok.strip()
        if tok and not re.fullmatch(r"[-0-9.eE]+", tok):
            names.append(tok)
    return names


def identity(a: str, b: str) -> Tuple[float, int]:
    """Percent identity over columns where both have a base (gaps and N skipped)."""
    same = n = 0
    for x, y in zip(a, b):
        if x in "ACGT" and y in "ACGT":
            n += 1
            if x == y:
                same += 1
    return (100.0 * same / n if n else 0.0), n


def load_meta(lib: Path) -> Dict[str, Dict[str, str]]:
    meta: Dict[str, Dict[str, str]] = {}
    for tsv in sorted((lib / "vp1_context").glob("*.tsv")):
        with open(tsv) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if f and f[0]:
                    meta[f[0]] = dict(zip(header, f))
    return meta


def describe(name: str, meta: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    m = meta.get(name, {})
    head = name.rsplit("_", 1)[0]
    topo_from_name = head.split("-", 1)[1] if "-" in head else ""
    lineage = m.get("topotype") or topo_from_name
    return {"name": name, "serotype": m.get("serotype") or head.split("-")[0],
            "lineage": lineage, "topotype": lineage.split("/")[0] if lineage else "",
            "country": m.get("country", ""), "year": m.get("year", ""), "host": m.get("host", ""),
            "isolate": m.get("isolate", ""), "accession": m.get("accession") or name.rsplit("_", 1)[-1]}


def translate(seq_nogap: str) -> str:
    aa = []
    for i in range(0, len(seq_nogap) - 2, 3):
        aa.append(CODON.get(seq_nogap[i:i + 3], "X"))
    return "".join(aa)


def write_xlsx(path: Path, header: List[str], rows: List[List[str]], note: str) -> None:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sites"
    ws.append(header)
    for r in rows:
        ws.append(r)
    bold = Font(bold=True)
    for c in ws[1]:
        c.font = bold
        c.alignment = Alignment(textRotation=90, horizontal="center")
    fills = {"A": "C6EFCE", "C": "BDD7EE", "G": "FFE699", "T": "F4B084", "-": "D9D9D9", "N": "D9D9D9"}
    # colour by difference from the first data row (the first sample in tree order)
    if rows:
        ref = rows[0]
        for ri, r in enumerate(rows, start=2):
            for ci, v in enumerate(r[2:], start=3):
                if v != ref[ci - 1] and v not in ("", "-", "N", "X"):
                    ws.cell(row=ri, column=ci).fill = PatternFill("solid", fgColor=fills.get(v, "FFD966"))
                elif v in ("-", "N", "X"):
                    ws.cell(row=ri, column=ci).fill = PatternFill("solid", fgColor="EDEDED")
    ws.freeze_panes = "C2"
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22
    for ci in range(3, len(header) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = 3.2
    ws2 = wb.create_sheet("about")
    for line in note.splitlines():
        ws2.append([line])
    wb.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="the VP1 tree run folder (holds VP1/)")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--near", type=float, default=NEAR_PCT)
    ap.add_argument("--cap", type=int, default=CAP)
    ap.add_argument("--floor", type=int, default=FLOOR)
    a = ap.parse_args()
    run = Path(a.run).expanduser().resolve()
    vp1 = run / "VP1"
    lib = Path(a.lib).expanduser().resolve()
    summary: Dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": {}, "near_pct": a.near, "cap": a.cap}
    try:
        aln_path = vp1 / "VP1_aligned.fasta"
        tre = sorted(vp1.glob("VP1_*.tre"))
        info = json.loads((vp1 / "vp1_tree.json").read_text()) if (vp1 / "vp1_tree.json").is_file() else {}
        aln = dict(read_fasta(aln_path))
        samples = [s for s in info.get("samples_included", []) if s in aln]
        if not samples:
            samples = [n for n in aln if not re.match(r"^[A-Z0-9]+-", n)]
        order = newick_leaves(tre[-1].read_text()) if tre else list(aln)
        order = [n for n in order if n in aln] + [n for n in aln if n not in order]
        meta = load_meta(lib)
        others = [n for n in aln if n not in samples]

        # Neighbourhoods
        neigh: Dict[str, Dict] = {}
        keep: set = set(samples)
        for s in samples:
            scored = []
            for o in others:
                pid, n = identity(aln[s], aln[o])
                if n >= 300:
                    scored.append((pid, n, o))
            scored.sort(key=lambda x: (-x[0], x[2]))
            if not scored:
                neigh[s] = {"nearest": None, "records": []}
                continue
            best = scored[0][0]
            within = [x for x in scored if x[0] >= best - a.near]
            chosen = within[:a.cap] if len(within) >= a.floor else scored[:a.floor]
            recs = [{**describe(o, meta), "identity": round(pid, 2), "compared_bases": n} for pid, n, o in chosen]
            neigh[s] = {"nearest": recs[0], "records": recs, "within_pct": a.near,
                        "n_within": len(within), "shown": len(recs)}
            keep.update(r["name"] for r in recs)
        (vp1 / "VP1_neighbours.json").write_text(json.dumps({"samples": samples, "neighbourhoods": neigh}, indent=1))
        with open(vp1 / "VP1_neighbours.tsv", "w") as fh:
            fh.write("sample\trank\trecord\tidentity\tserotype\tlineage\tcountry\tyear\thost\taccession\tisolate\n")
            for s in samples:
                for i, r in enumerate(neigh[s]["records"], 1):
                    fh.write("\t".join(str(x) for x in (s, i, r["name"], r["identity"], r["serotype"], r["lineage"],
                                                        r["country"], r["year"], r["host"], r["accession"], r["isolate"])) + "\n")
        summary["files"]["neighbours"] = str(vp1 / "VP1_neighbours.json")

        # Site tables over samples + neighbourhoods, in tree order
        rows_names = [n for n in order if n in keep]
        first = rows_names[0]
        # column index in the alignment -> position in the first row's VP1 (gaps removed)
        pos_of_col: Dict[int, int] = {}
        p = 0
        for ci, ch in enumerate(aln[first]):
            if ch != "-":
                p += 1
            pos_of_col[ci] = p
        L = len(aln[first])

        def label(n: str) -> str:
            if n in samples:
                return "sample"
            d = describe(n, meta)
            return " ".join(x for x in (d["lineage"], d["country"], d["year"]) if x)

        def build(level: str):
            if level == "nt":
                seqs = {n: aln[n] for n in rows_names}
                ncol = L
                def site_name(ci): return f"nt{pos_of_col[ci]}"
            else:
                # translate each row's ungapped sequence; then re-gap by codon against the first row
                # simple approach: translate aligned sequence codon by codon on the alignment frame
                # of the first row (its first base is codon position 1).
                seqs = {}
                for n in rows_names:
                    s_ = aln[n]
                    aa = []
                    for i in range(0, L - 2, 3):
                        cod = s_[i:i + 3]
                        aa.append("-" if cod == "---" else ("X" if "-" in cod or "N" in cod else CODON.get(cod, "X")))
                    seqs[n] = "".join(aa)
                ncol = len(seqs[first])
                def site_name(ci): return f"aa{ci + 1}"
            variable = []
            for ci in range(ncol):
                states = {seqs[n][ci] for n in rows_names if seqs[n][ci] not in "-NX"}
                if len(states) > 1:
                    variable.append(ci)
            # cascade: minority-state count, descending
            def minority(ci):
                col = [seqs[n][ci] for n in rows_names if seqs[n][ci] not in "-NX"]
                if not col:
                    return 0
                top = max(set(col), key=col.count)
                return len(col) - col.count(top)
            cascade = sorted(variable, key=lambda ci: (-minority(ci), ci))
            def table(cols):
                header = ["taxon", "lineage / country / year"] + [site_name(ci) for ci in cols]
                rows = [[n, label(n)] + [seqs[n][ci] for ci in cols] for n in rows_names]
                return header, rows
            return variable, cascade, table

        note = (f"VP1 sites that vary among the samples and their neighbourhoods (records within {a.near}% of each "
                f"sample's nearest, at most {a.cap}). Rows in tree order. Numbering is {first}'s VP1, 1-based, gaps "
                f"removed. Colours mark differences from the first row. Written by fmdv_vp1_tables.py.")
        counts = {}
        for level in ("nt", "aa"):
            variable, cascade, table = build(level)
            h, r = table(variable)
            write_xlsx(vp1 / f"VP1_sorted_table_{level}.xlsx", h, r, note + f" Sorted: {level} sites in VP1 order.")
            h, r = table(cascade)
            write_xlsx(vp1 / f"VP1_cascade_table_{level}.xlsx", h, r,
                       note + f" Cascade: {level} sites ordered by how many rows carry the minority state.")
            counts[level] = {"rows": len(rows_names), "variable_sites": len(variable)}
            summary["files"][f"sorted_{level}"] = str(vp1 / f"VP1_sorted_table_{level}.xlsx")
            summary["files"][f"cascade_{level}"] = str(vp1 / f"VP1_cascade_table_{level}.xlsx")
        summary.update({"status": "ok", "samples": samples, "rows": rows_names, "counts": counts})
        _err(f"vp1_tables: {len(samples)} samples, {len(rows_names)} rows; nt sites {counts['nt']['variable_sites']}, "
             f"aa sites {counts['aa']['variable_sites']}")
    except Exception as e:  # never fail the tree run
        summary.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
        _err(f"vp1_tables: failed: {e}")
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (vp1 / "vp1_tables.json").write_text(json.dumps(summary, indent=1))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
