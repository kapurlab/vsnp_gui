#!/usr/bin/env python3
"""The run report: one page for the outbreak team, every sample's call on
one table, with the serotype trees the samples sit in.

Written for the people who decide what to do with the result, not for the
laboratory: what serotypes and lineages are present, which samples share a
lineage, which calls are provisional or mixed and so need a repeat or a
second look, and where the lineages' nearest relatives were seen. The QC
detail stays in the sample reports; here a sample carries its verdict and
its genome called percent, and a note in words when either should change
how the call is read.

Reads the sample folders (fmdv_vp1_type.json, fmdv_reference.json,
fmdv_consensus.json, the stats workbook) and the latest VP1 tree run per
serotype. Writes <project>/FMDV_run_report.html and .pdf.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fmdv_sample_report as R  # noqa: E402
import fmdv_vp1_tables as T  # noqa: E402

esc, pct, load_json = R.esc, R.pct, R.load_json


def latest_serotype_runs(project: Path) -> Dict[str, Path]:
    """Newest VP1 tree run per serotype (runs restricted to one serotype)."""
    out: Dict[str, Path] = {}
    step2 = project / "step2"
    if not step2.is_dir():
        return out
    for d in sorted(step2.iterdir(), reverse=True):
        m = re.search(r"_VP1-tree-([A-Za-z0-9]+)$", d.name)
        if d.is_dir() and m and m.group(1) not in out and (d / "VP1" / "VP1_neighbourhood.tre").is_file():
            out[m.group(1)] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    project = Path(a.project).expanduser().resolve()
    lib = Path(a.lib).expanduser().resolve()
    step1 = project / "step1"
    dirs = sorted(d for d in step1.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))
                  and (d / "fmdv_reference.json").is_file()) if step1.is_dir() else []
    proj = load_json(project / "project.json")
    thr = proj.get("qc_thresholds") or {}

    rows: List[Dict] = []
    for d in dirs:
        s = d.name
        ref = load_json(d / "fmdv_reference.json"); typ = load_json(d / "fmdv_vp1_type.json")
        cons = load_json(d / "fmdv_consensus.json"); srow = R.stats_row(d, s)
        chosen = ref.get("chosen") or {}
        verdict = None
        if R.compute_verdict and srow:
            r2 = dict(srow); reasons = []
            if ref.get("distant"): reasons.append("distant reference")
            if ref.get("mixed"): reasons.append("mixed serotypes")
            if reasons: r2["_contamination"] = True; r2["_contamination_reason"] = "; ".join(reasons)
            try: verdict = R.compute_verdict(r2, R.merge_thresholds(thr))
            except Exception: verdict = None
        typed = typ.get("status") == "typed"
        best = typ.get("best") or {}
        notes = []
        if ref.get("mixed"):
            sh = ref.get("serotype_share") or {}
            notes.append("two serotypes in the reads (" + ", ".join(f"{k} {v:.0%}" for k, v in list(sh.items())[:2]) + "): repeat or plaque-purify before the call is used")
        if ref.get("distant"):
            notes.append("no close genome in the library: lineage call provisional")
        if typed and not typ.get("lineage"):
            notes.append("no annotated record within 3% of the nearest: lineage unassigned")
        if typ.get("note"):
            notes.append("VP1 typing and reads vote disagree on the serotype")
        if verdict and verdict["level"] == "fail":
            notes.append("QC fail (" + "; ".join(verdict["reasons"]) + "): not reportable without a repeat")
        elif verdict and verdict["level"] == "review" and not notes:
            notes.append("QC review: " + "; ".join(verdict["reasons"]))
        if not typed:
            notes.append(f"VP1 not typed: {typ.get('status') or 'no result'}")
        rows.append({
            "sample": s, "serotype": typ.get("serotype") if typed else (chosen.get("serotype") or ""),
            "lineage": typ.get("lineage") if typed else "", "typed": typed,
            "nearest": f"{best.get('country', '')} {best.get('year', '') if str(best.get('year')) not in ('0', 'None', '') else ''}".strip() or "-",
            "nearest_acc": best.get("accession", ""), "identity": typ.get("identity"),
            "called": cons.get("called_percent"), "verdict": verdict["level"] if verdict else "-",
            "notes": notes, "date": srow.get("date") or "",
        })

    # Summary in words
    by_sero = Counter(r["serotype"] or "untyped" for r in rows)
    by_lineage = Counter((r["serotype"], r["lineage"]) for r in rows if r["typed"] and r["lineage"])
    flagged = [r for r in rows if r["notes"]]
    runs = latest_serotype_runs(project)

    def sero_sentence():
        parts = [f"{n} {s}" for s, n in sorted(by_sero.items(), key=lambda x: -x[1])]
        return ", ".join(parts)

    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(project.name)} FMDV run report</title><style>{R.CSS}</style></head><body>"]
    h.append(f"<h1>FMDV run report: {esc(project.name)}</h1><div class='muted'>{len(rows)} samples · written {time.strftime('%Y-%m-%d %H:%M')}</div>")
    h.append("<h2>Summary</h2>")
    lines = []
    lines.append(f"{len(rows)} sample{'s' if len(rows) != 1 else ''} sequenced: {sero_sentence()}.")
    if by_lineage:
        lin = "; ".join(f"{s} {l}: {n}" for (s, l), n in sorted(by_lineage.items(), key=lambda x: -x[1]))
        lines.append(f"Lineages by VP1: {lin}.")
        shared = [(k, n) for k, n in by_lineage.items() if n > 1]
        if shared:
            lines.append("Samples sharing a lineage: " + "; ".join(f"{s} {l} ({n})" for (s, l), n in shared) + ". Whether they are the same introduction is a whole-genome question; see the Genome SNPs comparison on that lineage's reference.")
    if flagged:
        lines.append(f"{len(flagged)} sample{'s' if len(flagged) != 1 else ''} carr{'y' if len(flagged) != 1 else 'ies'} a note that changes how the call is read (table).")
    else:
        lines.append("Every call is clean: QC pass, one serotype, an annotated relative within 3%.")
    h.append("<p>" + " ".join(esc(x) for x in lines) + "</p>")

    h.append("<h2>Calls</h2>")
    h.append("<table><tr><th>Sample</th><th>Serotype</th><th>Lineage (VP1)</th><th>Nearest relative</th><th>Identity</th><th>Genome called</th><th>QC</th><th>Note</th></tr>")
    for r in rows:
        v = r["verdict"]
        h.append(f"<tr><td><b>{esc(r['sample'])}</b></td><td>{esc(r['serotype'] or '-')}</td><td>{esc(r['lineage'] or ('unassigned' if r['typed'] else '-'))}</td>"
                 f"<td>{(esc(r['nearest']) + ' · ') if r['nearest'] != '-' else ''}{esc(r['nearest_acc']) if r['nearest_acc'] else ('-' if r['nearest'] == '-' else '')}</td><td>{pct(r['identity'])}</td><td>{pct(r['called'])}</td>"
                 f"<td><span class='flag {esc(v)}'>{esc(v)}</span></td><td class='small'>{'; '.join(esc(n) for n in r['notes']) or ''}</td></tr>")
    h.append("</table>")
    h.append("<div class='small muted'>Serotype and lineage from the VP1 consensus against the GenBank VP1 sets. Identity is to the nearest record. Genome called is the share of the genome with a confident consensus base; the pass line is 90%, the fail line 70%. Each sample has its own report with the QC detail, the neighbourhood and the sequence.</div>")

    for sero, run in sorted(runs.items()):
        in_run = [r["sample"] for r in rows if r["serotype"] == sero]
        tre = run / "VP1" / "VP1_neighbourhood.tre"
        try:
            svg = R.tree_svg(tre.read_text(), set(in_run))
        except Exception as e:
            svg = f"<p class='muted'>Tree could not be drawn: {esc(e)}</p>"
        h.append(f"<h2>{esc(sero)} samples among their relatives</h2><div class='small muted'>VP1 tree run {esc(run.name)}: the samples (highlighted), every record within 3% of each, and one record per other {esc(sero)} topotype. Midpoint-rooted; bootstrap 50 and over shown. Labels: lineage|country|year|accession.</div><div>{svg}</div>")
    if not runs and rows:
        h.append("<h2>Trees</h2><p class='muted'>No VP1 tree has been built for this project yet; build one per serotype from the Step 2 VP1 tab and this report is rewritten.</p>")

    ver = R.versions(project, lib, {})
    h.append("<h2>Method</h2>")
    h.append(f"<div class='small'>Reads were mapped to the closest of {esc(ver.get('library'))}; the VP1 consensus was typed by blastn against {esc(ver.get('typing_db') or 'the GenBank VP1 sets')}. Pipeline vsnp_gui FMDV mode {esc(ver.get('fmdv_gui'))}. A lineage is provisional when no annotated record lies within 3% of the sample or the reads sit far from every library genome. The consensus carries substitutions only.</div>")
    h.append("</body></html>")
    page = "\n".join(h)
    out = project / "FMDV_run_report.html"
    out.write_text(page)
    msg = f"run_report: {out}"
    if not a.no_pdf:
        try:
            from weasyprint import HTML  # type: ignore
            HTML(string=page, base_url=str(project)).write_pdf(str(project / "FMDV_run_report.pdf"))
            msg += " + pdf"
        except Exception as e:
            msg += f" (pdf failed: {e})"
    print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
