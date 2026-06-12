#!/usr/bin/env bash
# End-to-end smoke test: clean stack → compose up → client through gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/docker/docker-compose.yaml"
GATEWAY_HEALTH_URL="http://127.0.0.1:8080/health"
GATEWAY_MCP_URL="http://127.0.0.1:8080/mcp"
JAEGER_UI_URL="http://127.0.0.1:16686"
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

if [[ -z "${GATEWAY_JWT_SECRET:-}" ]]; then
  echo "❌  GATEWAY_JWT_SECRET is not set in .env" >&2
  exit 1
fi

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

print_pass() {
  local client_output="$1"

  echo
  echo "╔════════════════════════════════════════╗"
  echo "║  🎉  E2E DOCKER PASSED                 ║"
  echo "╚════════════════════════════════════════╝"
  echo
  echo "  Flow      client → gateway (:8080) → server (:8000)"
  echo "  Health    ${GATEWAY_HEALTH_URL}"
  echo "  Auth      🔐 JWT HS256 (from .env)"
  echo "  Identity  ${EXPECTED_CLIENT_IDENTITY}"
  echo "  Client    $(grep '^Connected to' <<<"${client_output}")"
  echo "  Tools     $(grep '^Tools:' <<<"${client_output}" | cut -d' ' -f2-)"
  echo "  Audit     Postgres (docker)"
  echo "  Tracing   Jaeger ${JAEGER_UI_URL} (service: ${OTEL_SERVICE_NAME})"
}

cleanup_docker() {
  docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans >/dev/null 2>&1 || true
}

finish() {
  local exit_code=$?
  trap - EXIT

  if [[ "${CLEANUP_PROMPTED:-}" == "true" ]]; then
    exit "${exit_code}"
  fi

  if (( exit_code != 0 )); then
    section "❌  Test failed"
    echo "  Stack left running for inspection."
  fi

  prompt_cleanup
  exit "${exit_code}"
}

wait_for_gateway_health() {
  local deadline=$((SECONDS + MAX_WAIT_SECONDS))

  until curl -sf "${GATEWAY_HEALTH_URL}" >/dev/null; do
    if (( SECONDS >= deadline )); then
      fail "gateway health check timed out after ${MAX_WAIT_SECONDS}s"
      docker compose -f "${COMPOSE_FILE}" logs gateway
      return 1
    fi
    sleep 1
  done
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
  docker compose -f "${COMPOSE_FILE}" logs gateway | tail -20 | sed 's/^/      /'
  return 1
}

prompt_cleanup() {
  local reply

  CLEANUP_PROMPTED=true
  section "👀  Inspect traces"
  echo "  Jaeger:  ${JAEGER_UI_URL}  (service: ${OTEL_SERVICE_NAME})"
  echo "  Gateway: ${GATEWAY_HEALTH_URL}"
  echo

  if [[ -t 0 ]]; then
    read -r -p "Clean up Docker stack? [y/N] " reply
  else
    echo "  Non-interactive — stack left running."
    echo "  When finished:"
    echo "    docker compose -f docker/docker-compose.yaml down -v"
    return
  fi

  if [[ "${reply}" =~ ^[Yy]$ ]]; then
    section "🧹  Cleanup"
    cleanup_docker
    ok "docker stack cleaned"
  else
    echo "  Stack left running."
    echo "  When finished:"
    echo "    docker compose -f docker/docker-compose.yaml down -v"
  fi
}

cd "${ROOT}"
trap finish EXIT

section "🧪  E2E docker smoke test"

section "🔧  Setup"
step "Cleaning Docker stack"
cleanup_docker
ok "docker stack cleaned"

section "🚀  Stack"
step "Starting containers (server + gateway + postgres + jaeger + client)"
docker compose -f "${COMPOSE_FILE}" up -d --build
ok "containers started"

step "Waiting for gateway health"
wait_for_gateway_health
ok "mcp-gateway healthy"

section "🔐  Auth (JWT)"
echo "  secret     loaded from .env (gateway + mcp-client via env_file)"
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
wait_for_client
ok "mcp-client finished"

output="$(docker compose -f "${COMPOSE_FILE}" logs --no-log-prefix mcp-client 2>&1)"
echo "${output}" | sed 's/^/      /'

exit_code="$(docker inspect -f '{{.State.ExitCode}}' mcp-client)"
if [[ "${exit_code}" != "0" ]]; then
  fail "mcp-client exited with code ${exit_code}"
  exit 1
fi

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

section "📋  Audit (Postgres)"
audit_table="$(docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  psql -U "${POSTGRES_USER:-gateway}" -d "${POSTGRES_DB:-audit}" \
  -c "SELECT id, timestamp, tool_name, outcome, latency_ms, request_id, client_identity FROM audit_events ORDER BY id")"
echo "${audit_table}" | sed 's/^/      /'

audit_rows="$(docker compose -f "${COMPOSE_FILE}" exec -T postgres \
  psql -U "${POSTGRES_USER:-gateway}" -d "${POSTGRES_DB:-audit}" -t -A \
  -c "SELECT tool_name, outcome, client_identity FROM audit_events ORDER BY id")"

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

section "📡  Tracing (OpenTelemetry / Jaeger)"
step "Waiting for spans in Jaeger (service: ${OTEL_SERVICE_NAME})"
verify_jaeger_traces

print_pass "${output}"
prompt_cleanup
