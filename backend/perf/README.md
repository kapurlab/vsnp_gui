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
