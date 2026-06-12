# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

See [DOCS.md](./DOCS.md) for architecture and design decisions.

Copy [`.env.example`](./.env.example) to `.env` before running tests or Docker Compose.

## Local running

All commands from the repo root:

| Service | Command | Port |
|---------|---------|------|
| Upstream MCP server | `uv run mcp-server` | `:8000` |
| Gateway | `uv run mcp-gateway` | `:8080` |
| Test client | `uv run mcp-client` | — (talks to gateway) |

Start server and gateway in separate terminals, then run the client when ready.

For tracing locally, set `GATEWAY_OTEL_EXPORTER_ENDPOINT` in `.env` (see `.env.example`) and run Jaeger:

```bash
docker compose -f docker/docker-compose.yaml up -d jaeger
```

Jaeger UI: http://localhost:16686

## E2E tests

**Manual** — three terminals:

```bash
uv run mcp-server
uv run mcp-gateway
uv run mcp-client
```

**Automated (local)** — cleans processes, starts Jaeger (if tracing enabled in `.env`), server and gateway via `uv run`, runs the client, prints pass/fail, audit log, and Jaeger span checks:

```bash
./tests/e2e-local.sh
```

Expected client output:

```
Tools: echo, ping
echo: hello
ping: denied (...)
```

`echo` is allowed; `ping` is not in `policy.yaml`.

When tracing is enabled, the script prompts before stopping Jaeger so you can inspect traces in the UI.

**Docker** — full stack: `mcp-server`, gateway, Postgres, Jaeger, smoke client.

```bash
cd docker
docker compose build
docker compose up -d
```

Compose overrides `GATEWAY_UPSTREAM_URL`, `GATEWAY_AUDIT_DB_PATH`, and `GATEWAY_OTEL_EXPORTER_ENDPOINT` for the Docker network. Other vars come from `.env`.

**Automated (docker):** same checks as local; audit from Postgres; Jaeger span verification; prompts before cleanup when run interactively:

```bash
./tests/e2e-docker.sh
```

CI runs `./tests/e2e-docker.sh` on every pull request (see [`.github/workflows/e2e.yml`](./.github/workflows/e2e.yml)).

## Project layout

```
src/
  config.py           # GatewayConfig, policy loading
  main.py             # FastAPI app + entrypoint
  deps/
    auth.py           # authenticate dependency (JWT)
  routes/
    mcp.py            # /mcp proxy route
    health.py         # GET /health
  services/
    mcp.py            # MCPService — proxy, policy, audit, trace spans
    tools_policy.py   # ToolsPolicyService — tools/call allow-list
    audit.py          # AuditService — append-only tool call log
    auth.py           # AuthService — JWT validation
    tracing.py        # TracingService — OpenTelemetry bootstrap
policy.yaml           # Tool policy (tools_allowed)
mcp-server/           # Demo upstream (echo, ping)
mcp-client/           # Smoke-test client
docker/               # Dockerfile, compose stack, Jaeger UI config
tests/                # e2e-local.sh, e2e-docker.sh
```
