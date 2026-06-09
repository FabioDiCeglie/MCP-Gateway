# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

See [DOCS.md](./DOCS.md) for architecture and design decisions.

## Local running

All commands from the repo root:

| Service | Command | Port |
|---------|---------|------|
| Upstream MCP server | `uv run mcp-server` | `:8000` |
| Gateway | `uv run mcp-gateway` | `:8080` |
| Test client | `uv run mcp-client` | — (talks to gateway) |

Start server and gateway in separate terminals, then run the client when ready.

## E2E tests

**Manual** — three terminals:

```bash
uv run mcp-server
uv run mcp-gateway
uv run mcp-client
```

**Automated (local)** — cleans docker + local processes, starts server and gateway, runs the client, prints pass/fail and the audit log table:

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

**Docker** — starts server, gateway, and client. The client runs once after the gateway is healthy, then exits.

```bash
cd docker
docker compose build
docker compose up -d
```

Compose overrides `GATEWAY_UPSTREAM_URL` so the gateway reaches `mcp-server` on the Docker network, and bind-mounts `policy.yaml` into the gateway container.

**Automated (docker):** same checks; prints the audit log table from Postgres.

```bash
./tests/e2e-docker.sh
```

## Project layout

```
src/
  config.py           # GatewayConfig, policy loading
  main.py             # FastAPI app + entrypoint
  routes/
    mcp.py            # /mcp proxy route
    health.py         # GET /health
  services/
    mcp.py            # MCPService — upstream proxy + audit hook
    tools_policy.py   # ToolsPolicyService — tools/call allow-list
    audit.py          # AuditService — append-only tool call log
policy.yaml           # Tool policy (tools_allowed)
mcp-server/           # Demo upstream (echo, ping)
mcp-client/           # Smoke-test client
docker/               # Dockerfile + compose stack
tests/                # e2e-local.sh, e2e-docker.sh
```
