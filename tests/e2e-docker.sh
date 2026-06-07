#!/usr/bin/env bash
# End-to-end smoke test: clean stack → compose up → client through gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker/docker-compose.yaml"
GATEWAY_HEALTH_URL="http://127.0.0.1:8080/health"
MAX_WAIT_SECONDS=60

cd "${ROOT}"

echo "==> Cleaning Docker stack"
docker compose -f "${COMPOSE_FILE}" --profile test down -v --remove-orphans

echo "==> Starting stack"
docker compose -f "${COMPOSE_FILE}" up -d --build

echo "==> Waiting for gateway (${GATEWAY_HEALTH_URL})"
deadline=$((SECONDS + MAX_WAIT_SECONDS))
until curl -sf "${GATEWAY_HEALTH_URL}" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "error: gateway did not become ready within ${MAX_WAIT_SECONDS}s" >&2
    docker compose -f "${COMPOSE_FILE}" logs
    exit 1
  fi
  sleep 1
done

echo "==> Running client through gateway (Docker network)"
output="$(docker compose -f "${COMPOSE_FILE}" --profile test run --rm mcp-client)"
echo "${output}"

if ! grep -q "Tools: echo" <<<"${output}"; then
  echo "error: expected tool 'echo' in client output" >&2
  exit 1
fi

echo "==> E2E passed"
