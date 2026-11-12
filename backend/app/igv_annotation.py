"""The reference annotation, rewritten so igv.js names every gene.

No GFF in the vsnp3 reference set declares itself: each starts at
``##sequence-region``, with no ``##gff-version 3`` line. igv.js takes a GFF's
attribute syntax from that line or, failing it, from the track's ``format``.
The IGV window asked for ``format: "gff"`` on a ``.gff`` file, so igv.js read
column 9 as GFF2 (``key value``), found no key in ``ID=...;Name=pepC;...``, and
drew every gene with no name; its popup was down to Type, Source, Phase and
Location. The SNP tables were unaffected because vsnp3 annotates them from the
GenBank file.

Declaring GFF3 gets the names through, but NCBI's layout still draws badly.
Each gene is a ``gene`` line plus a ``CDS`` line whose Parent is the gene, with
no mRNA between them, and igv.js combines parts only under a transcript. So the
CDS gets its own row, labelled with protein IDs (SIT99422.1), above the row of
gene names (pepC). The ``region`` line, as long as the chromosome, takes a third
row.

So the IGV window reads this rewrite instead of the file:

* ``##gff-version 3`` comes first;
* ``region`` / ``source`` / ``chromosome`` lines are dropped, as is a trailing
  ``##FASTA`` section (IGV reads the sequence from the reference FASTA);
* a CDS whose single Parent is a gene line folds into that gene. The gene takes
  the CDS's product, protein_id and Note where it has none of its own, and the
  CDS line is dropped. A CDS that is itself some feature's Parent stays;
* a gene is labelled (``Name``) with its gene name, else its own Name, else its
  locus tag; an RNA under a gene, which has no Name of its own (a tRNA), takes
  its gene's label rather than show its raw ID.

Everything else passes through as written. The file on disk is not touched.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from stat import S_ISREG
from typing import Dict, List, Optional, Tuple, Union

_DROP_TYPES = {"region", "source", "chromosome"}
_GENE_TYPES = {"gene", "pseudogene"}
# Carried from a folded CDS onto its gene, in this order, when the gene lacks them.
_CARRY_KEYS = ("product", "protein_id", "Note")

Attrs = List[Tuple[str, str]]


def _is_gene(feature_type: str) -> bool:
    return feature_type in _GENE_TYPES or feature_type.endswith("_gene")


def _parse_attrs(column9: str) -> Attrs:
    """``key=value`` pairs in file order, values left percent-encoded."""
    attrs: Attrs = []
    for part in column9.split(";"):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        if sep:
            attrs.append((key.strip(), value.strip()))
    return attrs


def _get(attrs: Attrs, key: str) -> Optional[str]:
    for k, v in attrs:
        if k == key:
            return v or None
    return None


def _join_attrs(attrs: Attrs) -> str:
    return ";".join(f"{k}={v}" for k, v in attrs)


def igv_gff3(text: str) -> str:
    """The rewrite described in the module docstring."""
    lines = text.splitlines()
    fasta_at = next((i for i, line in enumerate(lines) if line.startswith("##FASTA")), len(lines))
    lines = lines[:fasta_at]

    rows: List[Optional[List[str]]] = []   # columns of each feature line; None for others
    genes: Dict[str, int] = {}              # gene ID -> index into rows
    parents_named = set()                   # every ID some feature names as its Parent
    for line in lines:
        cols = line.split("\t") if line and not line.startswith("#") else None
        if cols is not None and len(cols) < 9:
            cols = None
        rows.append(cols)
        if cols is None:
            continue
        attrs = _parse_attrs(cols[8])
        parent = _get(attrs, "Parent")
        if parent:
            parents_named.update(p.strip() for p in parent.split(","))
        fid = _get(attrs, "ID")
        if fid and _is_gene(cols[2]):
            genes.setdefault(fid, len(rows) - 1)

    # Which CDS lines fold, and into which gene.
    folded: Dict[int, List[Attrs]] = {}     # gene row -> attrs of its folded CDSs
    dropped = set()
    for i, cols in enumerate(rows):
        if cols is None or cols[2] != "CDS":
            continue
        attrs = _parse_attrs(cols[8])
        parent = _get(attrs, "Parent")
        if not parent or "," in parent or parent not in genes:
            continue
        if _get(attrs, "ID") in parents_named:
            continue
        folded.setdefault(genes[parent], []).append(attrs)
        dropped.add(i)

    labels: Dict[str, str] = {}             # gene ID -> its label
    for fid, i in genes.items():
        attrs = _parse_attrs(rows[i][8])
        cds_attrs = folded.get(i, [])
        label = (_get(attrs, "gene")
                 or next((_get(c, "gene") for c in cds_attrs if _get(c, "gene")), None)
                 or _get(attrs, "Name")
                 or _get(attrs, "locus_tag")
                 or next((_get(c, "locus_tag") for c in cds_attrs if _get(c, "locus_tag")), None))
        if label:
            labels[fid] = label

    out = ["##gff-version 3"]
    for i, (line, cols) in enumerate(zip(lines, rows)):
        if cols is None:
            if not line.startswith("##gff-version"):
                out.append(line)
            continue
        if i in dropped or cols[2] in _DROP_TYPES:
            continue
        attrs = _parse_attrs(cols[8])
        if _is_gene(cols[2]):
            cds_attrs = folded.get(i, [])
            for key in _CARRY_KEYS:
                if _get(attrs, key) is None:
                    value = next((_get(c, key) for c in cds_attrs if _get(c, key)), None)
                    if value:
                        attrs.append((key, value))
            label = labels.get(_get(attrs, "ID") or "")
        elif (cols[2].endswith("RNA") or cols[2].endswith("transcript")) and _get(attrs, "Name") is None:
            label = labels.get(_get(attrs, "Parent") or "")
        else:
            label = None
        if label:
            attrs = [(k, v) for k, v in attrs if k != "Name"]
            at = 1 if attrs and attrs[0][0] == "ID" else 0
            attrs.insert(at, ("Name", label))
            line = "\t".join(cols[:8] + [_join_attrs(attrs)])
        elif _is_gene(cols[2]):
            line = "\t".join(cols[:8] + [_join_attrs(attrs)])
        out.append(line)
    return "\n".join(out) + "\n"


# (resolved path, size, mtime_ns) -> rewritten bytes. A reference GFF is a few
# MB and changes almost never; the IGV window asks for it on every open.
_CACHE: "OrderedDict[Tuple[str, int, int], bytes]" = OrderedDict()
_CACHE_MAX = 4
_LOCK = threading.Lock()


def igv_gff3_bytes(path: Union[str, Path]) -> bytes:
    """The rewrite of the GFF at `path`, as UTF-8. FileNotFoundError when there
    is no regular file there."""
    st = os.stat(path)
    if not S_ISREG(st.st_mode):
        raise FileNotFoundError(str(path))
    key = (str(path), st.st_size, st.st_mtime_ns)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", errors="surrogateescape")
    body = igv_gff3(text).encode("utf-8", errors="surrogateescape")
    with _LOCK:
        _CACHE[key] = body
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return body
