#!/usr/bin/env python3
"""Apply the Kapur Lab minus-strand fix to a vsnp3 install's vsnp3_annotation.py.

Upstream slices the codon from the plus strand and translates it as-is — there
is no reverse_complement() anywhere in the module in either v3.16 or v3.35. For
a gene on the minus strand that yields the wrong reference and variant amino
acid, and the synonymous / nonsynonymous call becomes close to a coin flip.
Roughly half of MTBC genes are on the minus strand, so this touches about half
of all coding SNPs in every TB run, with no signature that anything is wrong.

Reported by Vivek Kapur (2026-08-01) with positive controls against H37Rv
(NC_000962.3):

    rpoB S450L  Rv0667   plus strand   761155 C>T  -> residue 450, S->L
    katG S315T  Rv1908c  minus strand  2155168 C>G -> residue 315, S->T

Unpatched v3.35 reports katG as residue 315 A->G, translating the plus-strand
codon GCT->GGT instead of the gene-strand codon AGC->ACC.

Why a rewriter and not a .patch: the two point releases we deploy (/srv carries
v3.16, the bdtools env carries v3.35) restructured this function completely, so
a line-context diff cannot apply to both. This matches content anchors instead,
emits the correction appropriate to the shape it finds, and refuses loudly if it
recognises neither.

Idempotent: a file already carrying the KL_STRAND_FIX marker (or Vivek's
hand-applied `+kl.strandfix` version string) is left untouched.

Usage: strandfix.py <path/to/vsnp3_annotation.py>
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

MARKER = "KL_STRAND_FIX"

# Anchors. Each is matched on the stripped line so indentation changes between
# releases don't break detection; the emitted block reuses the anchor's own
# indentation.
ANCHOR_SLICE_316 = "rbc_list = list(rbc)"
ANCHOR_SLICE_335 = "rbc_list = list(str(rbc))"
ANCHOR_RESIDUE_316 = "zero_index_residue = aa_residue_pos - 1"
SHAPE_335 = "self.aa_residue_pos = total_codons - codon_number"

# vsnp3 3.36's own minus-strand correction. Both anchors are required: the
# strand test alone appears in unrelated code, and it is the complement of the
# substitution that actually fixes the codon.
UPSTREAM_FIXED_ANCHOR = "feature.location.strand == -1"
UPSTREAM_FIXED_COMPLEMENT = "Seq(substitution).complement()"

_COMPLEMENT = (
    '{"A": "T", "T": "A", "G": "C", "C": "G", '
    '"a": "t", "t": "a", "g": "c", "c": "g", "N": "N", "n": "n"}'
)

# v3.16: nt_index_aa / left / right / aa_residue_pos all come from a modulo
# ladder over `nt_in_aa` that only ever counts from the plus strand, and ref_aa
# is then read out of /translation at that index — so the residue number is
# wrong on the minus strand too, not just the codon. Recompute the lot from the
# feature's own bounds, in gene orientation.
BLOCK_RESIDUE_316 = '''# --- BEGIN {marker} (Kapur Lab) ---
# Recompute codon number, codon bounds and the index within the codon from the
# feature's own coordinates, counting from the gene's 5' end rather than the
# plus strand. On a plus-strand gene this reproduces the values above; on a
# minus-strand gene the upstream ladder numbers the residue from the wrong end.
_kl_minus = (getattr(part, "strand", 1) == -1)
_kl_offset = (int(part.end) - position) if _kl_minus else ((position - 1) - int(part.start))
_kl_codon = _kl_offset // 3
nt_index_aa = _kl_offset % 3          # index within the codon, gene orientation
aa_residue_pos = _kl_codon + 1
if _kl_minus:
    right = int(part.end) - 3 * _kl_codon
    left = right - 3
else:
    left = int(part.start) + 3 * _kl_codon
    right = left + 3
self.aa_pos = nt_index_aa + 1
self.aa_residue_pos = aa_residue_pos
self.direction = "reverse gene" if _kl_minus else "forward gene"
# --- END {marker} ---
'''

# v3.16 codon flip. nt_index_aa is already gene-oriented by the block above, so
# only the codon itself and the substituted base need complementing.
BLOCK_FLIP_316 = '''# --- BEGIN {marker} (Kapur Lab) ---
# The slice above is plus-strand. Put it in gene orientation and complement the
# variant base so the substitution lands on the right strand.
if _kl_minus:
    rbc = rbc.reverse_complement()
    if snp_nt:
        snp_nt = {complement}.get(snp_nt, snp_nt)
# --- END {marker} ---
'''

# v3.35 already derives aa_residue_pos correctly on the minus strand
# (total_codons - codon_number) and takes ref_aa from the codon table rather
# than /translation, so only the codon handling is wrong. nt_index_aa here is
# still a plus-strand index into the codon, hence the 2 - i flip.
BLOCK_FLIP_335 = '''# --- BEGIN {marker} (Kapur Lab) ---
# The slice above is plus-strand and was previously translated as-is. Put it in
# gene orientation, mirror the index within the codon, and complement the
# variant base so the substitution lands on the right strand.
_kl_minus = (getattr(part, "strand", 1) == -1)
if _kl_minus:
    rbc = rbc.reverse_complement()
    nt_index_aa = 2 - nt_index_aa
    if snp_nt:
        snp_nt = {complement}.get(snp_nt, snp_nt)
    self.direction = "reverse gene"
# --- END {marker} ---
'''


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _insert_before(lines: list[str], anchor: str, block: str) -> list[str]:
    """Insert `block` immediately above the first line that starts with
    `anchor`, indented to match it. startswith rather than == because v3.35
    carries trailing `# Convert Bio.Seq to string first` style comments."""
    for i, line in enumerate(lines):
        if line.strip().startswith(anchor):
            pad = _indent_of(line)
            rendered = [
                (pad + b if b.strip() else "")
                for b in block.format(marker=MARKER, complement=_COMPLEMENT).splitlines()
            ]
            return lines[:i] + rendered + lines[i:]
    raise LookupError(f"anchor not found: {anchor!r}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"strandfix: not a file: {path}", file=sys.stderr)
        return 1

    src = path.read_text(encoding="utf-8")
    version = "?"
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
    if m:
        version = m.group(1)

    if MARKER in src or "kl.strandfix" in version:
        print(f"strandfix: already applied ({version}) at {path}")
        return 0

    # FIXED UPSTREAM. vsnp3 3.36 corrects the minus-strand codon itself — it
    # complements the ALT before placing it in a gene-oriented codon, which is
    # the same correction this script injects into 3.16/3.35. Recognise that and
    # stand down: without this, 3.36 matched no known anchor, strandfix returned
    # 1, and apply.sh (set -e) aborted the whole patch step — so moving to the
    # release that fixes the bug looked like a broken install.
    if UPSTREAM_FIXED_ANCHOR in src and UPSTREAM_FIXED_COMPLEMENT in src:
        print(f"strandfix: minus-strand codon already correct upstream in "
              f"vsnp3 {version} at {path}; nothing to patch")
        return 0

    if "reverse_complement" in src:
        # Some other patch already reverse-complements here. Refuse rather than
        # stack a second correction on top and silently double-flip.
        print(
            f"strandfix: {path} already calls reverse_complement() but carries no "
            f"{MARKER} marker — refusing to patch. Inspect it by hand.",
            file=sys.stderr,
        )
        return 1

    lines = src.splitlines()
    try:
        if ANCHOR_RESIDUE_316 in src and ANCHOR_SLICE_316 in src:
            shape = "v3.16"
            lines = _insert_before(lines, ANCHOR_RESIDUE_316, BLOCK_RESIDUE_316)
            lines = _insert_before(lines, ANCHOR_SLICE_316, BLOCK_FLIP_316)
        elif SHAPE_335 in src and ANCHOR_SLICE_335 in src:
            shape = "v3.35"
            lines = _insert_before(lines, ANCHOR_SLICE_335, BLOCK_FLIP_335)
        else:
            print(
                f"strandfix: unrecognised vsnp3_annotation.py shape ({version}) at "
                f"{path} — no known anchors. Not patching.",
                file=sys.stderr,
            )
            return 1
    except LookupError as exc:
        print(f"strandfix: {exc} in {path}", file=sys.stderr)
        return 1

    out = "\n".join(lines) + ("\n" if src.endswith("\n") else "")
    compile(out, str(path), "exec")  # never leave a syntactically broken module

    backup = path.with_suffix(path.suffix + ".pre-strandfix")
    if not backup.exists():
        shutil.copy2(path, backup)
    # Stamp the version so the install is identifiable in provenance output and
    # so a re-run of this script is a no-op.
    out = re.sub(
        r'^(__version__\s*=\s*["\'])([^"\']+)(["\'])',
        lambda mm: f"{mm.group(1)}{mm.group(2)}+kl.strandfix1{mm.group(3)}",
        out,
        count=1,
        flags=re.M,
    )

    # Write to a temp file and rename over the target rather than writing in
    # place. conda hardlinks env files to the package cache under pkgs/ — here
    # env/bin, envs/vsnp3/bin and pkgs/vsnp3-3.35-*/bin were all one inode — so
    # an in-place write would silently patch the *cache*, and every future env
    # unpacked from it would carry the patch with no record. os.replace gives
    # this path a fresh inode and leaves the other links on the original.
    links = path.stat().st_nlink
    tmp = path.with_suffix(path.suffix + ".strandfix-tmp")
    tmp.write_text(out, encoding="utf-8")
    shutil.copystat(path, tmp)
    tmp.replace(path)
    note = f", broke {links - 1} hardlink(s) to the conda cache" if links > 1 else ""
    print(
        f"strandfix: applied {shape} fix to {path} "
        f"(was {version}, backup at {backup.name}{note})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
