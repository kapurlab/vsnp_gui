"""The preview page's IGV launcher, as SERVED.

Two classes of bug live here, and neither is visible in the source text.

The first is escaping. The page is built from a Python f-string, so every brace
in its JavaScript is doubled. Get one wrong and the served page carries `{{`
verbatim, or the format call raises, or — worst — the script is syntactically
broken and the page renders with every interaction silently dead. So these
assertions run against `compose_page` output, never against xlsx_html.py's
source.

The second is silent failure. "I click a SNP and no tab opens" took two rounds
to pin down because all three ways the launch could fail said nothing:

  * postMessage into a tab that is open but no longer running this app —
    `window.closed` is false for it, which is routine under Open OnDemand where
    the URL carries a compute node and port that change when the session is
    recycled. The message vanished and the click did nothing.
  * `window.open` returning null under a pop-up blocker, never checked.
  * the opened tab never being focused, so it could appear in the background.

The launcher now requires the viewer to acknowledge a reused tab, reports a
blocked pop-up, and focuses what it opens. These assertions pin that contract.
Behaviour (ack arrives -> reuse; ack does not -> open fresh; open returns null
-> message) was verified by running the extracted script under node; what is
kept here is the contract that lets it work.

Run directly:  python test_igv_launcher_page.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_xlsx_filter import assert_eq, assert_true, make_group_table

import xlsx_html


def render_page(tmp: Path) -> str:
    """A streamed (>20,000 cell) preview, which is the path that uses the
    delegated click handler and therefore the launcher."""
    book = tmp / "big.xlsx"
    labels = [f"S{i}" for i in range(1, 41)]
    make_group_table(book, labels, 600, lambda i, c: "A" if c % 3 else "C",
                     locus_at=lambda c: f"A/owl/ICA/25-003495-001/2024_PB2:{c}")
    total_rows, total_cols = len(labels) + 4, 600
    assert total_rows * total_cols > xlsx_html.STREAM_ABOVE_CELLS
    window = xlsx_html.render_window(
        book, total_rows, total_cols, None, "proj",
        samples_with_bams=set(labels), samples_with_vcfs=set(),
        max_cells=1_000_000, max_rows=1_000, max_table_bytes=64 * 1024 * 1024)
    return xlsx_html.compose_page(window)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="igv_launcher_"))
    try:
        page = render_page(tmp)

        print("[the f-string survived]")
        assert_eq("{{" in page, False, "no doubled opening braces in the served page")
        assert_eq("}}" in page, False, "no doubled closing braces in the served page")
        assert_true("<script>" in page, "the page carries a script block")

        print("\n[the launcher exists and is reachable from a cell]")
        assert_true("window.__vsnpLaunchIgv" in page, "launcher defined")
        assert_true('id="xlsxIgvNote"' in page, "a place to report a failed launch")
        assert_true("xlsx-igv-note" in page, "the note is styled")

        print("\n[a reused tab has to prove it is alive]")
        assert_true("vsnpIgvAck" in page, "the ack protocol is present")
        assert_true("__vsnpIgvAck" in page, "the ack is recorded")
        # The fallback is what makes a stale handle recoverable rather than a
        # dead end: no ack, so discard the handle and open a fresh tab.
        assert_true("window.__vsnpIgvWin = null" in page,
                    "a handle that does not ack is discarded")
        assert_true(re.search(r"setTimeout\(function\(\)\s*\{", page) is not None,
                    "the ack is given a deadline")

        print("\n[a blocked pop-up is reported, and a new tab is focused]")
        assert_true("blocked the IGV window" in page, "a blocked pop-up says so")
        assert_true("Allow pop-ups" in page, "and says what to do about it")
        assert_true("win.focus()" in page, "a newly opened tab is focused")
        # The named target is deliberate — it is what makes clicks additive —
        # but it is also what can hand back a stale tab, hence the ack above.
        assert_true('window.open(url, "vsnp_igv")' in page, "named target kept")

        print("\n[the origin check on incoming messages is not relaxed]")
        assert_true("ev.origin !== window.location.origin" in page,
                    "messages from other origins are ignored")

        print("\n[the served script parses]")
        node = shutil.which("node")
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        assert_true(len(scripts) >= 1, f"{len(scripts)} script block(s) extracted")
        if node:
            js = tmp / "served.js"
            js.write_text("\n;\n".join(scripts))
            proc = subprocess.run([node, "--check", str(js)],
                                  capture_output=True, text=True)
            assert_eq(proc.returncode, 0,
                      f"node --check on the served script{'' if not proc.stderr else ': ' + proc.stderr[:300]}")
        else:
            print("  SKIP  node not on PATH — cannot parse-check the served script")

        print("\nALL PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
