#!/bin/bash
# kapurlab-setup-project.sh — provision a per-project group + dir + XFS quota
#
# Usage:
#   sudo kapurlab-setup-project.sh <project-name> [user1] [user2] ...
#
# Creates:
#   - Unix group  proj-<name>
#   - Directory   /srv/kapurlab/projects/<name>/  (setgid, 2770, group=proj-<name>)
#   - Standard subdirs: download/ step1/ step2/vcf_source/ audit/
#   - Append-only audit ledger at audit/edits.jsonl (chattr +a)
#   - XFS project quota on /srv/kapurlab (5 TB soft / 7 TB hard)
#
# Idempotent: safe to re-run for an existing project (e.g. to add users).

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (or under sudo)" >&2
  exit 1
fi

if [ $# -lt 1 ]; then
  echo "usage: $0 <project-name> [user1] [user2] ..." >&2
  exit 1
fi

NAME="$1"; shift
USERS=("$@")

if ! [[ "$NAME" =~ ^[a-z][a-z0-9_-]{1,30}$ ]]; then
  echo "invalid name: must be lowercase alnum/_/-, start with a letter, 2-31 chars" >&2
  exit 1
fi

GROUP="proj-${NAME}"
ROOT="/srv/kapurlab/projects"
DIR="${ROOT}/${NAME}"
PROJID_FILE=/etc/projid
PROJECTS_FILE=/etc/projects
PROJID_RANGE_LO=1000
PROJID_RANGE_HI=1999

# 1. group
if getent group "$GROUP" >/dev/null; then
  echo "[ok] group ${GROUP} exists"
else
  groupadd "$GROUP"
  echo "[new] group ${GROUP} created"
fi

# 2. users
for u in "${USERS[@]}"; do
  if ! id "$u" >/dev/null 2>&1; then
    echo "[warn] no such user: ${u}" >&2
    continue
  fi
  if id -nG "$u" | tr ' ' '\n' | grep -qx "$GROUP"; then
    echo "[ok] ${u} already in ${GROUP}"
  else
    usermod -aG "$GROUP" "$u"
    echo "[add] ${u} -> ${GROUP}"
  fi
done

# 3. directory tree
mkdir -p "${DIR}"/{download,step1,step2/vcf_source,audit}
chown -R "root:${GROUP}" "${DIR}"
# Top dir + every subdir setgid 2770. Files inherit the group.
find "${DIR}" -type d -exec chmod 2770 {} +

# Project metadata (consumed by vsnp_gui's list_projects).
META="${DIR}/project.json"
if [ ! -f "${META}" ]; then
  cat > "${META}" <<EOF
{
  "name": "${NAME}",
  "scope": "shared",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "created"
}
EOF
  chown "root:${GROUP}" "${META}"
  chmod 0660 "${META}"
fi

# Audit ledger: ensure exists, then make append-only.
LEDGER="${DIR}/audit/edits.jsonl"
[ -f "${LEDGER}" ] || { touch "${LEDGER}"; chown "root:${GROUP}" "${LEDGER}"; chmod 0660 "${LEDGER}"; }
# +a may fail on non-ext/xfs, on bind mounts, or in containers. Tolerate.
if ! lsattr "${LEDGER}" 2>/dev/null | grep -q ' \+a'; then
  chattr +a "${LEDGER}" 2>/dev/null && echo "[set] +a on ${LEDGER}" || echo "[warn] +a not supported here; ledger writeable but not append-only"
fi

# 4. XFS project quota
touch "${PROJID_FILE}" "${PROJECTS_FILE}"
EXISTING_ID="$(awk -F: -v g="${GROUP}" '$1==g{print $2}' "${PROJID_FILE}" 2>/dev/null || true)"
if [ -n "${EXISTING_ID}" ]; then
  PROJID="${EXISTING_ID}"
  echo "[ok] project id ${PROJID} (existing) for ${GROUP}"
else
  # Allocate the next free ID in our reserved range.
  USED_IDS="$(awk -F: '/^[a-z]/{print $2}' "${PROJID_FILE}" | tr '\n' ' ')"
  PROJID=""
  for cand in $(seq "${PROJID_RANGE_LO}" "${PROJID_RANGE_HI}"); do
    if ! echo " ${USED_IDS} " | grep -q " ${cand} "; then
      PROJID="${cand}"; break
    fi
  done
  if [ -z "${PROJID}" ]; then
    echo "[err] no free project id in ${PROJID_RANGE_LO}-${PROJID_RANGE_HI}" >&2
    exit 1
  fi
  echo "${GROUP}:${PROJID}" >> "${PROJID_FILE}"
  echo "${PROJID}:${DIR}" >> "${PROJECTS_FILE}"
  echo "[new] project id ${PROJID} for ${GROUP}"
fi

# Activate quota tracking on the directory tree.
xfs_quota -x -c "project -s -p ${DIR} ${PROJID}" /srv/kapurlab >/dev/null
xfs_quota -x -c "limit -p bsoft=5t bhard=7t ${GROUP}" /srv/kapurlab
echo "[set] xfs project quota: bsoft=5t bhard=7t on ${DIR}"

echo
echo "Done."
echo "  Project root: ${DIR}"
echo "  Group:        ${GROUP}"
echo "  Quota:        5T soft / 7T hard"
echo
echo "User memberships are session-scoped — added users must log out and back in"
echo "for their new group membership to take effect."
