# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

See [DOCS.md](./DOCS.md) for architecture, design decisions, and configuration reference.

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

**Automated (local)** — cleans docker + local processes, starts server and gateway, runs the client, prints pass/fail:

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

**Automated (docker):**

```bash
./tests/e2e-docker.sh
```
