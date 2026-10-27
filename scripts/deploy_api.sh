#!/usr/bin/env bash
# Deploy the read-only API next to the graph on the production host.
#
# Two networks on purpose:
#   traefik-public  — so Traefik can route to it and terminate TLS
#   expediente-net  — so it can reach MillenniumDB by name without publishing the
#                     SPARQL endpoint anywhere. The engine speaks update; the API does
#                     not, and that asymmetry is the whole security model.
#
# Usage (on the host):  bash scripts/deploy_api.sh
set -euo pipefail

HOST_DOMAIN="${HOST_DOMAIN:-expediente.wagnersolutionsai.com}"
IMAGE="expediente-api:latest"
SRC="${SRC:-/opt/expediente/src}"
MDB_URL="${MDB_URL:-http://expediente-mdb:1234}"

echo "building ${IMAGE} from ${SRC}"
docker build -q -t "${IMAGE}" "${SRC}" >/dev/null

docker network create expediente-net >/dev/null 2>&1 || true
docker network connect expediente-net expediente-mdb 2>/dev/null || true

docker rm -f expediente-api >/dev/null 2>&1 || true
docker run -d --name expediente-api --restart unless-stopped \
  --network traefik-public \
  -e "EXPEDIENTE_MDB_URL=${MDB_URL}" \
  --label traefik.enable=true \
  --label "traefik.http.routers.expediente.rule=Host(\`${HOST_DOMAIN}\`)" \
  --label traefik.http.routers.expediente.entrypoints=websecure \
  --label traefik.http.routers.expediente.tls=true \
  --label traefik.http.routers.expediente.tls.certresolver=letsencrypt \
  --label traefik.http.services.expediente.loadbalancer.server.port=8080 \
  --label "traefik.http.routers.expediente-http.rule=Host(\`${HOST_DOMAIN}\`)" \
  --label traefik.http.routers.expediente-http.entrypoints=web \
  --label traefik.http.routers.expediente-http.middlewares=redirect-https@file \
  "${IMAGE}" >/dev/null

docker network connect expediente-net expediente-api 2>/dev/null || true

echo "waiting for the API to answer on https://${HOST_DOMAIN}/health"
for _ in $(seq 1 40); do
  if curl -sf -m 5 "https://${HOST_DOMAIN}/health" >/dev/null 2>&1; then
    echo "up: https://${HOST_DOMAIN}"
    exit 0
  fi
  sleep 3
done

echo "the container is running but the public URL does not answer yet." >&2
echo "check: docker logs expediente-api --tail 20 ; docker logs traefik --tail 30" >&2
exit 1
