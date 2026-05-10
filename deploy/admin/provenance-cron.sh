#!/bin/bash
# T-07 provenance-index cron helper.
#
# Wraps the three janitor / archive operations the indexer exposes so that
# /etc/cron.d/vsnp_gui-provenance can stay declarative. Walks every project
# root that exists on this host (the shared /srv root plus any per-user
# /home/*/projects).
#
# Subcommands:
#   gc      mark stuck `running` records as `unknown_terminated` after 48h
#           and rewrite the on-disk run_metadata.json sentinels.
#   crawl   index every run_metadata.json (no-op if already indexed by the
#           writer's inline finalize_callback; this is the fallback path).
#   export  dump the runs table to JSONL for archive grep-ability.
#
# Run as `vxk1` (the indexer-owning user). Cron lines should redirect output
# to /dev/null; this script intentionally surfaces errors via non-zero exit
# so an interactive run shows what went wrong.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 {gc|crawl|export}" >&2
    exit 2
fi

ACTION="$1"
PY=/srv/kapurlab/tools/vsnp3/bin/python
DB=/srv/kapurlab/audit/runs.sqlite
JSONL=/srv/kapurlab/audit/runs.sqlite.jsonl
export PYTHONPATH=/srv/kapurlab/tools/vsnp_gui/backend

ROOTS=()
[[ -d /srv/kapurlab/projects ]] && ROOTS+=( /srv/kapurlab/projects )
for d in /home/*/projects; do
    [[ -d "$d" ]] && ROOTS+=( "$d" )
done

run_indexer() {
    "$PY" -m app.vsnp_provenance.index --db "$DB" "$@"
}

case "$ACTION" in
    gc)
        for r in "${ROOTS[@]}"; do
            run_indexer gc --max-hours 48 --projects-root "$r" >/dev/null
        done
        ;;
    crawl)
        for r in "${ROOTS[@]}"; do
            run_indexer crawl "$r" >/dev/null
        done
        ;;
    export)
        run_indexer export --out "$JSONL" >/dev/null
        ;;
    *)
        echo "unknown action: $ACTION (expected gc|crawl|export)" >&2
        exit 2
        ;;
esac
