"""The reference metadata file, read as an index from display name to sample.

vSNP3 renames every sample it reports using the reference directory's
``*_metadata.xlsx`` — column A the name the sample is stored under, column B
the name to print. So a sample that Step 1 ran as ``EPI-ISL-19152935``, whose
BAM and VCF are on disk under that name, appears in every Step 2 SNP table and
tree as ``GISAID-19152935_FAV-0863-5_Razorbill_2022_CAN-NL``.

Every feature that has to get from one of those spellings back to the other was
guessing. ``xlsx_html._canonical_stem`` carries four increasingly desperate
string heuristics — exact, prefix, separator-flattened, leading-id — and they
work only while the metadata name CONTAINS the original. The moment the
metadata rewrites the id itself (``EPI-ISL-…`` to ``GISAID-…``) no rule over
the two strings can bridge them, and the consequences are silent: the SNP
table's cells stop linking to the sample's BAM, so a SNP cannot be opened in
IGV, and a pasted list of names from a SNP table's first column matches
nothing.

The mapping is not a guess. It is written down, in the file vSNP3 renamed from.
This module reads it once per (file, mtime, size) and hands back O(1) lookups
in both directions, so consulting it costs nothing per row — see `resolve`,
which is tried BEFORE the heuristics and so also removes their per-row scan
over every known sample on the tables where it hits.

Nothing here raises: a reference with no metadata file, an unreadable one, or
one in an unexpected shape yields an empty index, and every caller degrades to
exactly the behaviour it had before this module existed.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("uvicorn.error")

# A metadata sheet is one row per sample. 200k rows is far past any real
# reference set (the largest in service is ~40k) and stops a mis-shaped
# workbook — a full SNP matrix saved under a *metadata* name — from being
# walked to its last cell on a request thread.
MAX_ROWS = 200_000

# (path, mtime_ns, size) -> NameAliases. Small: one entry per reference the
# process has been asked about, and the key changes the moment the file does,
# so an edit through the Reference Editor is picked up on the next request
# without any invalidation call.
_CACHE: Dict[tuple, "NameAliases"] = {}
_CACHE_MAX = 8


def _norm(s: str) -> str:
    """Case and separator folded, for the tolerant lookups only.

    Underscore and dash as one character, matching `xlsx_html._flatten_sep`:
    Step 1 dashes a sample prefix when it stages reads, so the same specimen is
    routinely spelled both ways.
    """
    return s.strip().lower().replace("_", "-")


def _strip_vcf(s: str) -> str:
    out = s.strip()
    for suffix in (".vcf.gz", ".vcf", "_zc"):
        if out.lower().endswith(suffix):
            out = out[: -len(suffix)]
    return out


# --- vsnp3's own two transformations, mirrored exactly -----------------------
#
# The index answers questions about names AS VSNP3 PRINTS THEM and AS VSNP3
# MATCHES THEM, so both of vsnp3's rules are reproduced here rather than
# approximated. Source: vsnp3_group_on_defining_snps.py (Group.__init__ for the
# display cleaning, Group.resolve_sample_name_detail for the file matching).

# Characters vsnp3 replaces with "_" in a display name before it reaches any
# table or tree, after folding accented characters to ASCII.
_VSNP3_ILLEGAL = ['\t', '\r', ' ', ':', ',', ')', '(', ';', ']', '[', "'",
                  '/', '.', '*', '?', '{', '}']


def vsnp3_display_name(raw: str) -> str:
    """Column B as vsnp3 writes it into its output.

    `GISAID-19152935 FAV-0863-5 Razorbill 2022 CAN/NL` in the workbook is
    `GISAID-19152935_FAV-0863-5_Razorbill_2022_CAN_NL` in every table, so a
    lookup keyed on the workbook's spelling alone would miss the name that is
    actually on screen.
    """
    text = unicodedata.normalize("NFD", str(raw))
    text = text.encode("ascii", errors="ignore").decode("ascii")
    for ch in _VSNP3_ILLEGAL:
        text = text.replace(ch, "_")
    text = re.sub("_+", "_", text)
    text = re.sub(r"_$", "", text)
    text = re.sub(r"-$", "", text)
    return text


def vsnp3_file_keys(column_a: str) -> List[str]:
    """Every spelling of a VCF file name that vsnp3 would match to column A.

    vsnp3 compares the file's basename to column A exactly, then strips
    `.vcf`, then `_zc`, then `_zc_*`, cumulatively, retrying after each. So a
    row whose column A is `EPI-ISL-19152935` matches the file
    `EPI-ISL-19152935_zc.vcf`; the index has to accept the sample under every
    one of those forms. (`.vcf$` is kept as vsnp3 has it, with `.` matching any
    character — widening it would change which names strip.)
    """
    out: List[str] = []
    cur = str(column_a).strip()
    if cur.lower().endswith(".gz"):
        cur = cur[:-3]
    for strip in (None, ".vcf$", "_zc$", "_zc_.*$"):
        if strip is not None:
            cur = re.sub(strip, "", cur)
        if cur and cur not in out:
            out.append(cur)
    return out


class NameAliases:
    """Two-way index between stored sample names and metadata display names.

    Exact spellings win over folded ones, and a folded spelling that two
    different samples share answers nothing at all — an ambiguous alias is
    worse than no alias, because it links a SNP to another specimen's reads.
    """

    __slots__ = ("path", "n_rows", "_pairs", "_orig_by_disp", "_disp_by_orig",
                 "_orig_by_disp_n", "_disp_by_orig_n", "_ambiguous")

    def __init__(self, pairs: Iterable[Tuple[str, str]] = (), path: str = ""):
        self.path = path
        # Kept so indexes from different sources can be folded into one — the
        # reference metadata and the GUI's own tree relabelling are two
        # renamings of the same samples, and a caller should not have to ask
        # twice.
        self._pairs: List[Tuple[str, str]] = list(pairs)
        pairs = self._pairs
        self._orig_by_disp: Dict[str, str] = {}
        self._disp_by_orig: Dict[str, str] = {}
        self._orig_by_disp_n: Dict[str, str] = {}
        self._disp_by_orig_n: Dict[str, str] = {}
        self._ambiguous: set = set()
        n = 0
        for raw_original, display in pairs:
            raw_original = (raw_original or "").strip()
            display = (display or "").strip()
            if not raw_original or not display:
                continue
            n += 1
            # Column A of a real metadata workbook names the VCF FILE, suffix
            # and all — `SRR1791698_zc.vcf`, not `SRR1791698`. Everything that
            # asks this index a question is holding a sample name, so the
            # most-stripped form is what the index answers with, and every
            # form vsnp3 itself would have matched is accepted as a key.
            keys_a = vsnp3_file_keys(raw_original)
            original = keys_a[-1]
            # Column B as printed, plus as written: what is on screen is the
            # cleaned form, what is in the search box may be either.
            printed = vsnp3_display_name(display)
            display_forms = {display, printed, _strip_vcf(display), _strip_vcf(printed)}
            for d in display_forms:
                if d:
                    self._orig_by_disp.setdefault(d, original)
            for k in keys_a:
                self._disp_by_orig.setdefault(k, printed)
            for dn in {_norm(d) for d in display_forms if d}:
                # Two display names folding together are only a problem when
                # they name DIFFERENT samples; the same pair written twice
                # is not.
                prev = self._orig_by_disp_n.get(dn)
                if prev is not None and prev != original:
                    self._ambiguous.add(dn)
                else:
                    self._orig_by_disp_n[dn] = original
            for k in keys_a:
                self._disp_by_orig_n.setdefault(_norm(k), printed)
        self.n_rows = n

    def __len__(self) -> int:
        return len(self._orig_by_disp)

    def __bool__(self) -> bool:
        return bool(self._orig_by_disp)

    def original_of(self, label: str) -> Optional[str]:
        """The stored sample name for a metadata display name, or None."""
        if not label:
            return None
        label = str(label).strip()
        hit = self._orig_by_disp.get(label)
        if hit is not None:
            return hit
        bare = _strip_vcf(label)
        if bare != label:
            hit = self._orig_by_disp.get(bare)
            if hit is not None:
                return hit
        key = _norm(bare)
        if key in self._ambiguous:
            return None
        return self._orig_by_disp_n.get(key)

    def display_of(self, original: str) -> Optional[str]:
        """The name vsnp3 prints for a stored sample name, or None.

        The PRINTED form (see vsnp3_display_name), not column B verbatim: this
        is what the tables, trees and summary show, so it is what a search or a
        pasted list will contain.
        """
        if not original:
            return None
        original = str(original).strip()
        hit = self._disp_by_orig.get(original)
        if hit is not None:
            return hit
        bare = _strip_vcf(original)
        if bare != original:
            hit = self._disp_by_orig.get(bare)
            if hit is not None:
                return hit
        return self._disp_by_orig_n.get(_norm(bare))

    def counterparts(self, token: str) -> List[str]:
        """Every other spelling of `token`, in either direction.

        For search and for list matching, where the user may type either name
        and the thing being searched may hold either.
        """
        out: List[str] = []
        seen = {token}
        for got in (self.original_of(token), self.display_of(token)):
            if got and got not in seen:
                seen.add(got)
                out.append(got)
        return out


    @classmethod
    def combine(cls, *indexes: "NameAliases") -> "NameAliases":
        """One index over every pair of the given ones, first source first."""
        pairs: List[Tuple[str, str]] = []
        paths: List[str] = []
        for idx in indexes:
            if idx:
                pairs.extend(idx._pairs)
                if idx.path:
                    paths.append(idx.path)
        return cls(pairs, path=" + ".join(paths))


EMPTY = NameAliases()


_WORD_SPLIT = re.compile(r"[^A-Za-z0-9_]+")


class PrefixLabelAliases:
    """The GUI's tree-tip relabelling (vcf_refs.csv) as an alias index, undone
    on demand rather than expanded over the project.

    The relabel script rewrites every whole-word occurrence of a csv accession
    as ``<label>_<accession>`` — so the tip for the sample stored as
    ``ERR015582-trim200`` reads ``Orygis_ERR015582-trim200``. The first version
    of this ran that substitution over every sample name in the project, which
    meant listing 80,000 directory entries and rebuilding whenever a Step 1
    batch created a folder: 0.6 s per SNP-table preview for as long as a batch
    ran. Undoing the substitution on the name being asked about needs only the
    csv, so this index depends on nothing but that file.

    A label whose accession the project does not hold still resolves, to the
    accession: every consumer checks the answer against the samples that exist
    (`_canonical_stem` against its known stems, `_resolve_sample_dir` against
    the directory), so a spurious answer costs a dictionary miss, not a wrong
    link.
    """

    __slots__ = ("path", "_labels_by_ident", "_strip", "_rx")

    def __init__(self, rows: Iterable[Tuple[str, str]], path: str = ""):
        """`rows` are (accession, friendly label) — several per accession when
        more than one label style is in play."""
        self.path = path
        self._labels_by_ident: Dict[str, List[str]] = {}
        self._strip: Dict[str, int] = {}          # label -> len("friendly_")
        for ident, friendly in rows:
            ident = str(ident or "").strip()
            friendly = str(friendly or "").strip()
            if not ident or not friendly:
                continue
            label = f"{friendly}_{ident}"
            labs = self._labels_by_ident.setdefault(ident, [])
            if label not in labs:
                labs.append(label)
            self._strip.setdefault(label, len(friendly) + 1)
        keys = sorted(self._strip, key=len, reverse=True)
        # A label, standing as a whole word, anywhere in the name — the same
        # boundary the script's \b…\b substitution used.
        self._rx = (re.compile(r"(?:^|(?<=[^A-Za-z0-9_]))(?:"
                               + "|".join(re.escape(k) for k in keys)
                               + r")(?=$|[^A-Za-z0-9_])")
                    if keys else None)

    def __bool__(self) -> bool:
        return bool(self._strip)

    def __len__(self) -> int:
        return len(self._strip)

    def original_of(self, label: str) -> Optional[str]:
        """The stored name a relabelled tip stands for, or None."""
        if not label or self._rx is None:
            return None
        label = str(label).strip()
        m = self._rx.search(label)
        if not m:
            return None
        stored = label[:m.start()] + label[m.start() + self._strip[m.group(0)]:]
        return stored if stored and stored != label else None

    def displays_of(self, stored: str) -> List[str]:
        """Every label the script would give this stored name, one per style."""
        if not stored or not self._labels_by_ident:
            return []
        stored = str(stored).strip()
        out: List[str] = []
        for tok in [stored, *_WORD_SPLIT.split(stored)]:
            labs = self._labels_by_ident.get(tok)
            if not labs:
                continue
            for lab in labs:
                shown = re.sub(rf"\b{re.escape(tok)}\b", lab, stored)
                if shown != stored and shown not in out:
                    out.append(shown)
            break
        return out

    def display_of(self, stored: str) -> Optional[str]:
        shown = self.displays_of(stored)
        return shown[0] if shown else None

    def counterparts(self, token: str) -> List[str]:
        out: List[str] = []
        stored = self.original_of(token)
        if stored and stored != token:
            out.append(stored)
        for d in self.displays_of(token):
            if d != token and d not in out:
                out.append(d)
        return out


class ChainedAliases:
    """Several alias indexes asked in turn; the first answer wins.

    Replaces merging them into one NameAliases, which re-ran that constructor
    over every metadata row each time any source changed.
    """

    __slots__ = ("path", "_parts")

    def __init__(self, *parts):
        self._parts = [p for p in parts if p]
        self.path = " + ".join(str(getattr(p, "path", "")) for p in self._parts if getattr(p, "path", ""))

    def __bool__(self) -> bool:
        return bool(self._parts)

    def __len__(self) -> int:
        return sum(len(p) for p in self._parts)

    def original_of(self, label: str) -> Optional[str]:
        for p in self._parts:
            got = p.original_of(label)
            if got:
                return got
        return None

    def display_of(self, stored: str) -> Optional[str]:
        for p in self._parts:
            got = p.display_of(stored)
            if got:
                return got
        return None

    def counterparts(self, token: str) -> List[str]:
        out: List[str] = []
        for p in self._parts:
            for c in p.counterparts(token):
                if c != token and c not in out:
                    out.append(c)
        return out


def _read_pairs(meta_path: Path) -> List[Tuple[str, str]]:
    """Columns A and B of the first worksheet, as (original, display).

    openpyxl in read_only mode rather than the pandas subprocess the Reference
    Editor's own metadata endpoints use: this runs inside request handling, and
    a 40k-row sheet is a few hundred milliseconds of streaming here against
    seconds of interpreter start plus DataFrame build there.
    """
    import openpyxl
    wb = openpyxl.load_workbook(meta_path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        pairs: List[Tuple[str, str]] = []
        for i, row in enumerate(ws.iter_rows(min_col=1, max_col=2, values_only=True)):
            if i >= MAX_ROWS:
                logger.warning("metadata %s: stopped reading at %d rows",
                               meta_path, MAX_ROWS)
                break
            if not row:
                continue
            a = row[0]
            b = row[1] if len(row) > 1 else None
            if a is None or b is None:
                continue
            pairs.append((str(a), str(b)))
        return pairs
    finally:
        wb.close()


def load(meta_path: Optional[Path]) -> NameAliases:
    """The index for one metadata workbook. Cached; never raises."""
    if not meta_path:
        return EMPTY
    meta_path = Path(meta_path)
    try:
        st = meta_path.stat()
    except OSError:
        return EMPTY
    key = (str(meta_path), st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        idx = NameAliases(_read_pairs(meta_path), path=str(meta_path))
    except Exception as e:                      # a corrupt or odd workbook
        logger.warning("metadata %s could not be read as a name map: %s: %s",
                       meta_path, type(e).__name__, e)
        idx = EMPTY
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = idx
    return idx


def metadata_file(ref_dir: Optional[Path]) -> Optional[Path]:
    """The reference directory's metadata workbook, if it has one."""
    if not ref_dir:
        return None
    try:
        for f in sorted(Path(ref_dir).glob("*meta*xlsx")):
            if not f.name.startswith("~$"):
                return f
    except OSError:
        return None
    return None


def for_reference_dir(ref_dir: Optional[Path]) -> NameAliases:
    """The index for a reference directory, empty when it has no metadata."""
    return load(metadata_file(ref_dir))
