#!/usr/bin/env bash
# End-to-end smoke test: clean docker + local processes → uv run → client through gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker/docker-compose.yaml"
GATEWAY_HEALTH_URL="http://127.0.0.1:8080/health"
GATEWAY_MCP_URL="http://127.0.0.1:8080/mcp"
JAEGER_UI_URL="http://127.0.0.1:16686"
LOG_DIR="${ROOT}/tests/.logs"
MAX_WAIT_SECONDS=60
EXPECTED_CLIENT_IDENTITY="smoke-client"

if [[ ! -f "${ROOT}/.env" ]]; then
  echo "❌  .env not found — copy .env.example to .env" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source "${ROOT}/.env"
set +a

OTEL_SERVICE_NAME="${GATEWAY_OTEL_SERVICE_NAME:-mcp-gateway}"
TRACING_ENABLED=false
if [[ -n "${GATEWAY_OTEL_EXPORTER_ENDPOINT:-}" ]]; then
  TRACING_ENABLED=true
fi

if [[ -z "${GATEWAY_JWT_SECRET:-}" ]]; then
  echo "❌  GATEWAY_JWT_SECRET is not set in .env" >&2
  exit 1
fi

SERVER_PID=""
GATEWAY_PID=""

section() {
  echo
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $1"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

step() {
  echo
  echo "▶ $1"
}

ok() {
  echo "  ✅  $*"
}

fail() {
  echo "  ❌  $*" >&2
}

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti ":${port}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    kill ${pids} 2>/dev/null || true
  fi
}

cleanup_processes() {
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
      echo "      see ${LOG_DIR}/${label}.log" >&2
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
      echo "      see ${LOG_DIR}/mcp-gateway.log" >&2
      return 1
    fi
    sleep 1
  done
}

print_pass() {
  local client_output="$1"

  echo
  echo "╔════════════════════════════════════════╗"
  echo "║  🎉  E2E LOCAL PASSED                  ║"
  echo "╚════════════════════════════════════════╝"
  echo
  echo "  Flow      client → gateway (:8080) → server (:8000)"
  echo "  Health    ${GATEWAY_HEALTH_URL}"
  echo "  Auth      🔐 JWT HS256 (from .env)"
  echo "  Identity  ${EXPECTED_CLIENT_IDENTITY}"
  echo "  Client    $(grep '^Connected to' <<<"${client_output}")"
  echo "  Tools     $(grep '^Tools:' <<<"${client_output}" | cut -d' ' -f2-)"
  echo "  Audit     SQLite (data/audit.db)"
  if [[ "${TRACING_ENABLED}" == "true" ]]; then
    echo "  Tracing   Jaeger ${JAEGER_UI_URL} (service: ${OTEL_SERVICE_NAME})"
  fi
  echo
  echo "  Logs"
  echo "    server   ${LOG_DIR}/mcp-server.log"
  echo "    gateway  ${LOG_DIR}/mcp-gateway.log"
}

verify_jaeger_traces() {
  local deadline=$((SECONDS + MAX_WAIT_SECONDS))
  local traces_json

  while (( SECONDS < deadline )); do
    if traces_json="$(curl -sf "${JAEGER_UI_URL}/api/traces?service=${OTEL_SERVICE_NAME}&limit=20" 2>/dev/null)"; then
      if grep -q '"operationName":"gateway.request"' <<<"${traces_json}"; then
        ok "gateway.request span found"
        if grep -q '"operationName":"policy.check"' <<<"${traces_json}"; then
          ok "policy.check span found"
        else
          fail "policy.check span not found in Jaeger"
          return 1
        fi
        if grep -q '"operationName":"upstream.call"' <<<"${traces_json}"; then
          ok "upstream.call span found"
        else
          fail "upstream.call span not found in Jaeger"
          return 1
        fi
        ok "Jaeger UI → ${JAEGER_UI_URL}"
        return 0
      fi
    fi
    sleep 2
  done

  fail "no OpenTelemetry traces in Jaeger after ${MAX_WAIT_SECONDS}s (service: ${OTEL_SERVICE_NAME})"
  tail -20 "${LOG_DIR}/mcp-gateway.log" | sed 's/^/      /'
  return 1
}

prompt_cleanup_jaeger() {
  local reply

  CLEANUP_PROMPTED=true
  section "👀  Inspect traces"
  echo "  Jaeger:  ${JAEGER_UI_URL}  (service: ${OTEL_SERVICE_NAME})"
  echo "  Gateway: ${GATEWAY_HEALTH_URL}  (stopped)"
  echo

  if [[ -t 0 ]]; then
    read -r -p "Stop Jaeger container? [y/N] " reply
  else
    echo "  Non-interactive — Jaeger left running."
    echo "  When finished:"
    echo "    docker compose -f docker/docker-compose.yaml stop jaeger"
    return
  fi

  if [[ "${reply}" =~ ^[Yy]$ ]]; then
    section "🧹  Cleanup"
    docker compose -f "${COMPOSE_FILE}" stop jaeger >/dev/null 2>&1 || true
    ok "jaeger stopped"
  else
    echo "  Jaeger left running."
    echo "  When finished:"
    echo "    docker compose -f docker/docker-compose.yaml stop jaeger"
  fi
}

finish() {
  local exit_code=$?
  trap - EXIT
  cleanup_processes
  if [[ "${TRACING_ENABLED}" == "true" && "${CLEANUP_PROMPTED:-}" != "true" ]]; then
    if (( exit_code != 0 )); then
      section "❌  Test failed"
      echo "  Jaeger still running for inspection."
    fi
    prompt_cleanup_jaeger
  fi
  exit "${exit_code}"
}

trap finish EXIT

cd "${ROOT}"
mkdir -p "${LOG_DIR}"

section "🧪  E2E local smoke test"

section "🔧  Setup"
step "Syncing dependencies"
uv sync --reinstall-package mcp-gateway --quiet
ok "dependencies synced"

step "Cleaning Docker stack"
docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans 2>/dev/null || true
ok "docker stack cleaned"

step "Clearing ports :8000 and :8080"
kill_port 8000
kill_port 8080
ok "ports cleared"

if [[ "${TRACING_ENABLED}" == "true" ]]; then
  step "Starting Jaeger (:16686) for local tracing"
  docker compose -f "${COMPOSE_FILE}" up -d jaeger
  ok "jaeger started (gateway → ${GATEWAY_OTEL_EXPORTER_ENDPOINT})"
fi

section "🚀  Stack"
step "Starting mcp-server (:8000)"
uv run mcp-server >"${LOG_DIR}/mcp-server.log" 2>&1 &
SERVER_PID=$!
wait_for_port 8000 "mcp-server"
ok "mcp-server listening"

step "Starting mcp-gateway (:8080)"
rm -f "${ROOT}/data/audit.db"
uv run mcp-gateway >"${LOG_DIR}/mcp-gateway.log" 2>&1 &
GATEWAY_PID=$!
wait_for_health
ok "mcp-gateway healthy"

section "🔐  Auth (JWT)"
echo "  secret     loaded from .env"
echo "  identity   ${EXPECTED_CLIENT_IDENTITY}  (JWT sub → audit client_identity)"

step "Reject — no Bearer token"
unauth_status="$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "${GATEWAY_MCP_URL}" \
  -H 'Content-Type: application/json' \
  -d '{}')"
if [[ "${unauth_status}" != "401" ]]; then
  fail "POST /mcp without token → expected 401, got ${unauth_status}"
  exit 1
fi
ok "POST /mcp without token → 401 Unauthorized"

step "Accept — valid JWT from mcp-client"
output="$(uv run mcp-client 2>&1)"
echo "${output}" | sed 's/^/      /'

if ! grep -q "Tools: echo, ping" <<<"${output}"; then
  fail "authenticated client did not receive tools/list"
  exit 1
fi
ok "JWT accepted — authenticated as ${EXPECTED_CLIENT_IDENTITY}"
ok "tools/list succeeded"

section "🛡️  Policy"
if ! grep -q "echo: hello" <<<"${output}"; then
  fail "allowed echo call did not succeed"
  exit 1
fi
ok "echo allowed → proxied to upstream"

if ! grep -q "ping: denied" <<<"${output}"; then
  fail "denied ping call not blocked at gateway"
  exit 1
fi
ok "ping denied → blocked at gateway"

section "📋  Audit (SQLite)"
audit_table="$(uv run python -c "
import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path('${ROOT}') / 'data' / 'audit.db')
columns = ['id', 'timestamp', 'tool_name', 'outcome', 'latency_ms', 'request_id', 'client_identity']
rows = conn.execute(
    f\"SELECT {', '.join(columns)} FROM audit_events ORDER BY id\"
).fetchall()

widths = [len(col) for col in columns]
for row in rows:
    for index, value in enumerate(row):
        widths[index] = max(widths[index], len(str(value) if value is not None else ''))

def format_row(cells):
    return '  '.join(
        str(cell if cell is not None else '').ljust(widths[index])
        for index, cell in enumerate(cells)
    )

lines = [
    format_row(columns),
    '  '.join('-' * width for width in widths),
    *(format_row(row) for row in rows),
]
print('\n'.join(lines))
")"
echo "${audit_table}" | sed 's/^/      /'

audit_rows="$(uv run python -c "
import sqlite3
conn = sqlite3.connect('${ROOT}/data/audit.db')
for tool_name, outcome, client_identity in conn.execute(
    'SELECT tool_name, outcome, client_identity FROM audit_events ORDER BY id'
):
    print(f'{tool_name}|{outcome}|{client_identity}')
")"

if ! grep -q "^echo|allowed|${EXPECTED_CLIENT_IDENTITY}$" <<<"${audit_rows}"; then
  fail "expected echo|allowed|${EXPECTED_CLIENT_IDENTITY}"
  exit 1
fi
ok "echo|allowed|${EXPECTED_CLIENT_IDENTITY}"

if ! grep -q "^ping|denied|${EXPECTED_CLIENT_IDENTITY}$" <<<"${audit_rows}"; then
  fail "expected ping|denied|${EXPECTED_CLIENT_IDENTITY}"
  exit 1
fi
ok "ping|denied|${EXPECTED_CLIENT_IDENTITY}"

if [[ "${TRACING_ENABLED}" == "true" ]]; then
  section "📡  Tracing (OpenTelemetry / Jaeger)"
  step "Waiting for spans in Jaeger (service: ${OTEL_SERVICE_NAME})"
  verify_jaeger_traces
fi

print_pass "${output}"

if [[ "${TRACING_ENABLED}" == "true" ]]; then
  prompt_cleanup_jaeger
fi
