#!/bin/bash
# Atomically rename a project directory AND update the provenance indexer's
# path references. Without the indexer step a plain `mv` would silently
# corrupt every runs.sqlite row that points at the old path.
#
# Usage:
#   kapurlab-rename-project <old> <new>
#
# <old> and <new> may be project names (resolved against PROJECTS_ROOT,
# default /srv/kapurlab/projects) or absolute paths. Both must live under
# the same parent.
#
# Example:
#   kapurlab-rename-project sanity_test deer-pilot-2026
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $(basename "$0") <old> <new>" >&2
    exit 2
fi

PROJECTS_ROOT="${PROJECTS_ROOT:-/srv/kapurlab/projects}"
PY=/srv/kapurlab/tools/vsnp3/bin/python
DB=/srv/kapurlab/audit/runs.sqlite
export PYTHONPATH=/srv/kapurlab/tools/vsnp_gui/backend

resolve() {
    case "$1" in
        /*) echo "$1" ;;
        *)  echo "$PROJECTS_ROOT/$1" ;;
    esac
}

OLD_PATH="$(resolve "$1")"
NEW_PATH="$(resolve "$2")"

if [[ ! -d "$OLD_PATH" ]]; then
    echo "error: source not found: $OLD_PATH" >&2
    exit 1
fi
if [[ -e "$NEW_PATH" ]]; then
    echo "error: destination already exists: $NEW_PATH" >&2
    exit 1
fi
if [[ "$(dirname "$OLD_PATH")" != "$(dirname "$NEW_PATH")" ]]; then
    echo "error: cross-parent rename is not supported (old parent=$(dirname "$OLD_PATH"), new parent=$(dirname "$NEW_PATH"))" >&2
    exit 1
fi

mv -- "$OLD_PATH" "$NEW_PATH"
"$PY" -m app.vsnp_provenance.index --db "$DB" rename \
    --old "$OLD_PATH" --new "$NEW_PATH" --by "$(whoami)"
echo "renamed: $OLD_PATH -> $NEW_PATH"
