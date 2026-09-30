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
