#!/usr/bin/env bash
# End-to-end smoke test: clean docker + local processes → uv run → client through gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker/docker-compose.yaml"
GATEWAY_HEALTH_URL="http://127.0.0.1:8080/health"
LOG_DIR="${ROOT}/tests/.logs"
MAX_WAIT_SECONDS=60

SERVER_PID=""
GATEWAY_PID=""

ok() {
  echo "    OK  $*"
}

fail() {
  echo "    FAIL  $*" >&2
}

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti ":${port}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    kill ${pids} 2>/dev/null || true
  fi
}

cleanup() {
  kill "${SERVER_PID}" "${GATEWAY_PID}" 2>/dev/null || true
  kill_port 8000
  kill_port 8080
}

wait_for_port() {
  local port="$1"
  local label="$2"
  local deadline=$((SECONDS + MAX_WAIT_SECONDS))

  until lsof -ti ":${port}" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      fail "${label} did not start on port ${port} within ${MAX_WAIT_SECONDS}s"
      echo "        see ${LOG_DIR}/${label}.log" >&2
      return 1
    fi
    sleep 1
  done
}

wait_for_health() {
  local deadline=$((SECONDS + MAX_WAIT_SECONDS))

  until curl -sf "${GATEWAY_HEALTH_URL}" >/dev/null; do
    if (( SECONDS >= deadline )); then
      fail "gateway health check timed out after ${MAX_WAIT_SECONDS}s"
      echo "        see ${LOG_DIR}/mcp-gateway.log" >&2
      return 1
    fi
    sleep 1
  done
}

print_pass() {
  local client_output="$1"

  echo
  echo "========================================"
  echo "  E2E LOCAL PASSED"
  echo "========================================"
  echo "  flow:   client → gateway (:8080) → server (:8000)"
  echo "  health: ${GATEWAY_HEALTH_URL}"
  echo "  client: $(grep '^Connected to' <<<"${client_output}")"
  echo "  tools:  $(grep '^Tools:' <<<"${client_output}" | cut -d' ' -f2-)"
  echo "========================================"
  echo "  server logs:  ${LOG_DIR}/mcp-server.log"
  echo "  gateway logs: ${LOG_DIR}/mcp-gateway.log"
  echo "========================================"
}

trap cleanup EXIT

cd "${ROOT}"
mkdir -p "${LOG_DIR}"

echo
echo "==> E2E local smoke test"
echo

echo "==> Syncing dependencies"
uv sync --reinstall-package mcp-gateway --quiet
ok "dependencies synced"

echo "==> Cleaning Docker stack"
docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans 2>/dev/null || true
ok "docker stack cleaned"

echo "==> Stopping local processes on :8000 and :8080"
kill_port 8000
kill_port 8080
ok "ports cleared"

echo "==> Starting mcp-server (:8000)"
uv run mcp-server >"${LOG_DIR}/mcp-server.log" 2>&1 &
SERVER_PID=$!
wait_for_port 8000 "mcp-server"
ok "mcp-server listening on :8000"

echo "==> Starting mcp-gateway (:8080)"
uv run mcp-gateway >"${LOG_DIR}/mcp-gateway.log" 2>&1 &
GATEWAY_PID=$!
wait_for_health
ok "mcp-gateway healthy at ${GATEWAY_HEALTH_URL}"

echo "==> Running mcp-client through gateway"
output="$(uv run mcp-client 2>&1)"
echo "${output}" | sed 's/^/    /'

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
