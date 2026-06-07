# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

See [PLAN.md](./PLAN.md) for the build plan.

## Local running

All commands from the repo root:

| Service | Command | Port |
|---------|---------|------|
| Upstream MCP server | `uv run mcp-server` | `:8000` |
| Gateway | `uv run mcp-gateway` | `:8080` |
| Test client | `uv run mcp-client` | — (talks to gateway) |

Flow: **client → gateway → server**. The client never hits the server directly.

Start server and gateway in separate terminals, then run the client when ready.

## E2E tests

### Local

**Manual** — three terminals:

```bash
uv run mcp-server
uv run mcp-gateway
uv run mcp-client
```

**Automated** — cleans docker + local processes, starts server and gateway, runs the client, prints pass/fail:

```bash
./tests/e2e-local.sh
```

Expected client output: `Tools: echo`

### Docker

Starts server, gateway, and client. The client runs once after the gateway is healthy, then exits.

```bash
cd docker
docker compose build
docker compose up -d
```

Compose overrides `GATEWAY_UPSTREAM_URL` so the gateway reaches `mcp-server` on the Docker network. The client runs once and exits — use the automated script to verify output.

**Automated:**

```bash
./tests/e2e-docker.sh
```

## Streamable HTTP session lifecycle

One client run is not a single HTTP call. Streamable HTTP MCP opens a session, streams on a GET, sends RPCs over POST, then closes with DELETE:

| Call | Why |
|------|-----|
| `POST /mcp` 200 | `initialize` |
| `POST /mcp` 202 | Session created (`Mcp-Session-Id`) |
| `GET /mcp` 200 | SSE stream — server can push messages on that connection |
| `POST /mcp` 200 | `tools/list` |
| `DELETE /mcp` 200 | Client closes the session |

The gateway forwards each hop unchanged — you see the same pattern on `:8080` (gateway) and `:8000` (upstream).
