"""The PDF renderer the FMDV reports share, with its fonts found first.

weasyprint draws text through pango and fontconfig, and fontconfig reads one
configuration file to learn where the fonts are. In a conda env that file is
<env>/etc/fonts/fonts.conf, and the library is built to look there. When it
looks somewhere else (a system libfontconfig picked up ahead of the env's, on
a node with no /etc/fonts), every report prints

    Fontconfig error: Cannot load default config file: File not found

three times and falls back on whatever font it can find. On the Ames HPC
(2026-11-20) a Step 1 run of 79 samples put some 240 of those lines in the
log, each one reading as a failure of the sample above it, while every report
was being written.

So the env's own file is named outright before the renderer is loaded, which
is what fontconfig honours over its built-in default. A choice already made
in the environment is left alone. When the env has no such file there is
nothing to name, and that is said once, in a sentence, with what it means.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SAID = False


def font_config() -> str:
    """Point fontconfig at this env's configuration. Returns the file used,
    or "" when none was found (and says so once on stderr)."""
    global _SAID
    already = os.environ.get("FONTCONFIG_FILE", "").strip()
    if already:
        return already
    conf = Path(sys.prefix) / "etc" / "fonts" / "fonts.conf"
    if conf.is_file() and os.access(conf, os.R_OK):
        os.environ["FONTCONFIG_FILE"] = str(conf)
        os.environ.setdefault("FONTCONFIG_PATH", str(conf.parent))
        return str(conf)
    if not _SAID:
        _SAID = True
        why = "cannot be read by this account" if conf.exists() else "is not there"
        print(f"reports: the font configuration {conf} {why}, so PDFs use a fallback font and the "
              "'Fontconfig error' lines that follow are that and nothing more. The reports themselves, "
              "and the HTML copies, are complete.", file=sys.stderr, flush=True)
    return ""


def weasy_html():
    """weasyprint's HTML class, or None when weasyprint is not installed."""
    font_config()
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        return None
    return HTML
