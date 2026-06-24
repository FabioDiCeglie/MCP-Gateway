#!/usr/bin/env bash
# Shared Redis setup + rate-limit probe for e2e scripts.

redis_start_local() {
  docker rm -f redis >/dev/null 2>&1 || true
  docker run -d --name redis -p 6379:6379 redis:7-alpine >/dev/null
}

redis_stop_local() {
  docker rm -f redis >/dev/null 2>&1 || true
}

redis_reset_counter_local() {
  docker exec redis redis-cli DEL "mcp-gateway:rate_limit:${EXPECTED_CLIENT_IDENTITY}" \
    >/dev/null
}

redis_reset_counter_compose() {
  docker compose -f "${COMPOSE_FILE}" exec -T redis \
    redis-cli DEL "mcp-gateway:rate_limit:${EXPECTED_CLIENT_IDENTITY}" >/dev/null
}

redis_run_probe() {
  local root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  local mode="${2:-local}"
  local -a probe_cmd

  export GATEWAY_MCP_URL EXPECTED_CLIENT_IDENTITY PYTHONUNBUFFERED=1

  if [[ "${mode}" == "compose" ]]; then
    probe_cmd=(
      docker compose -f "${COMPOSE_FILE}" exec -T
      -e "GATEWAY_MCP_URL=${GATEWAY_MCP_URL:-http://127.0.0.1:8080/mcp}"
      -e "EXPECTED_CLIENT_IDENTITY=${EXPECTED_CLIENT_IDENTITY}"
      -e PYTHONUNBUFFERED=1
      gateway uv run python -u -
    )
  else
    probe_cmd=(bash -c "cd '${root}' && exec uv run python -u -")
  fi

  "${probe_cmd[@]}" <<'PY'
import asyncio
import os
import sys
import time

import httpx
import jwt

from services.rate_limit import RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW_SEC

GATEWAY_URL = os.environ.get("GATEWAY_MCP_URL", "http://127.0.0.1:8080/mcp")
SECRET = os.environ["GATEWAY_JWT_SECRET"]
IDENTITY = os.environ.get("EXPECTED_CLIENT_IDENTITY", "smoke-client")


def token() -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": IDENTITY, "iat": now, "exp": now + 3600},
        SECRET,
        algorithm="HS256",
    )


async def tools_call(
    client: httpx.AsyncClient, headers: dict[str, str], request_id: int
) -> tuple[int, str | None]:
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": "rate-limit-e2e"}},
    }
    response = await client.post(GATEWAY_URL, json=body, headers=headers)
    return response.status_code, response.headers.get("retry-after")


async def main() -> None:
    headers = {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-rate-limit", "version": "0.1.0"},
            },
        }
        init_response = await client.post(GATEWAY_URL, json=init, headers=headers)
        if init_response.status_code not in {200, 202}:
            print(f"initialize failed: {init_response.status_code}", file=sys.stderr)
            raise SystemExit(1)
        session_id = init_response.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        for index in range(RATE_LIMIT_CALLS):
            status, _ = await tools_call(client, headers, 100 + index)
            if status != 200:
                print(f"call {index + 1}: expected 200, got {status}", file=sys.stderr)
                raise SystemExit(1)
            print(f"call {index + 1}/{RATE_LIMIT_CALLS}: 200 OK", flush=True)

        status, retry_after = await tools_call(client, headers, 999)
        if status != 429:
            print(f"over-limit: expected 429, got {status}", file=sys.stderr)
            raise SystemExit(1)
        print(f"over-limit: 429 Retry-After={retry_after}", flush=True)

        wait_sec = RATE_LIMIT_WINDOW_SEC + 1
        print(f"waiting {wait_sec}s…", flush=True)
        await asyncio.sleep(wait_sec)

        status, _ = await tools_call(client, headers, 1000)
        if status != 200:
            print(f"after window: expected 200, got {status}", file=sys.stderr)
            raise SystemExit(1)
        print("after window: 200 OK", flush=True)


asyncio.run(main())
PY
}

redis_verify_probe_output() {
  local output="$1"
  grep -q "over-limit: 429" <<<"${output}" \
    && grep -q "after window: 200 OK" <<<"${output}"
}

run_rate_limit_e2e() {
  local mode="$1"
  local root="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

  section "⏱️  Rate limit (Redis)"
  step "Reset Redis counter for ${EXPECTED_CLIENT_IDENTITY}"
  if [[ "${mode}" == "local" ]]; then
    redis_reset_counter_local
  else
    redis_reset_counter_compose
  fi
  ok "counter cleared"

  step "Burst → 429 → wait → OK"
  local rate_log rate_output
  rate_log="$(mktemp)"
  redis_run_probe "${root}" "${mode}" 2>&1 | tee "${rate_log}" | sed 's/^/      /'
  if (( PIPESTATUS[0] != 0 )); then
    rm -f "${rate_log}"
    fail "rate limit probe failed"
    exit 1
  fi
  rate_output="$(<"${rate_log}")"
  rm -f "${rate_log}"
  if ! redis_verify_probe_output "${rate_output}"; then
    fail "rate limit probe failed"
    exit 1
  fi
  ok "429 when over limit, allowed again after window"
}
