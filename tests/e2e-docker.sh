#!/usr/bin/env bash
# End-to-end smoke test: clean stack → compose up → client through gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker/docker-compose.yaml"
GATEWAY_HEALTH_URL="http://127.0.0.1:8080/health"
MAX_WAIT_SECONDS=60

ok() {
  echo "    OK  $*"
}

fail() {
  echo "    FAIL  $*" >&2
}

print_pass() {
  local client_output="$1"

  echo
  echo "========================================"
  echo "  E2E DOCKER PASSED"
  echo "========================================"
  echo "  flow:   client → gateway (:8080) → server (:8000)"
  echo "  health: ${GATEWAY_HEALTH_URL}"
  echo "  client: $(grep '^Connected to' <<<"${client_output}")"
  echo "  tools:  $(grep '^Tools:' <<<"${client_output}" | cut -d' ' -f2-)"
  echo "========================================"
  echo "  logs: docker compose -f docker/docker-compose.yaml logs"
  echo "========================================"
}

wait_for_client() {
  local deadline=$((SECONDS + MAX_WAIT_SECONDS))
  local status

  while true; do
    status="$(docker inspect -f '{{.State.Status}}' mcp-client 2>/dev/null || echo "missing")"
    if [[ "${status}" == "exited" ]]; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      fail "mcp-client did not finish within ${MAX_WAIT_SECONDS}s"
      docker compose -f "${COMPOSE_FILE}" logs
      return 1
    fi
    sleep 1
  done
}

cd "${ROOT}"

echo
echo "==> E2E docker smoke test"
echo

echo "==> Cleaning Docker stack"
docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans
ok "docker stack cleaned"

echo "==> Starting stack (server + gateway + client)"
docker compose -f "${COMPOSE_FILE}" up -d --build
ok "containers started"

echo "==> Waiting for mcp-client"
wait_for_client
ok "mcp-client finished"

output="$(docker compose -f "${COMPOSE_FILE}" logs --no-log-prefix mcp-client 2>&1)"
echo "${output}" | sed 's/^/    /'

exit_code="$(docker inspect -f '{{.State.ExitCode}}' mcp-client)"
if [[ "${exit_code}" != "0" ]]; then
  fail "mcp-client exited with code ${exit_code}"
  exit 1
fi

if ! grep -q "Tools: echo, ping" <<<"${output}"; then
  fail "expected tools 'echo, ping' in client output"
  exit 1
fi
ok "client received tools/list via gateway"

if ! grep -q "echo: hello" <<<"${output}"; then
  fail "expected allowed echo call to succeed"
  exit 1
fi
ok "allowed tool call passed through gateway"

if ! grep -q "ping: denied" <<<"${output}"; then
  fail "expected denied ping call at gateway"
  exit 1
fi
ok "denied tool call blocked at gateway"

print_pass "${output}"
