#!/usr/bin/env bash
# Isolated MillenniumDB instance for Expediente.
#
# Deliberately not shared with any other graph: its own container, its own database
# folder, bound to loopback. See docs/GRAPH_BACKEND.md.
set -euo pipefail

NAME="expediente-mdb"
IMAGE="imfd/millenniumdb:latest"
ROOT="${HOME}/expediente-mdb"
PORT="${EXPEDIENTE_MDB_PORT:-1234}"
HOST_BIND="127.0.0.1"

usage() {
  cat <<'EOF'
usage: mdb_isolated.sh <start|stop|status|reset|logs>

  start   create the database if missing and run the server on 127.0.0.1:1234
  stop    stop and remove the container (the database folder is kept)
  status  show container state and triple-addressability
  reset   delete the database folder and re-import the empty seed
  logs    tail the server log
EOF
}

require_docker() {
  command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
}

start() {
  require_docker
  mkdir -p "${ROOT}"
  [ -f "${ROOT}/seed.ttl" ] || printf '@prefix exp: <http://expediente.local/onto#> .\n' > "${ROOT}/seed.ttl"

  # The volume is ${ROOT}:/data, so the database the container sees at /data/db is
  # ${ROOT}/db on the host. Checking ${ROOT}/data/db made "reset" a no-op and the
  # next start failed with "folder already exists and it's not empty".
  if [ ! -d "${ROOT}/db" ]; then
    echo "importing seed into ${ROOT}/db"
    docker run --rm --entrypoint bash -v "${ROOT}:/data" "${IMAGE}" \
      -c "mdb import /data/seed.ttl /data/db"
  fi

  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  docker run -d --name "${NAME}" -p "${HOST_BIND}:${PORT}:1234" \
    -v "${ROOT}:/data" --entrypoint bash "${IMAGE}" \
    -c "mdb server /data/db -p 1234 --browser false" >/dev/null

  echo "waiting for the server..."
  for _ in $(seq 1 20); do
    if curl -sf -m 2 -X POST "http://${HOST_BIND}:${PORT}/sparql" \
        --data-urlencode 'query=SELECT ?s WHERE { ?s ?p ?o } LIMIT 1' >/dev/null; then
      echo "up: http://${HOST_BIND}:${PORT}"
      echo "export EXPEDIENTE_MDB_URL=http://${HOST_BIND}:${PORT}"
      return 0
    fi
    sleep 1
  done
  echo "server did not answer; check '$(basename "$0") logs'" >&2
  return 1
}

stop() {
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  echo "stopped"
}

status() {
  docker ps -a --filter "name=${NAME}" --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
  if curl -sf -m 3 -X POST "http://${HOST_BIND}:${PORT}/sparql" \
      --data-urlencode 'query=SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }' 2>/dev/null; then
    echo
  else
    echo "not answering on http://${HOST_BIND}:${PORT}"
  fi
}

reset() {
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  # The engine writes its files as the container user, so removing them from the host
  # fails with EACCES. Deleting them from inside the image keeps the reset working
  # without sudo and without widening the permissions of the folder.
  docker run --rm --entrypoint bash -v "${ROOT}:/data" "${IMAGE}" \
    -c "rm -rf /data/db /data/data/db" >/dev/null 2>&1 || true
  rm -rf "${ROOT}/db" "${ROOT}/data/db" 2>/dev/null || true
  echo "database removed; run 'start' to recreate it"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  reset) reset ;;
  logs) docker logs "${NAME}" ;;
  *) usage; exit 1 ;;
esac
