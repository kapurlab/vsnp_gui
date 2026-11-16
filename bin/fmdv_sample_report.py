#!/usr/bin/env python3
"""One report per sample, HTML and PDF, for FMDV mode.

What a sample's report says, in the order a reader needs it: the call
(serotype, topotype, lineage, nearest record), the flags, the QC verdict and
the numbers under it, the reference the reads chose, the sample's
neighbourhood among GenBank VP1 records, its neighbourhood tree, the VP1
amino acid differences from the record that named its lineage, the VP1
sequence in print with the consensus beside it as a file, and the fixed
caveats of the method. Versions and dates at the top.

Written at the end of Step 1 (typing done, no tree yet: the tree and
neighbourhood sections say so) and again when a VP1 tree that includes the
sample finishes. Reads only what the sample folder and the latest such tree
run hold; writes <sample>_FMDV_report.html and .pdf beside them. PDF needs
weasyprint (in the vsnp3 env); without it the HTML is still written.

    fmdv_sample_report.py --sample-dir <step1/sample> --lib <library>
    fmdv_sample_report.py --project <project> --lib <library> [--samples a b]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "backend" / "app"))

import fmdv_vp1_tables as T  # noqa: E402  (Newick, fasta, metadata, translation)

try:
    from qc_verdict import compute_verdict, merge_thresholds  # noqa: E402
except Exception:  # the backend is not beside bin/: no verdict, numbers only
    compute_verdict = merge_thresholds = None
try:
    from qc_scan import _read_stats_row  # noqa: E402
except Exception:
    _read_stats_row = None


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_json(p: Path) -> Dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def pct(v, nd=1) -> str:
    return f"{float(v):.{nd}f}%" if isinstance(v, (int, float)) else "-"


# ---- inputs ---------------------------------------------------------------

def stats_row(sample_dir: Path, sample: str) -> Dict:
    files = sorted(sample_dir.glob(f"{sample}_*_stats.xlsx"), key=lambda p: p.stat().st_mtime)
    if not files:
        return {}
    if _read_stats_row:
        return _read_stats_row(str(files[-1])) or {}
    import openpyxl
    ws = openpyxl.load_workbook(files[-1], read_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    h, v = next(it, None), next(it, None)
    return dict(zip([str(k) for k in h], v)) if h and v else {}


def latest_tree_run(project: Path, sample: str) -> Optional[Path]:
    """The newest VP1 tree run whose tables include this sample, a run
    restricted to one serotype preferred over an all-serotype run: its
    neighbourhood and tree are the serotype's, which is what a report
    shows; the all-serotype tree carries every other serotype's
    representatives and runs off the page."""
    step2 = project / "step2"
    if not step2.is_dir():
        return None
    runs = sorted((d for d in step2.iterdir() if d.is_dir() and "_VP1-tree" in d.name), reverse=True)
    fallback = None
    for r in runs:
        j = load_json(r / "VP1" / "vp1_tables.json")
        if sample not in (j.get("samples") or []):
            continue
        if re.search(r"_VP1-tree-[A-Za-z0-9]+$", r.name):
            return r
        fallback = fallback or r
    return fallback


def versions(project: Path, lib: Path, row: Dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    repo = HERE.parent
    try:
        out["fmdv_gui"] = subprocess.run(["git", "-C", str(repo), "describe", "--tags", "--always", "--dirty"],
                                         capture_output=True, text=True, timeout=10).stdout.strip() or (repo / "VERSION").read_text().strip()
    except Exception:
        try:
            out["fmdv_gui"] = (repo / "VERSION").read_text().strip()
        except OSError:
            out["fmdv_gui"] = "unknown"
    caller = str(row.get("SNP Caller") or "")
    out["caller"] = caller
    out["aligner"] = str(row.get("Aligner") or "")
    lin = load_json(lib / "lineages.json")
    out["library"] = f"{lin.get('n_genomes', '?')} lineage genomes from {Path(str(lin.get('source', ''))).name or 'NanoFMDV'}"
    src = load_json(lib / "vp1_context" / "SOURCE.json")
    out["context"] = src.get("source", "") or "GenBank VP1 sets"
    db = lib / "vp1_context" / "_typing" / "all_vp1.fasta"
    if db.is_file():
        n = sum(1 for line in open(db) if line.startswith(">"))
        out["typing_db"] = f"{n:,} VP1 records, built {time.strftime('%Y-%m-%d', time.localtime(db.stat().st_mtime))}"
    return out


# ---- the tree figure --------------------------------------------------------

def _dist_from(node: T.Node) -> Dict[int, Tuple[float, List[T.Node]]]:
    """Distance and path from `node` to every descendant leaf (by id)."""
    out = {}
    def rec(n, d, path):
        if not n.kids:
            out[id(n)] = (d, path + [n]); return
        for k in n.kids:
            rec(k, d + (k.length or 0.0), path + [n])
    rec(node, 0.0, [])
    return out


def midpoint_root(root: T.Node) -> T.Node:
    """Re-root at the midpoint of the longest leaf-to-leaf path. Done on a
    parent map so the tree is rebuilt once, not re-parsed."""
    parent: Dict[int, Optional[T.Node]] = {id(root): None}
    nodes: List[T.Node] = []
    def walk(n):
        nodes.append(n)
        for k in n.kids:
            parent[id(k)] = n; walk(k)
    walk(root)
    lv = [n for n in nodes if not n.kids]
    if len(lv) < 3:
        return root
    def dist_to_root(n):
        d, x = 0.0, n
        while parent[id(x)] is not None:
            d += x.length or 0.0; x = parent[id(x)]
        return d
    def path_to_root(n):
        p, x = [n], n
        while parent[id(x)] is not None:
            x = parent[id(x)]; p.append(x)
        return p
    # farthest leaf from an arbitrary leaf, then farthest from that: the diameter
    def farthest(a):
        pa = path_to_root(a); da = dist_to_root(a)
        best = (0.0, a)
        for b in lv:
            pb = path_to_root(b)
            common = next(x for x in pa if x in pb)
            d = da + dist_to_root(b) - 2 * dist_to_root(common)
            if d > best[0]:
                best = (d, b)
        return best
    _, a = farthest(lv[0])
    diam, b = farthest(a)
    if diam <= 0:
        return root
    # walk from a toward b, find the edge holding the midpoint
    pa, pb = path_to_root(a), path_to_root(b)
    common = next(x for x in pa if x in pb)
    path = pa[:pa.index(common)] + [common] + list(reversed(pb[:pb.index(common)]))
    half, acc = diam / 2.0, 0.0
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        # edge length between u and v: whichever is the child
        L = (u.length or 0.0) if parent[id(u)] is v else (v.length or 0.0)
        if acc + L >= half:
            child = u if parent[id(u)] is v else v
            frac = (half - acc) / L if L > 0 else 0.5
            return _reroot_on_edge(child, parent, frac)
        acc += L
    return root


def _reroot_on_edge(child: T.Node, parent: Dict[int, Optional[T.Node]], frac: float) -> T.Node:
    """New root on the edge above `child`, `frac` of the way from child to parent."""
    L = child.length or 0.0
    up = parent[id(child)]
    new = T.Node()
    # the child side keeps its subtree
    child_copy = child
    child_copy.length = L * frac
    # the parent side: re-hang the chain of ancestors
    def hang(node, came_from, length_to_node):
        n = T.Node(node.name, length_to_node, node.label)
        for k in node.kids:
            if k is came_from:
                continue
            n.kids.append(k)
        p = parent[id(node)]
        if p is not None:
            n.kids.append(hang(p, node, node.length or 0.0))
        if not n.kids and not n.name:
            return None
        return n
    other = hang(up, child, L * (1 - frac))
    new.kids = [child_copy] + ([other] if other is not None else [])
    # collapse a unary internal on the far side
    def collapse(n):
        if not n.kids:
            return n
        n.kids = [collapse(k) for k in n.kids]
        if len(n.kids) == 1 and not n.name:
            k = n.kids[0]
            k.length = (k.length or 0.0) + (n.length or 0.0)
            return k
        return n
    new.kids = [collapse(k) for k in new.kids]
    return new


def tree_svg(newick: str, highlight: set, width: int = 760) -> str:
    root = midpoint_root(T.parse_newick(newick))
    lv = T.leaves(root)
    n = len(lv)
    if n == 0:
        return ""
    row_h = 15 if n <= 40 else 11 if n <= 70 else 8
    pad_l, pad_t, label_w = 10, 14, 300
    plot_w = width - pad_l - label_w - 10
    height = pad_t + n * row_h + 34
    # x by cumulative length, y by leaf order
    xs: Dict[int, float] = {}
    ys: Dict[int, float] = {}
    order = {id(l): i for i, l in enumerate(lv)}
    def setx(node, x):
        xs[id(node)] = x
        for k in node.kids:
            setx(k, x + (k.length or 0.0))
    setx(root, 0.0)
    xmax = max(xs.values()) or 1.0
    def sety(node):
        if not node.kids:
            ys[id(node)] = pad_t + (order[id(node)] + 0.5) * row_h; return ys[id(node)]
        ks = [sety(k) for k in node.kids]
        ys[id(node)] = (min(ks) + max(ks)) / 2.0
        return ys[id(node)]
    sety(root)
    sx = lambda x: pad_l + x / xmax * plot_w
    fs = 10 if row_h >= 15 else 8 if row_h >= 11 else 6.5
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="{fs}">']
    def draw(node):
        x0 = sx(xs[id(node)]); y0 = ys[id(node)]
        for k in node.kids:
            x1 = sx(xs[id(k)]); y1 = ys[id(k)]
            parts.append(f'<path d="M{x0:.1f},{y0:.1f} V{y1:.1f} H{x1:.1f}" fill="none" stroke="#333" stroke-width="1"/>')
            draw(k)
        if node.kids and node.label and node is not root:
            try:
                bs = float(node.label)
                if bs >= 50:
                    parts.append(f'<text x="{x0 - 2:.1f}" y="{y0 - 2:.1f}" text-anchor="end" fill="#777" font-size="8">{int(bs)}</text>')
            except ValueError:
                pass
        if not node.kids:
            hl = node.name in highlight
            label = node.name
            if hl:
                band_w = min(label_w - 6, len(label) * fs * 0.62 + 8)
                parts.append(f'<rect x="{x0 + 3:.1f}" y="{y0 - row_h / 2 + 0.5:.1f}" width="{band_w:.0f}" height="{row_h - 1}" fill="#f9d6d6" rx="2"/>')
            parts.append(f'<text x="{x0 + 6:.1f}" y="{y0 + 3.5:.1f}" fill="{"#b00020" if hl else "#222"}" font-weight="{"bold" if hl else "normal"}">{esc(label)}</text>')
    draw(root)
    # scale bar: a round number of substitutions per site
    target = xmax / 5.0
    step = 10 ** math.floor(math.log10(target)) if target > 0 else 0.01
    for m in (1, 2, 5, 10):
        if step * m >= target:
            step *= m; break
    bx0, by = pad_l, height - 14
    parts.append(f'<path d="M{bx0},{by} H{sx(step):.1f}" stroke="#333" stroke-width="1.2"/>')
    parts.append(f'<text x="{bx0}" y="{by - 4}" fill="#333" font-size="9">{step:g} substitutions/site</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ---- VP1 differences --------------------------------------------------------

def vp1_differences(aln: Dict[str, str], sample: str, record: str) -> Dict:
    a, b = aln.get(sample), aln.get(record)
    if not a or not b:
        return {}
    nt = aa = 0
    aa_list: List[str] = []
    pos = 0
    L = min(len(a), len(b))
    for i in range(0, L - 2, 3):
        ca, cb = a[i:i + 3], b[i:i + 3]
        for x, y in zip(ca, cb):
            if x in "ACGT" and y in "ACGT" and x != y:
                nt += 1
        if "-" in ca or "-" in cb or "N" in ca or "N" in cb:
            if ca.replace("-", "") :
                pos += 1 if "-" not in ca else 0
            continue
        pos += 1
        ra, rb = T.CODON.get(ca, "X"), T.CODON.get(cb, "X")
        if ra != rb and "X" not in (ra, rb):
            aa += 1
            aa_list.append(f"{rb}{pos}{ra}")
    return {"nt": nt, "aa": aa, "changes": aa_list}


# ---- the page ----------------------------------------------------------------

CSS = """
body{font-family:Helvetica,Arial,sans-serif;font-size:11pt;color:#1a1a1a;margin:0;padding:24px 28px;max-width:860px}
h1{font-size:18pt;margin:0 0 2px} h2{font-size:12.5pt;margin:22px 0 6px;border-bottom:1px solid #ccc;padding-bottom:2px}
.muted{color:#666} .call{font-size:14pt;margin:10px 0 4px} .flag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:9.5pt;margin-right:5px}
.pass{background:#d7f2dc;color:#13552a} .review{background:#fff0c2;color:#6b4e00} .fail{background:#f9d6d6;color:#8a0f1a} .note{background:#eef1f4;color:#333}
table{border-collapse:collapse;width:100%;font-size:9.5pt;margin:4px 0 8px} th,td{border-bottom:1px solid #e3e3e3;text-align:left;padding:3px 6px;vertical-align:top} th{color:#555;font-weight:600}
.kv td:first-child{color:#555;width:38%} pre.seq{font-family:Menlo,Consolas,monospace;font-size:8pt;line-height:1.35;white-space:pre-wrap;word-break:break-all;background:#f7f7f7;padding:8px;border-radius:4px}
.small{font-size:9pt} @page{size:A4;margin:14mm 12mm} .nobreak{page-break-inside:avoid}
"""


def build(sample_dir: Path, lib: Path, project: Path) -> Tuple[str, Dict]:
    sample = sample_dir.name
    ref = load_json(sample_dir / "fmdv_reference.json")
    typ = load_json(sample_dir / "fmdv_vp1_type.json")
    cons = load_json(sample_dir / "fmdv_consensus.json")
    row = stats_row(sample_dir, sample)
    proj = load_json(project / "project.json")
    thr = proj.get("qc_thresholds") or {}
    chosen = ref.get("chosen") or {}
    run = latest_tree_run(project, sample)
    meta = T.load_meta(lib)
    ver = versions(project, lib, row)

    # verdict, as the GUI computes it
    verdict = None
    if compute_verdict and row:
        r2 = dict(row)
        reasons = []
        if ref.get("distant"):
            reasons.append("distant reference" + (f", identity {ref['identity']:.2f}" if isinstance(ref.get("identity"), (int, float)) else ""))
        if ref.get("mixed"):
            sh = ref.get("serotype_share") or {}
            reasons.append("mixed serotypes (" + ", ".join(f"{k} {v:.0%}" for k, v in list(sh.items())[:3]) + ")")
        if reasons:
            r2["_contamination"] = True; r2["_contamination_reason"] = "; ".join(reasons)
        try:
            verdict = compute_verdict(r2, merge_thresholds(thr))
        except Exception:
            verdict = None

    # neighbourhood, tree, differences from the latest run
    neigh = None; tree_svg_txt = ""; diffs = {}; aln = {}
    if run:
        nj = load_json(run / "VP1" / "VP1_neighbours.json")
        neigh = (nj.get("neighbourhoods") or {}).get(sample)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", sample)
        tre = run / "VP1" / f"VP1_neighbourhood_{safe}.tre"
        if tre.is_file():
            try:
                tree_svg_txt = tree_svg(tre.read_text(), {sample})
            except Exception as e:  # a figure must never sink the report
                tree_svg_txt = f'<p class="muted">Tree figure could not be drawn: {esc(e)}</p>'
        af = run / "VP1" / "VP1_aligned.fasta"
        if af.is_file():
            aln = dict(T.read_fasta(af))
            # Against the record that named the lineage when the run's
            # alignment holds it, else the nearest record in the alignment.
            tb = typ.get("typed_by") or {}
            against = tb.get("name") if tb.get("name") in aln else None
            if not against and neigh and neigh.get("records"):
                against = neigh["records"][0]["name"]
            if against:
                diffs = vp1_differences(aln, sample, against)
                diffs["against"] = against
                diffs["named_lineage"] = against == tb.get("name")

    best = typ.get("best") or {}
    tb = typ.get("typed_by") or {}
    status = typ.get("status") or "not typed"
    typed = status == "typed"
    lineage = typ.get("lineage") or ""
    provisional = typed and (not lineage or ref.get("distant"))
    flags = []
    if verdict:
        flags.append(f'<span class="flag {verdict["level"]}">QC {verdict["level"]}</span>')
    if ref.get("mixed"):
        sh = ref.get("serotype_share") or {}
        flags.append('<span class="flag review">mixed: ' + ", ".join(f"{k} {v:.0%}" for k, v in list(sh.items())[:3]) + "</span>")
    if ref.get("distant"):
        flags.append(f'<span class="flag review">distant: reads {ref.get("identity")} identical to the nearest library genome</span>')
    if provisional:
        flags.append('<span class="flag note">lineage provisional</span>')
    if typ.get("note"):
        flags.append(f'<span class="flag review">{esc(typ["note"])}</span>')
    if ref.get("composition_basis") == "undetermined":
        flags.append('<span class="flag note">serotype composition undetermined (too few VP1 reads)</span>')

    def kv(rows_):
        return '<table class="kv">' + "".join(f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in rows_) + "</table>"

    call_line = (f"{esc(typ.get('serotype'))} {esc(lineage or 'lineage unassigned')}" if typed
                 else (f"{esc(chosen.get('serotype'))} (reads vote; VP1 {esc(status)})" if chosen else "no call"))
    nearest_line = (f"{esc(best.get('accession'))} {esc(best.get('isolate'))} {esc(best.get('country'))} {esc(best.get('year')) if str(best.get('year')) not in ('0', 'None') else ''}, "
                    f"{pct(typ.get('identity'))} identity over {pct(typ.get('coverage'), 0)} of VP1" if typed else "")
    named_by = (f"lineage named by {esc(tb.get('accession'))} {esc(tb.get('country'))} {esc(tb.get('year')) if str(tb.get('year')) not in ('0', 'None') else ''} at {pct(tb.get('identity'))}"
                if tb else "")
    agree = typ.get("agreement") or {}

    vp1_seq = ""
    vf = sample_dir / f"{sample}_VP1.fasta"
    if vf.is_file():
        recs = T.read_fasta(vf)
        if recs:
            seq = recs[0][1]
            vp1_seq = "\n".join(seq[i:i + 60] for i in range(0, len(seq), 60))
    cons_file = sample_dir / f"{sample}_consensus.fasta"

    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(sample)} FMDV report</title><style>{CSS}</style></head><body>"]
    h.append(f"<h1>{esc(sample)}</h1><div class='muted'>FMDV report · project {esc(project.name)} · Step 1 run {esc(row.get('date') or '-')} · written {time.strftime('%Y-%m-%d %H:%M')}</div>")
    h.append("<div class='call'>" + call_line + "</div>")
    if nearest_line:
        h.append(f"<div>Nearest VP1 record: {nearest_line}" + (f" · {named_by}" if named_by and tb.get("accession") != best.get("accession") else "") + "</div>")
    if agree.get("lineage") is not None:
        h.append(f"<div class='small muted'>Lineage agreement {agree['lineage']:.0%} of the {agree.get('n_annotated')} annotated hits among the top {agree.get('n')}; serotype agreement {agree.get('serotype', 0):.0%}.</div>")
    h.append("<div style='margin:8px 0'>" + " ".join(flags) + "</div>")

    h.append("<h2>Quality</h2>")
    qrows = [("Verdict", f"<b>{esc(verdict['level'])}</b>" + (": " + esc("; ".join(verdict["reasons"])) if verdict and verdict["reasons"] else "") if verdict else "-"),
             ("Reads", esc(row.get("R1 Read Count") or "-") + (" paired" if row.get("FASTQ_R2") else "")),
             ("Platform", esc(row.get("Platform") or "-")),
             ("Mean depth", esc(row.get("Average Depth") or "-")),
             ("Mapped to the reference", f"{100 - float(str(row.get('Unmapped Percent', '0%')).rstrip('%') or 0):.1f}%" if row.get("Unmapped Percent") else "-"),
             ("Genome covered (≥1 read)", esc(row.get("Genome with Coverage") or "-")),
             ("Genome called (depth ≥10, unambiguous)", pct(cons.get("called_percent"))),
             ("VP1 called", pct(cons.get("vp1_called_percent"))),
             ("Mixed sites (reads disagree)", esc(row.get("Ambiguous SNPs") or "-")),
             ("SNPs against the lineage reference", esc(row.get("Quality SNPs") or "-"))]
    h.append(kv(qrows))
    if thr:
        h.append(f"<div class='small muted'>Lines: genome covered pass {thr.get('genome_coverage', {}).get('pass_min')}% / fail {thr.get('genome_coverage', {}).get('review_min')}%; depth pass {thr.get('coverage', {}).get('pass_min')}x / review {thr.get('coverage', {}).get('review_min')}x; mapping pass {thr.get('mapping_rate', {}).get('pass_min')}%.</div>")

    h.append("<h2>Reference chosen by the reads</h2>")
    sh = ref.get("serotype_share") or {}
    h.append(kv([("Lineage reference", f"{esc(chosen.get('safe'))} ({esc(chosen.get('isolate'))}, {esc(chosen.get('accession'))})" if chosen else "-"),
                 ("Chosen by", f"{esc(ref.get('vote'))} vote, share {esc(ref.get('f_match'))}" + (f"; runner-up {esc(ref.get('runner_up', {}).get('safe'))}" if ref.get("runner_up") else "")),
                 ("Read identity to it", esc(ref.get("identity"))),
                 ("Serotype composition of VP1 reads", ", ".join(f"{k} {v:.0%}" for k, v in sh.items()) if sh else "undetermined"),
                 ("Reads used", f"{esc(ref.get('reads_used'))} sampled; {esc(ref.get('fmdv_reads'))} FMDV; {esc(ref.get('vp1_reads'))} on VP1")]))

    h.append("<h2>Neighbourhood</h2>")
    if neigh and neigh.get("records"):
        h.append(f"<div class='small muted'>Records within {neigh.get('within_pct', 3)}% identity of the nearest, at most 20, from the VP1 tree run {esc(run.name)}; identity over compared bases of the alignment.</div>")
        h.append("<table><tr><th>#</th><th>Identity</th><th>Record</th><th>Lineage</th><th>Country</th><th>Year</th><th>Host</th><th>Isolate</th></tr>")
        for i, r in enumerate(neigh["records"], 1):
            h.append(f"<tr><td>{i}</td><td>{r['identity']}%</td><td>{esc(r['name'])}</td><td>{esc(r.get('lineage') or '-')}</td><td>{esc(r.get('country') or '-')}</td><td>{esc(r.get('year')) if str(r.get('year')) not in ('0', '', 'None') else '-'}</td><td>{esc(r.get('host') or '-')}</td><td>{esc(r.get('isolate') or '-')}</td></tr>")
        h.append("</table>")
    elif typed and typ.get("top"):
        h.append("<div class='small muted'>No VP1 tree yet for this sample; the top blastn hits stand in until one is built.</div><table><tr><th>#</th><th>Identity</th><th>Record</th><th>Lineage</th><th>Country</th><th>Year</th></tr>")
        for i, r in enumerate(typ["top"][:10], 1):
            h.append(f"<tr><td>{i}</td><td>{r['identity']}%</td><td>{esc(r['name'])}</td><td>{esc(r.get('lineage') or '-')}</td><td>{esc(r.get('country') or '-')}</td><td>{esc(r.get('year')) if str(r.get('year')) not in ('0', '', 'None') else '-'}</td></tr>")
        h.append("</table>")
    else:
        h.append("<p class='muted'>Not available: no typing result.</p>")

    h.append("<h2>VP1 tree</h2>")
    if tree_svg_txt:
        h.append(f"<div class='small muted'>The sample among its neighbourhood and one record per other topotype, pruned from the run's RAxML tree ({esc(run.name)}); midpoint-rooted; bootstrap support of 50 or more shown. Labels: lineage|country|year|accession.</div><div>{tree_svg_txt}</div>")
    else:
        h.append("<p class='muted'>No VP1 tree yet. Build one from the Step 2 VP1 tab; this report is rewritten when it finishes.</p>")

    h.append("<h2>VP1 amino acid differences</h2>")
    if diffs:
        ch = diffs.get("changes") or []
        h.append(f"<div class='small'>Against {esc(diffs['against'])}, {'the record that named the lineage' if diffs.get('named_lineage') else 'the nearest record in the tree alignment'}: {diffs['nt']} nucleotide and {diffs['aa']} amino acid differences over the aligned VP1.</div>")
        h.append("<div class='small' style='margin-top:4px'>" + (", ".join(esc(c) for c in ch) if ch else "none") + "</div>")
        h.append("<div class='small muted'>Residue numbers are the sample's own VP1, from its first codon; record residue first, sample residue last.</div>")
    else:
        h.append("<p class='muted'>Available once a VP1 tree includes this sample.</p>")

    h.append("<h2>Sequences</h2>")
    h.append(kv([("Consensus genome", f"{esc(cons_file.name)} · {esc(cons.get('length'))} nt, {pct(cons.get('called_percent'))} called, {esc(cons.get('substitutions'))} substitutions from the reference, {esc(cons.get('n_count'))} N" if cons else "-"),
                 ("VP1", f"{esc(vf.name)} · {esc(cons.get('vp1_length'))} nt, {pct(cons.get('vp1_called_percent'))} called" if cons else "-")]))
    if vp1_seq:
        h.append(f"<div class='small muted'>VP1 consensus ({esc(sample)}):</div><pre class='seq'>&gt;{esc(sample)}_VP1\n{esc(vp1_seq)}</pre>")

    h.append("<h2>Method and versions</h2>")
    h.append(kv([("Pipeline", f"vsnp_gui FMDV mode {esc(ver.get('fmdv_gui'))}; {esc(ver.get('aligner'))}; {esc(ver.get('caller'))}"),
                 ("Reference library", esc(ver.get("library"))),
                 ("Typing database", esc(ver.get("typing_db") or "-") + "; " + esc(ver.get("context"))),
                 ("Reference choice", "competitive minimap2 of a read sample against the VP1 panel of the lineage genomes, whole genomes as fallback (after NanoFMDV, Shaw et al. 2025)"),
                 ("Typing", "blastn of the VP1 consensus against the GenBank VP1 sets; the nearest annotated record within 3% names the lineage"),
                 ("Consensus", "reference-anchored: vsnp3 substitution calls, N below depth 10 or where the reads disagree; no indels")]))
    h.append("<div class='small muted' style='margin-top:10px'>Caveats: the consensus carries substitutions only, so insertions and deletions relative to the reference are not called and a true deletion reads as reference sequence. A lineage is provisional when the nearest records are far from the sample or no annotated record is within 3% of the best hit. A sample flagged mixed carries a second serotype in its VP1 reads; its consensus is that of the majority with the disputed sites masked.</div>")
    h.append("</body></html>")
    info = {"sample": sample, "typed": typed, "tree_run": run.name if run else None, "verdict": verdict["level"] if verdict else None}
    return "\n".join(h), info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample-dir")
    g.add_argument("--project")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--samples", nargs="*", help="with --project: only these samples")
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    lib = Path(a.lib).expanduser().resolve()
    if a.sample_dir:
        dirs = [Path(a.sample_dir).resolve()]
        project = dirs[0].parent.parent
    else:
        project = Path(a.project).expanduser().resolve()
        step1 = project / "step1"
        dirs = sorted(d for d in step1.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))
                      and (d / "fmdv_reference.json").is_file() and (not a.samples or d.name in a.samples))
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        HTML = None
    n = 0
    for d in dirs:
        try:
            page, info = build(d, lib, project)
        except Exception as e:
            _err(f"sample_report: {d.name}: failed: {type(e).__name__}: {e}")
            continue
        out = d / f"{d.name}_FMDV_report.html"
        out.write_text(page)
        pdf = ""
        if HTML and not a.no_pdf:
            try:
                HTML(string=page, base_url=str(d)).write_pdf(str(d / f"{d.name}_FMDV_report.pdf"))
                pdf = " + pdf"
            except Exception as e:
                _err(f"sample_report: {d.name}: pdf failed: {e}")
        n += 1
        _err(f"sample_report: {d.name}: written{pdf}" + (f" (tree run {info['tree_run']})" if info.get("tree_run") else " (no tree yet)"))
    _err(f"sample_report: {n} report(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
