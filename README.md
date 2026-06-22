# MCP Gateway

[![CI](https://github.com/FabioDiCeglie/MCP-Gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/FabioDiCeglie/MCP-Gateway/actions/workflows/ci.yml)

A control plane between MCP clients and MCP servers — auth, rate limiting, policy, audit, and observability.

See [DOCS.md](./DOCS.md) for design decisions and [PLAN.md](./PLAN.md) for the milestone roadmap.

## Architecture

Agents call MCP tools through a single choke point. The gateway forwards Streamable HTTP on `/mcp` and hooks control-plane logic at one insertion point (`MCPService.proxy()`).

```
Agent / Client  →  MCP Gateway  →  MCP Server(s)
                        │
                        ├─ Auth (JWT HS256 — dev/local)
                        ├─ Rate limit (Redis — tools/call per client)
                        ├─ Tool policy (allow-list on tools/call)
                        ├─ Audit log (SQLite / Postgres)
                        └─ Tracing (OpenTelemetry → Jaeger)
```

## Verify

```bash
uv sync --group dev
uv run ruff check . && uv run ruff format --check .
uv run pytest
./tests/e2e-docker.sh
```

Copy [`.env.example`](./.env.example) to `.env` before running tests or Docker Compose.

## Local running

All commands from the repo root:

| Service | Command | Port |
|---------|---------|------|
| Upstream MCP server | `uv run mcp-server` | `:8000` |
| Gateway | `uv run mcp-gateway` | `:8080` |
| Test client | `uv run mcp-client` | — (talks to gateway) |

Start server and gateway in separate terminals, then run the client when ready.

Rate limiting uses Redis (`GATEWAY_REDIS_URL`, default `redis://127.0.0.1:6379/0`). For manual local runs, start Redis on `:6379` first — for example:

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

Limits are fixed in code: **10** `tools/call` requests per client identity per **60s** window (auth must be enabled). Over limit → **429** + `Retry-After`.

For tracing locally, set `GATEWAY_OTEL_EXPORTER_ENDPOINT` in `.env` (see `.env.example`) and run Jaeger:

```bash
docker compose -f docker/docker-compose.yaml up -d jaeger
```

Jaeger UI: http://localhost:16686

## Unit tests

Policy, auth, audit, rate limit, MCP proxy, and deps:

```bash
uv run pytest
```

## E2E tests

**Manual** — three terminals:

```bash
uv run mcp-server
uv run mcp-gateway
uv run mcp-client
```

**Automated (local)** — cleans processes, starts Redis and Jaeger (if tracing enabled in `.env`), server and gateway via `uv run`, runs the client, then a rate-limit probe (burst → 429 → wait → OK). Prints pass/fail, audit log, and Jaeger span checks:

```bash
./tests/e2e-local.sh
```

The rate-limit step takes ~60s (window expiry). Shared probe logic lives in [`tests/redis.sh`](./tests/redis.sh).

Expected client output:

```
Tools: echo, ping
echo: hello
ping: denied (...)
```

`echo` is allowed; `ping` is not in `policy.yaml`.

When tracing is enabled, the script prompts before stopping Jaeger so you can inspect traces in the UI.

E2E also checks audit rows (`echo|allowed`, `ping|denied`, `echo|rate_limited`) and, when tracing is on, Jaeger spans including `rate_limit.check`.

**Docker** — full stack: `mcp-server`, gateway, Postgres, Redis, Jaeger, smoke client.

```bash
cd docker
docker compose build
docker compose up -d
```

Compose overrides `GATEWAY_UPSTREAM_URL`, `GATEWAY_AUDIT_DB_PATH`, `GATEWAY_OTEL_EXPORTER_ENDPOINT`, and `GATEWAY_REDIS_URL` for the Docker network. Other vars come from `.env`.

**Automated (docker):** same checks as local; audit from Postgres; rate-limit probe via `tests/redis.sh`; Jaeger span verification; prompts before cleanup when run interactively:

```bash
./tests/e2e-docker.sh
```

CI runs ruff, unit tests (`uv run pytest`), and `./tests/e2e-docker.sh` on every pull request (see [`.github/workflows/ci.yml`](./.github/workflows/ci.yml)).

## Project layout

```
src/
  config.py           # GatewayConfig, policy loading
  main.py             # FastAPI app + entrypoint
  deps/
    auth.py           # authenticate dependency (JWT)
  routes/
    mcp.py            # /mcp proxy route
    health.py         # GET /health, GET /health/upstream
  services/
    mcp.py            # MCPService — proxy, rate limit, policy, audit, trace spans
    rate_limit.py     # RateLimitService — Redis fixed-window per client
    tools_policy.py   # ToolsPolicyService — tools/call allow-list
    audit.py          # AuditService — append-only tool call log
    auth.py           # AuthService — JWT validation
    tracing.py        # TracingService — OpenTelemetry bootstrap
policy.yaml           # Tool policy (tools_allowed)
mcp-server/           # Demo upstream (echo, ping)
mcp-client/           # Smoke-test client
docker/               # Dockerfile, compose stack, Jaeger UI config
tests/
  unit/               # pytest — policy, auth, audit, rate_limit, mcp, deps, tracing
  redis.sh            # shared Redis + rate-limit probe for e2e scripts
  e2e-local.sh        # local smoke test
  e2e-docker.sh       # docker smoke test
```
