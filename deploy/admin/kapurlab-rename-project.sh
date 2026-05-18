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

# Rewrite intra-project absolute symlinks. step1_setup creates fastq
# symlinks with an *absolute* target (Path.symlink_to(absolute_path)), so
# after the directory rename every symlink under step1/ that pointed at
# files in the old download/ dir is now broken. vsnp3 itself works fine
# (it doesn't follow these), but the T-07 provenance dispatch tries to
# stat() each input via the symlink → FileNotFoundError → the whole
# Step 1 run aborts with "Provenance dispatch failed". Fix in place:
# rewrite every absolute symlink whose target starts with $OLD_PATH/ to
# instead start with $NEW_PATH/.
SYMLINKS_FIXED=0
while IFS= read -r link; do
    target="$(readlink "$link")"
    case "$target" in
        "$OLD_PATH"/*)
            new_target="$NEW_PATH${target#$OLD_PATH}"
            ln -snf "$new_target" "$link"
            SYMLINKS_FIXED=$((SYMLINKS_FIXED+1))
            ;;
    esac
done < <(find "$NEW_PATH" -type l 2>/dev/null)
if [[ "$SYMLINKS_FIXED" -gt 0 ]]; then
    echo "rewrote $SYMLINKS_FIXED intra-project symlink(s) to new path"
fi

# Rewrite project.json so name + display_name match the new directory.
# Without this the GUI keeps showing the old name in the project list,
# because the frontend renders `p.display_name || p.name` and both
# fields are written from the original creation-time name. Skip
# silently if project.json is absent (legacy projects from before
# we started writing metadata).
OLD_NAME="$(basename "$OLD_PATH")"
NEW_NAME="$(basename "$NEW_PATH")"
META="$NEW_PATH/project.json"
if [[ -f "$META" ]]; then
    "$PY" - <<PYEOF
import json
from pathlib import Path
p = Path("$META")
m = json.loads(p.read_text())
m["name"] = "$NEW_NAME"
# If display_name was the auto-generated "<old>_<reference>" form, swap
# the old project name for the new one. Leave it alone if the user has
# customised it to something that doesn't start with the old name.
dn = m.get("display_name")
if isinstance(dn, str) and dn.startswith("$OLD_NAME"):
    m["display_name"] = "$NEW_NAME" + dn[len("$OLD_NAME"):]
p.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
PYEOF
    echo "updated metadata: $META (name + display_name)"
fi

echo "renamed: $OLD_PATH -> $NEW_PATH"
