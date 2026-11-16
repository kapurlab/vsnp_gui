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


class Node:
    __slots__ = ("name", "length", "label", "kids")

    def __init__(self, name: str = "", length: Optional[float] = None, label: str = ""):
        self.name, self.length, self.label, self.kids = name, length, label, []


def parse_newick(text: str) -> Node:
    """Enough Newick for RAxML output: nested parentheses, optional quoted
    names, branch lengths, internal labels (bootstraps) and [comments]."""
    s = re.sub(r"\[[^\]]*\]", "", text.strip())
    pos = 0

    def read_label() -> str:
        nonlocal pos
        if pos < len(s) and s[pos] == "'":
            end = s.index("'", pos + 1)
            while end + 1 < len(s) and s[end + 1] == "'":
                end = s.index("'", end + 2)
            lab = s[pos + 1:end].replace("''", "'")
            pos = end + 1
            return lab
        m = re.match(r"[^,:;()\[\]]*", s[pos:])
        pos += m.end()
        return m.group(0).strip()

    def read_length() -> Optional[float]:
        nonlocal pos
        if pos < len(s) and s[pos] == ":":
            m = re.match(r":\s*([-+0-9.eE]+)", s[pos:])
            pos += m.end()
            return float(m.group(1))
        return None

    def node() -> Node:
        nonlocal pos
        n = Node()
        if s[pos] == "(":
            pos += 1
            while True:
                n.kids.append(node())
                if s[pos] == ",":
                    pos += 1
                    continue
                if s[pos] == ")":
                    pos += 1
                    break
            n.label = read_label()
        else:
            n.name = read_label()
        n.length = read_length()
        return n

    return node()


def leaves(n: Node) -> List[Node]:
    return [n] if not n.kids else [x for k in n.kids for x in leaves(k)]


def prune(n: Node, keep: set) -> Optional[Node]:
    """The subtree over `keep`: dropped leaves vanish, a node left with one
    child is merged into it with the branch lengths added, so distances
    between the kept leaves are unchanged."""
    if not n.kids:
        return n if n.name in keep else None
    kids = [k for k in (prune(k, keep) for k in n.kids) if k is not None]
    if not kids:
        return None
    if len(kids) == 1:
        child = kids[0]
        if n.length is not None or child.length is not None:
            child.length = (n.length or 0.0) + (child.length or 0.0)
        return child
    n.kids = kids
    return n


def write_newick(n: Node, rename: Optional[Dict[str, str]] = None) -> str:
    def q(label: str) -> str:
        return "'" + label.replace("'", "''") + "'" if re.search(r"[\s,:;()\[\]']", label) else label

    def rec(x: Node) -> str:
        if x.kids:
            inner = "(" + ",".join(rec(k) for k in x.kids) + ")" + (q(x.label) if x.label else "")
        else:
            inner = q((rename or {}).get(x.name, x.name))
        return inner + (f":{x.length:g}" if x.length is not None else "")
    return rec(n) + ";"


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


# vsnp3's cascade-table scheme, kept so the two tabs read alike: a cell that
# matches the first row is near-white and only differences carry colour; A
# green, G yellow, C blue, T red; ambiguity codes, N and gaps a pale mauve.
# Amino acids colour by class. Fills are written opaque ("FF" alpha): openpyxl
# stores a bare six-digit colour with alpha 00, which the GUI's renderer, like
# Excel, treats as transparent.
NT_FILL = {"A": "FF58FA82", "G": "FFF7FE2E", "C": "FF0000FF", "T": "FFFF0000"}
AA_CLASS = {**{a: "FFFFE26B" for a in "AVLIMFWY"},    # hydrophobic
            **{a: "FF7FE3A1" for a in "STNQ"},        # polar
            **{a: "FF8FB4FF" for a in "KRH"},         # positive
            **{a: "FFFF8A80" for a in "DE"},          # negative
            **{a: "FFD9B3FF" for a in "GPC"}}         # special
OTHER_FILL = "FFE2CFDD"
MATCH_FILL = "FFFDFEFE"
WHITE_TEXT = {"C", "T"}


def write_xlsx(path: Path, header: List[str], rows: List[List[str]], note: str, level: str) -> None:
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
    palette = NT_FILL if level == "nt" else AA_CLASS
    fills = {k: PatternFill("solid", fgColor=v) for k, v in palette.items()}
    other = PatternFill("solid", fgColor=OTHER_FILL)
    match = PatternFill("solid", fgColor=MATCH_FILL)
    white = Font(color="FFFFFFFF", bold=True)
    centre = Alignment(horizontal="center")
    if rows:
        # Differences are read against the first SAMPLE in tree order, not the
        # top row, which is usually a context record; the sample rows are bold
        # so the eye finds them among the records.
        ref_i = next((i for i, r in enumerate(rows) if r[1] == "sample"), 0)
        ref = rows[ref_i]
        for ri, r in enumerate(rows, start=2):
            if r[1] == "sample":
                ws.cell(row=ri, column=1).font = bold
                ws.cell(row=ri, column=2).font = bold
            for ci, v in enumerate(r[2:], start=3):
                cell = ws.cell(row=ri, column=ci)
                cell.alignment = centre
                if v in ("", "-", "N", "X"):
                    cell.fill = other
                elif ri - 2 != ref_i and v == ref[ci - 1]:
                    cell.fill = match
                else:
                    cell.fill = fills.get(v, other)
                    if level == "nt" and v in WHITE_TEXT:
                        cell.font = white
    ws.freeze_panes = "C2"
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 14
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
        # The full tree only: the neighbourhood tree this script writes sits
        # beside it and must never be the one pruned.
        tre = sorted(p for p in vp1.glob("VP1_*.tre") if "neighbourhood" not in p.name)
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

        # The neighbourhood tree: the samples, their neighbourhoods, and one
        # representative of every other lineage in the run, pruned from the
        # full tree so branch lengths and topology are the full tree's own.
        # A viewer draws fifteen to sixty leaves with every label readable;
        # the full tree stays beside it for the reader who wants it all.
        if tre:
            root = parse_newick(tre[-1].read_text())
            present = [x.name for x in leaves(root)]
            # One representative per TOPOTYPE outside the neighbourhoods. The
            # context TSVs name topotypes as the first field of the lineage
            # string (ME-SA/PANASIA-2/ANT-10); a panel genome's name carries
            # the same words joined by hyphens (ME-SA-PanAsia-2-FAR-09), so it
            # is matched to the longest known topotype it starts with.
            topotypes_by_sero: Dict[str, set] = {}
            for m in meta.values():
                t = (m.get("topotype") or "").split("/")[0].upper()
                if t:
                    topotypes_by_sero.setdefault(m.get("serotype", "").upper(), set()).add(t)

            def topotype_key(name: str) -> str:
                d = describe(name, meta)
                lin = (d["lineage"] or "").upper()
                if name in meta:
                    return f"{d['serotype']}:{lin.split('/')[0]}"
                cands = [t for t in topotypes_by_sero.get(d["serotype"].upper(), set())
                         if lin == t or lin.startswith(t + "-") or lin.startswith(t + "/")]
                return f"{d['serotype']}:{max(cands, key=len) if cands else lin.split('-')[0]}"

            by_topo: Dict[str, List[str]] = {}
            for name in present:
                if name in keep or name in samples:
                    continue
                by_topo.setdefault(topotype_key(name), []).append(name)
            kept_topos = {topotype_key(n) for n in keep if n not in samples}
            reps: List[str] = []
            for topo, names in sorted(by_topo.items()):
                if topo in kept_topos:
                    continue
                # alphabetical first: deterministic, and the reader only needs one of each
                reps.append(sorted(names)[0])
            def context_label(name: str) -> str:
                d = describe(name, meta)
                parts = [d["lineage"] or d["serotype"], d["country"], d["year"] if str(d["year"]) not in ("", "0") else "", d["accession"]]
                return "|".join(x for x in parts if x).replace(" ", "_")

            def write_pruned(path: Path, tree_keep: set) -> Optional[Dict]:
                # The pruner edits nodes in place, so each tree comes from a fresh parse.
                pruned = prune(parse_newick(tre[-1].read_text()), tree_keep)
                if pruned is None:
                    return None
                rename = {n: context_label(n) for n in tree_keep if n not in samples}
                path.write_text(write_newick(pruned, rename) + "\n")
                return {"leaves": len(tree_keep), "labels": rename}

            # The run's tree: every sample with its relatives, which answers
            # whether the samples cluster with each other.
            tree_keep = set(keep) | set(reps)
            run_tree = write_pruned(vp1 / "VP1_neighbourhood.tre", tree_keep)
            if run_tree:
                (vp1 / "VP1_neighbourhood_labels.json").write_text(json.dumps(
                    {"samples": samples, "neighbourhood": sorted(keep - set(samples)),
                     "lineage_representatives": reps, "labels": run_tree["labels"]}, indent=1))
                summary["files"]["neighbourhood_tree"] = str(vp1 / "VP1_neighbourhood.tre")
                summary["neighbourhood_tree"] = {"leaves": len(tree_keep), "samples": len(samples),
                                                 "neighbourhood": len(keep) - len(samples), "lineage_representatives": len(reps),
                                                 "full_tree_leaves": len(present)}
            # One tree per sample: that sample, its relatives, and one record
            # per other topotype. The figure a sample's report prints.
            per_sample: Dict[str, Dict] = {}
            for s_ in samples:
                own = {s_} | {r["name"] for r in neigh.get(s_, {}).get("records", [])}
                own_topos = {topotype_key(n) for n in own if n != s_}
                s_reps = [sorted(names)[0] for topo, names in sorted(by_topo.items()) if topo not in own_topos]
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", s_)
                t = write_pruned(vp1 / f"VP1_neighbourhood_{safe}.tre", own | set(s_reps))
                if t:
                    per_sample[s_] = {"file": str(vp1 / f"VP1_neighbourhood_{safe}.tre"), "leaves": t["leaves"],
                                      "neighbourhood": len(own) - 1, "lineage_representatives": len(s_reps)}
            summary["sample_trees"] = per_sample

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
                f"removed. A cell that matches the first sample (in tree order) is near-white; only differences carry colour (vsnp3's scheme). Sample rows are bold. Written by fmdv_vp1_tables.py.")
        counts = {}
        for level in ("nt", "aa"):
            variable, cascade, table = build(level)
            h, r = table(variable)
            write_xlsx(vp1 / f"VP1_sorted_table_{level}.xlsx", h, r, note + f" Sorted: {level} sites in VP1 order.", level)
            h, r = table(cascade)
            write_xlsx(vp1 / f"VP1_cascade_table_{level}.xlsx", h, r,
                       note + f" Cascade: {level} sites ordered by how many rows carry the minority state.", level)
            counts[level] = {"rows": len(rows_names), "variable_sites": len(variable)}
            summary["files"][f"sorted_{level}"] = str(vp1 / f"VP1_sorted_table_{level}.xlsx")
            summary["files"][f"cascade_{level}"] = str(vp1 / f"VP1_cascade_table_{level}.xlsx")
        summary.update({"status": "ok", "samples": samples, "rows": rows_names, "counts": counts})
        nt_ = summary.get("neighbourhood_tree") or {}
        _err(f"vp1_tables: {len(samples)} samples, {len(rows_names)} rows; nt sites {counts['nt']['variable_sites']}, "
             f"aa sites {counts['aa']['variable_sites']}"
             + (f"; neighbourhood tree {nt_['leaves']} of {nt_['full_tree_leaves']} leaves" if nt_ else ""))
    except Exception as e:  # never fail the tree run
        summary.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
        _err(f"vp1_tables: failed: {e}")
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (vp1 / "vp1_tables.json").write_text(json.dumps(summary, indent=1))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
