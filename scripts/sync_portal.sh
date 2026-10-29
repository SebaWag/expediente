#!/usr/bin/env bash
# Copy the repository to the portal host.
#
# The exclusions are anchored with a leading slash on purpose. The first version of
# this sync used `--exclude 'data'`, which matches EVERY directory named data at any
# depth — including `expediente/data/`, where `aliases.json` lives. The file never
# reached the container, `/api/search` raised FileNotFoundError, and the portal
# reported it as "run no encontrado". A deploy that drops a data file fails one
# endpoint, at request time, with the wrong error message.
#
# Usage:  bash scripts/sync_portal.sh [host]
set -euo pipefail

HOST="${1:-hetzner-ws}"
DEST="${DEST:-/opt/expediente/portal/}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -az --delete \
  --exclude '/.git' \
  --exclude '/out' \
  --exclude '/data' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '*.npy' \
  --exclude '/.env' \
  "${HERE}/" "${HOST}:${DEST}"

echo "synced ${HERE} -> ${HOST}:${DEST}"

# The two files whose absence is silent until a request fails.
for required in expediente/data/aliases.json expediente/portal/static/index.html; do
  if ! ssh "${HOST}" "test -f ${DEST}${required}"; then
    echo "missing on the host after sync: ${required}" >&2
    exit 1
  fi
done
echo "checked: aliases table and portal assets are present"
