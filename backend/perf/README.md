# Shared-storage performance harness

On the HPC every filesystem call the backend makes is a round trip to a
storage server, and a project switch on an 8,000-sample project is made of
tens of thousands of them. Nothing on a laptop shows that cost, so these
scripts model it: every `stat`, `scandir`, `listdir` and `open` under a
chosen directory costs a fixed latency (`fslat/sitecustomize.py`, on
`PYTHONPATH`, so the backend and its subprocesses are charged alike), and a
synthetic project laid out exactly like real Step 1 output stands in for
the real one.

    PY=<checkout>/env/bin/python          # the app's own env
    $PY make_fixture.py /tmp/fx --owl 8171 --small 500
    $PY switch_bench.py --fx /tmp/fx --ms 1 --checkout <tree> --label before
    $PY switch_bench.py --fx /tmp/fx --ms 1 --checkout <tree> --label after

`switch_bench.py` starts the real backend from `<tree>` (which needs an
`env/` — a symlink to the checkout's is fine), replays the requests
`App.jsx` fires on a project click in the same order and concurrency, and
reports each request's wall time, the settle time, and the number of
modelled calls — for the page load, a cold switch, a switch away and a warm
revisit. Run it twice: the first pass builds the on-disk caches, the second
is what a new Open OnDemand session costs.

`igv_bench.py` replays what the browser fires when a cascade-table cell is
clicked — `/step1/files`, then the igv.js sequence through `/serve` (FASTA
index, a sequence slice, the calls VCF, the BAM index, the BAM header, a
block of reads, the annotation) — and a table preview cold, warm and as a
scroll batch, reporting each request's wall time and modelled calls;
`--concurrent N` also times one `/serve` while N previews are in flight.

    $PY igv_bench.py --fx /tmp/fx --ms 1 --label before --concurrent 6

Numbers on the 8,171-sample owl at 1 ms per call, before and after the
preview stopped walking every Step 1 folder (v0.4.115 -> next):

                                  v0.4.115              after
    table preview, cold           46.3 s   32,740     1.45 s     654 calls
    table preview, cached         45.9 s   32,725     0.08 s      38
    table preview, scroll batch   45.3 s   32,731     0.08 s      38
    /serve, one igv.js request    0.04 s       25     0.02 s       9
    IGV launch, 8 requests        0.60 s      392     0.49 s     309

The preview built "which samples have a BAM" by looking inside all 8,171
folders on every request — three times that on the 24,000-sample project,
minutes on shared storage, cache hits and 200-row scroll batches included —
which is what a table tab held a browser connection open with while IGV's
requests waited in line. It now takes its candidates from the shared Step 1
listing and looks only at the samples the table's rows name (`LazyStems` in
xlsx_html.py); the cache key no longer carries the sample sets, and a cached
window instead records the rows it drew without reads and asks about just
those. `preview_equiv`-style checks (the same tables rendered by both trees)
came out byte-identical for the full page, a scroll batch, the cached page,
the small-sheet renderer and a clade selection. What remains of the IGV
launch is `/step1/files`, most of it the GFF lookup over every reference dir.

`igv_probe.mjs` opens the IGV page in headless Chrome (Node 22+, no
packages) against a running backend and prints every request the page makes
with its byte range, status, size and duration, the page's own status line
and transfer readout as they change, any igv.js alert, and a screenshot:

    node igv_probe.mjs "http://127.0.0.1:8771/?view=igv&tracks=owl:S&locus=seg8:577" out.png 40 dark 1000

The optional fourth and fifth arguments emulate a colour scheme and a link
speed in kbit/s, which is how the dark appearance and the transfer readout
were checked. A real BAM for a fixture sample (reads simulated from the
fixture reference, `samtools sort` + `index`) stands in for igv.js's data.

Reads are served by window when the backend has samtools (`reads_ticket` /
`reads_data` in main.py, igv.js's htsget source): the transfer is the depth at
the window, not the contig's block of reads. That path is a samtools process
plus two requests per view and is not modelled here; `igv_bench.py` replays
the byte-range path, which remains the fallback.

`resize_probe.mjs` opens a project (which expands its samples in the
Projects panel), resizes the window through the given steps and back, and
reports whether the page survived, over several runs; it exits 1 if any run
blanked:

    node resize_probe.mjs http://127.0.0.1:8771 owl 1000x900 6

v0.4.114 to v0.4.118 could blank the whole app this way: the Projects
panel's VirtualRows read the view after every render, and while a resize's
own update waited, each read asked React for a new band, until React gave up
("Maximum update depth exceeded"). This probe blanked v0.4.118 in 8 of 8
runs on both the owl fixture and mtbc0_test01, and the fix in 0 of 30 over
five resize patterns. Never screenshot with `captureBeyondViewport`: it
resizes the page too.

`endpoint_calls.py` counts calls per endpoint in-process (cold and warm);
`dump_endpoints.py` writes every endpoint's JSON so two trees can be diffed
on one fixture (`awkward_extras.py` makes the fixture awkward first:
legacy layouts, links, hidden and scaffold dirs, unfinished samples, edits,
a staged-only run, a post-hoc group). See `test_fs_round_trips.py` for the
per-sample call budgets that keep a later change from bringing the walks
back.

Numbers from the v0.4.107 release (1 ms per call, 8,171-sample owl, 37
groups; "new session" is a fresh backend with the on-disk caches in place,
"first ever" a project no backend has seen):

                                v0.4.106            v0.4.107
    page load (/api/projects)   0.8 s    8,714       0.9 s    8,720 calls
    new session, cold switch    4.2 s   58,857       2.7 s   16,819
    warm revisit                3.6 s   34,273       2.1 s   16,750
    first ever, cold switch    12.4 s  192,888      10.2 s   85,482

Numbers from the v0.4.125 release (1 ms per call, 8,171-sample owl, caches on
disk; the "before" column is v0.4.124, whose fan-out pool was 16 wide):

                                v0.4.124            v0.4.125
    page load (/api/projects)   0.96 s    8,720      0.30 s    8,720 calls
    warm revisit                2.42 s   16,750      0.79 s   16,750

The call counts are unchanged: what moved is how many are in flight at once
(fanout.py's pool is 64 wide now; `VSNP_GUI_FS_WORKERS` still overrides it)
and the Python a switch spends holding the GIL — the per-sample lists skip
FastAPI's jsonable_encoder (`_plain_json` in main.py; 430 ms of a switch on
this fixture, 300 ms of it the 13 MB Results answer). At 0 ms per call the
same switch is pure CPU: cold 1.97 s -> 1.70 s, warm revisit 1.01 s -> 0.58 s.

Two costs the model does not cover were measured on a `cp -cR` clone of
mtbc0_test01 (68 samples). A cold open of its 72 x 10,001 cascade table
through /preview-xlsx: 5.2 s -> 2.7 s (render_window alone 4.7 s -> 2.2 s).
Per-cell style and conditional-format work is memoised (`_CfMemo` and
`_CellStyleMemo` in xlsx_html.py), and the pages are byte-identical over all
120 Step 2 tables on this machine — full page, clade filter and the clade's
xlsx export. A 9.5 MB samtools reads window through reads_data, as igv.js
fetches it: 291 ms -> 147 ms, because the gzip exclusions in request_safety.py
now reach the middleware and BAM is no longer compressed a second time.

What remained of a table open was the browser's: the cached 72 x 10,001 page
was 0.7 MB on the wire but 720,000 `<td>`s, about 20 s of parsing and layout
in Chrome against 0.5 s for a 973-column table, because rows were paged and
columns were not.

v0.4.126 pages the columns too. The page inlines the first rows and a window
of leading columns (`initial_cols_for` in xlsx_html.py: about 40,000 cells,
400 columns of a 72-row table) and asks for the rest as it is scrolled —
`rows_from`/`rows_count`/`cols_count` for a block of rows as wide as the page,
`cols_from`/`cols_count`/`rows_count` for a block of columns as tall as it —
one request at a time so the table stays square however the two interleave.
The cached window is unchanged (rows stay whole `<tr>` strings on disk) and is
split into cells once per process when a column is first asked for. Measured
in headless Chrome on the same cached 72 x 10,001 table: load event at 0.23 s
with 28,800 cells (was 20 s with 720,000), the page 1.3 MB instead of 25.7 MB,
each scroll to the right edge adding 400 columns in one request, and a click
on a variant cell in column 2,800 opening the right sample at the right locus.
