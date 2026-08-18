#!/usr/bin/env python3
"""Teach a vsnp3 install to honour VSNP3_BOOTSTRAP in vsnp3_fasta_to_snps_table.py.

vsnp3 runs RAxML once, for the best tree only:

    raxml -s <alignment> -n raxml -m GTRCATI -o root -w <dir> -p 456123 -T 4

so `RAxML_bestTree.raxml` carries no support values, and every tree the GUI can
open has unlabelled internal nodes.  With VSNP3_BOOTSTRAP set to N > 0 the run
becomes RAxML's rapid-bootstrap analysis (`-f a -x 7777 -N N`) and the tree kept
is `RAxML_bipartitions.raxml` — the same ML topology, with bootstrap proportions
as internal node labels.  Off by default: bootstrapping is the expensive part of
Step 2 and is not wanted on most runs.

Why a rewriter and not a .patch: this hunk has existed since 2026-05 inside
`v3.16-kapurlab.patch`, and apply.sh skips that whole patch set on anything that
is not v3.16 — so on the v3.36 install the GUI's "Bootstrap (replicates)" field
set an environment variable that nothing read, and the run silently produced the
same unsupported tree.  v3.36 also restructured the call (os.system with an
f-string became vsnp3_run.run with an argv list), so no single diff fits both.
This matches content anchors, emits the form appropriate to the shape it finds,
and refuses loudly if it recognises neither.

Idempotent: a file already carrying the KL_BOOTSTRAP marker is left untouched.

Usage: bootstrapfix.py <path/to/vsnp3_fasta_to_snps_table.py>
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

MARKER = "KL_BOOTSTRAP"

# Shape anchors, matched on the stripped line so indentation is free to move.
ANCHOR_RUN_336 = "vsnp3_run.run([raxml,"
ANCHOR_BEST_336 = "best_tree = os.path.join(write_path, 'RAxML_bestTree.raxml')"
ANCHOR_BEST_TREE = "RAxML_bestTree.raxml"      # only inspected on the os.system shape

# The replicate count is read once, here, rather than trusted: an env var set to
# junk must not take RAxML's `-N` with it and fail the whole group.
PREAMBLE = """# --- BEGIN {marker} (Kapur Lab) ---
# Bootstrap replicates, from the environment (0/unset = best tree only, the
# upstream behaviour). vsnp_gui's Step 2 form sets this.
try:
    _kl_boot = int(str(os.environ.get('VSNP3_BOOTSTRAP') or '0').strip() or '0')
except ValueError:
    _kl_boot = 0
if _kl_boot < 0:
    _kl_boot = 0
"""

TAIL = "# --- END {marker} ---"


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _block(text: str, indent: str) -> list[str]:
    return [indent + ln if ln.strip() else "" for ln in text.rstrip("\n").split("\n")]


def _find(lines: list[str], needle: str, what: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise LookupError(f"no line containing {what}")


def _statement_end(lines: list[str], start: int) -> int:
    """Index of the last line of the bracketed statement beginning at `start`.

    The argv list is wrapped across lines, and a fixed two-line assumption would
    break the moment upstream reflows it.  Counting brackets is what actually
    knows where the call ends.
    """
    depth = 0
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if depth <= 0:
            return i
    raise LookupError("unterminated vsnp3_run.run(...) call")


def _bootstrap_variant(stmt: str) -> str:
    """The same RAxML invocation, as a rapid-bootstrap analysis.

    Both edits are made against text that is identical in every release we have
    seen (`[raxml,` opening the argv list, and the `-p 456123` seed), so this
    does not need to understand the rest of the command line — which is the
    point, since the rest is what changes between releases.
    """
    out = stmt.replace("[raxml,", "[raxml, '-f', 'a',", 1)
    if out == stmt:
        raise LookupError("RAxML argv does not start with [raxml,")
    seeded = out.replace(
        "'-p', '456123',", "'-p', '456123', '-x', '7777', '-N', str(_kl_boot),", 1
    )
    if seeded == out:
        raise LookupError("RAxML argv has no \"'-p', '456123',\" seed to extend")
    return seeded


def _patch_336(lines: list[str]) -> list[str]:
    run_at = _find(lines, ANCHOR_RUN_336, "the vsnp3_run.run([raxml, …]) call")
    run_end = _statement_end(lines, run_at)
    best_at = _find(lines, ANCHOR_BEST_336, "the RAxML_bestTree.raxml path")
    if not run_end < best_at <= run_end + 2:
        raise LookupError(
            "the RAxML_bestTree.raxml assignment is not where it was expected "
            "(directly after the run call)"
        )
    indent = _indent_of(lines[run_at])
    stmt = "\n".join(lines[run_at : run_end + 1])
    boot_stmt = _bootstrap_variant(stmt)
    body = _block(PREAMBLE.format(marker=MARKER), indent)
    body.append(f"{indent}if _kl_boot > 0:")
    body += ["    " + ln if ln.strip() else "" for ln in boot_stmt.split("\n")]
    body.append(
        f"{indent}    best_tree = os.path.join(write_path, "
        f"'RAxML_bipartitions.raxml')"
    )
    body.append(f"{indent}else:")
    body += ["    " + ln if ln.strip() else "" for ln in stmt.split("\n")]
    body.append("    " + lines[best_at])
    body.append(indent + TAIL.format(marker=MARKER))
    return lines[:run_at] + body + lines[best_at + 1 :]


def _patch_os_system(lines: list[str]) -> list[str]:
    """The os.system shape: one line building a RAxML command string.

    Two releases write that string two different ways — v3.16 with an f-string,
    v3.35 with `.format(...)` — so this does not touch the string literal at all.
    It captures whatever expression os.system was given and edits the COMMAND, at
    run time, after it has been built. Every way of building it is then covered,
    including whatever the next release does.

    Only reached on installs the v3.16 .patch set never got to; where that set HAS
    been applied, the marker check in main() has already stopped us.
    """
    at = None
    for i, line in enumerate(lines):
        if "os.system(" in line and "GTRCATI" in line and not line.lstrip().startswith("#"):
            at = i
            break
    if at is None:
        raise LookupError("no os.system(...) line running RAxML with GTRCATI")
    end = _statement_end(lines, at)
    stmt = "\n".join(lines[at : end + 1])
    open_at = stmt.index("os.system(")
    arg_start = stmt.index("(", open_at) + 1
    # Back from the matching close paren, so a trailing `#> /dev/null` comment
    # (v3.16 has one) is left out of the captured expression.
    depth = 1
    arg_end = None
    for k in range(arg_start, len(stmt)):
        if stmt[k] in "([{":
            depth += 1
        elif stmt[k] in ")]}":
            depth -= 1
            if depth == 0:
                arg_end = k
                break
    if arg_end is None:
        raise LookupError("unterminated os.system(...) call")
    expr = stmt[arg_start:arg_end].strip()
    if not expr:
        raise LookupError("os.system(...) was given no argument")
    # Checked here so a release that renames the seed fails at PATCH time, where
    # the message is read, rather than at run time inside a worker pool.
    if "-s " not in stmt or "-p 456123" not in stmt:
        raise LookupError("the os.system RAxML command has no '-s' or no "
                          "'-p 456123' seed to extend")
    rename_at = _find(lines, ANCHOR_BEST_TREE, "the RAxML_bestTree.raxml path")
    if rename_at <= at:
        raise LookupError("the RAxML_bestTree.raxml path is before the RAxML call")
    indent = _indent_of(lines[at])
    # Rewritten BEFORE the insertion below, because the inserted block itself
    # contains the string 'RAxML_bestTree.raxml' — searching for the anchor
    # afterwards finds our own line and edits that instead.
    lines = list(lines)
    lines[rename_at] = lines[rename_at].replace(
        "'RAxML_bestTree.raxml'", "_kl_best"
    ).replace("RAxML_bestTree.raxml", "{_kl_best}")

    body = _block(PREAMBLE.format(marker=MARKER), indent)
    body += _block(
        "_kl_cmd = " + expr.replace("\n", " ") + "\n"
        "_kl_best = 'RAxML_bestTree.raxml'\n"
        "if _kl_boot > 0:\n"
        "    # Edited after the command has been built, so this does not depend on\n"
        "    # how this release happens to build it.\n"
        "    _kl_new = _kl_cmd.replace(' -s ', ' -f a -s ', 1).replace(\n"
        "        ' -p 456123', ' -p 456123 -x 7777 -N {}'.format(_kl_boot), 1)\n"
        "    if _kl_new == _kl_cmd:\n"
        "        import sys as _kl_sys\n"
        "        print('WARN: VSNP3_BOOTSTRAP={} requested but the RAxML command '\n"
        "              'could not be rewritten; building the best tree without '\n"
        "              'support values'.format(_kl_boot), file=_kl_sys.stderr)\n"
        "    else:\n"
        "        _kl_cmd = _kl_new\n"
        "        _kl_best = 'RAxML_bipartitions.raxml'\n"
        "os.system(_kl_cmd)",
        indent,
    )
    body.append(indent + TAIL.format(marker=MARKER))
    return lines[:at] + body + lines[end + 1 :]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"bootstrapfix: {path} not found", file=sys.stderr)
        return 1
    src = path.read_text(encoding="utf-8")
    version = "unknown"
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)', src, re.M)
    if m:
        version = m.group(1)

    if MARKER in src or "VSNP3_BOOTSTRAP" in src:
        print(f"bootstrapfix: {path} already honours VSNP3_BOOTSTRAP; nothing to do")
        return 0
    if "import os" not in src:
        print(f"bootstrapfix: {path} does not import os; not patching", file=sys.stderr)
        return 1

    lines = src.splitlines()
    try:
        if ANCHOR_RUN_336 in src:
            shape = "v3.36 (vsnp3_run.run argv)"
            lines = _patch_336(lines)
        elif "os.system(" in src and "GTRCATI" in src:
            shape = "v3.16/v3.35 (os.system)"
            lines = _patch_os_system(lines)
        else:
            print(
                f"bootstrapfix: unrecognised vsnp3_fasta_to_snps_table.py shape "
                f"({version}) at {path} — no RAxML invocation matched. Not patching.",
                file=sys.stderr,
            )
            return 1
    except LookupError as exc:
        print(f"bootstrapfix: {exc} in {path}", file=sys.stderr)
        return 1

    out = "\n".join(lines) + ("\n" if src.endswith("\n") else "")
    compile(out, str(path), "exec")   # never leave a syntactically broken module

    backup = path.with_suffix(path.suffix + ".pre-bootstrapfix")
    if not backup.exists():
        shutil.copy2(path, backup)
    out = re.sub(
        r'^(__version__\s*=\s*["\'])([^"\']+)(["\'])',
        lambda mm: f"{mm.group(1)}{mm.group(2)}+kl.bootstrap1{mm.group(3)}",
        out,
        count=1,
        flags=re.M,
    )

    # tmp + rename, never an in-place write: conda hardlinks env files to the
    # package cache, so writing in place patches the CACHE and every future env
    # unpacked from it inherits the change with no record of why.
    links = path.stat().st_nlink
    tmp = path.with_suffix(path.suffix + ".bootstrapfix-tmp")
    tmp.write_text(out, encoding="utf-8")
    shutil.copystat(path, tmp)
    tmp.replace(path)
    note = f", broke {links - 1} hardlink(s) to the conda cache" if links > 1 else ""
    print(
        f"bootstrapfix: applied {shape} to {path} "
        f"(was {version}, backup at {backup.name}{note})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
