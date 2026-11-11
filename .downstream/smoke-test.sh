#!/usr/bin/env bash
set -euo pipefail
image=${1:?Pass the locally built image tag}
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
name="jellyfin-web-smoke-${GITHUB_RUN_ID:-local}-$$"
cleanup() {
    code=$?
    if [[ $code -ne 0 ]]; then
        docker logs "$name-web" 2>/dev/null || true
        docker logs "$name-backend" 2>/dev/null || true
    fi
    docker rm -f "$name-web" "$name-backend" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
    exit "$code"
}
trap cleanup EXIT
docker network create "$name" >/dev/null
docker run -d --name "$name-backend" --network "$name" --network-alias jellyfin \
    --mount "type=bind,src=$root/.downstream/tests/http_probe.py,dst=/probe.py,readonly" \
    python:3.13-alpine python /probe.py serve >/dev/null
docker run -d --name "$name-web" --network "$name" \
    -p 127.0.0.1::8080 --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp --tmpfs /etc/nginx/conf.d:uid=101,gid=101,mode=0755 \
    "$image" >/dev/null
port=$(docker inspect --format '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "$name-web")
python3 "$root/.downstream/tests/http_probe.py" probe "$port"
docker exec "$name-web" nginx -t
