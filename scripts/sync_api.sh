#!/usr/bin/env bash
# Copy the repository to the API host directory, then the image can be rebuilt.
#
# Why this file exists
# --------------------
# `sync_portal.sh` copies to `/opt/expediente/portal`, which is what the *portal* builds from.
# The read-only API builds from `/opt/expediente/src`, and that copy was being made by hand.
# The consequence was measured on 4-nov-2026: the API container had **no**
# `expediente/data/aliases.json`, so `/api/search` there never expanded an acronym — CENABAST
# worked on the portal and did not work on the API, and nobody noticed because the demo uses the
# portal. A deployment that drops a data file does not fail: it degrades in silence.
#
# Usage:  bash scripts/sync_api.sh [host]
set -euo pipefail

HOST="${1:-hetzner-ws}"
DEST="${DEST:-/opt/expediente/src/}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -az --delete \
  --exclude '/.git' \
  --exclude '/out' \
  --exclude '/data' \
  --exclude '/benchmarks' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '*.npy' \
  --exclude '/.env' \
  "${HERE}/" "${HOST}:${DEST}"

echo "synced ${HERE} -> ${HOST}:${DEST}"

#: Los dos archivos cuya ausencia no rompe el arranque: degrada una función en silencio.
for required in expediente/data/aliases.json expediente/api/server.py; do
  if ! ssh "${HOST}" "test -f ${DEST}${required}"; then
    echo "missing on the host after sync: ${required}" >&2
    exit 1
  fi
done
echo "checked: alias table and API source are present"
