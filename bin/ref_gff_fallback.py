#!/usr/bin/env python3
"""Fetch a nucleotide record's GFF3 from NCBI's sequence viewer.

Download New Reference gets the GFF through vsnp3, which asks E-utilities
(efetch, rettype gff3). That request sometimes answers HTTP 500 for a record
whose FASTA and GenBank arrive without trouble (NC_003197.2, 2026-11-24,
four tries over two minutes), while the sequence viewer serves the same GFF3.
The download job runs this when the vsnp3 step fails.

    ref_gff_fallback.py NC_003197.2 NC_003197.2.gff

The viewer answers HTTP 200 with a one-line "Error:" body for an id it does
not know, so the body is checked before anything is written: it must open
with "##gff-version 3" and carry at least one feature line. The file is
written beside its final name and moved into place, so a failure leaves no
partial GFF. Exit 0 with the file written, 1 otherwise.

Standard library only. No contact email is sent: the viewer does not ask for
one, and it would travel in the URL.

VSNP_GUI_GFF_FALLBACK_URL replaces the address, with {acc} where the
accession goes (the tests point it at a local file).
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = ("https://www.ncbi.nlm.nih.gov/sviewer/viewer.cgi"
               "?db=nuccore&report=gff3&id={acc}&retmode=text")
ATTEMPTS = 2
TIMEOUT_S = 180


def problem_with(text: str) -> str:
    """Why `text` is not a usable GFF3, or "" when it is."""
    if not text.startswith("##gff-version 3"):
        first = text.strip().splitlines()[0][:200] if text.strip() else "an empty reply"
        return f"the reply is not GFF3 ({first})"
    for line in text.splitlines():
        if line and not line.startswith("#") and len(line.split("\t")) == 9:
            return ""
    return "the GFF3 holds no feature"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "vsnp_gui"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main(argv: list) -> int:
    if len(argv) != 3:
        print("usage: ref_gff_fallback.py <accession> <output.gff>", file=sys.stderr)
        return 1
    accession, out = argv[1], Path(argv[2])
    template = os.environ.get("VSNP_GUI_GFF_FALLBACK_URL", "").strip() or DEFAULT_URL
    url = template.replace("{acc}", urllib.parse.quote(accession, safe=""))
    why = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            text = fetch(url)
            why = problem_with(text)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            text, why = "", f"{type(exc).__name__}: {exc}"
        if not why:
            tmp = out.with_name(out.name + ".part")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, out)
            print(f"GFF: {out.name} fetched from NCBI's sequence viewer "
                  f"({len(text.splitlines()):,} lines).")
            return 0
        print(f"GFF: attempt {attempt} of {ATTEMPTS} at NCBI's sequence viewer failed: {why}",
              file=sys.stderr)
        if attempt < ATTEMPTS:
            time.sleep(3)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
