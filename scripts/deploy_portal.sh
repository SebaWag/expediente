#!/usr/bin/env bash
# Deploy the investigation portal next to the graph.
#
# Two networks, and the split is the security model again:
#   traefik-public  — so Traefik terminates TLS for investiga.<domain>
#   expediente-net  — so the portal reaches MillenniumDB by container name without
#                     the SPARQL endpoint ever being published
#
# The portal WRITES (runs, event logs, dossiers) and the API does not, which is why
# they are separate containers and separate images. The write path is a mounted
# directory: --read-only on the root filesystem, plus one writable volume, so a bug
# in the agent cannot rewrite the image.
#
# Usage (on the host):
#   bash scripts/deploy_portal.sh
#
# Requires /opt/expediente/portal.env with the model credentials (see .env.example).
set -euo pipefail

HOST_DOMAIN="${HOST_DOMAIN:-investiga.wagnersolutionsai.com}"
IMAGE="expediente-portal:latest"
SRC="${SRC:-/opt/expediente/portal}"
RUNS="${RUNS:-/opt/expediente/runs}"
MDB_URL="${MDB_URL:-http://expediente-mdb:1234}"
ENV_FILE="${ENV_FILE:-/opt/expediente/portal.env}"
CONTAINER="expediente-portal"

if [ ! -f "${ENV_FILE}" ]; then
  echo "missing ${ENV_FILE} — it needs META_MODEL_API_KEY, META_MODEL_BASE_URL, META_MODEL_CHAT_MODEL" >&2
  exit 1
fi

echo "building ${IMAGE} from ${SRC}"
docker build -q -t "${IMAGE}" "${SRC}" >/dev/null

mkdir -p "${RUNS}"
mkdir -p "${EVIDENCE:-/opt/expediente/evidencia}"
# The container runs as uid 10001. The directory has to be writable by that uid and
# by nobody else: the runs are the record, and the record is not shared.
chown -R 10001:10001 "${RUNS}"
chmod 750 "${RUNS}"

docker network create expediente-net >/dev/null 2>&1 || true
docker network connect expediente-net expediente-mdb 2>/dev/null || true

docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER}" --restart unless-stopped \
  --network traefik-public \
  --env-file "${ENV_FILE}" \
  -e "EXPEDIENTE_MDB_URL=${MDB_URL}" \
  -e "EXPEDIENTE_RUNS_DIR=/data/runs" \
  -v "${RUNS}:/data/runs" \
  -v "${EVIDENCE:-/opt/expediente/evidencia}:/data/evidencia:ro" \
  --read-only --tmpfs /tmp \
  --label traefik.enable=true \
  --label "traefik.http.routers.investiga.rule=Host(\`${HOST_DOMAIN}\`)" \
  --label traefik.http.routers.investiga.entrypoints=websecure \
  --label traefik.http.routers.investiga.tls=true \
  --label traefik.http.routers.investiga.tls.certresolver=letsencrypt \
  --label traefik.http.services.investiga.loadbalancer.server.port=8080 \
  --label "traefik.http.routers.investiga-http.rule=Host(\`${HOST_DOMAIN}\`)" \
  --label traefik.http.routers.investiga-http.entrypoints=web \
  --label traefik.http.routers.investiga-http.middlewares=redirect-https@file \
  "${IMAGE}" \
  python3 -m expediente.portal.server --port 8080 >/dev/null

# The command is explicit because the image's default CMD is the read-only API.
# The first deploy omitted it and served the graph API on the portal hostname: the
# root path answered 200 with 190 bytes of JSON, which is exactly the kind of thing
# that looks like a working deploy if nobody reads the response.

docker network connect expediente-net "${CONTAINER}" 2>/dev/null || true

echo "waiting for https://${HOST_DOMAIN}/api/health"
for _ in $(seq 1 40); do
  if curl -sf -m 5 "https://${HOST_DOMAIN}/api/health" >/dev/null 2>&1; then
    echo "up: https://${HOST_DOMAIN}"
    exit 0
  fi
  sleep 3
done

echo "the container is running but the URL does not answer yet." >&2
echo "check: docker logs ${CONTAINER} --tail 30 ; docker logs traefik --tail 30" >&2
exit 1
