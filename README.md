# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

## Features

### Streamable HTTP proxy

The gateway sits between clients and upstream MCP servers on a single `/mcp` endpoint. One client run is not a single HTTP call — Streamable HTTP opens a session, streams on a GET, sends RPCs over POST, then closes with DELETE:

| Call | Why |
|------|-----|
| `POST /mcp` 200 | `initialize` |
| `POST /mcp` 202 | Session created (`Mcp-Session-Id`) |
| `GET /mcp` 200 | SSE stream — server can push messages on that connection |
| `POST /mcp` 200 | `tools/list`, `tools/call`, … |
| `DELETE /mcp` 200 | Client closes the session |

Allowed traffic shows the same pattern on `:8080` (gateway) and `:8000` (upstream). Flow: **client → gateway → server**.

### Tool policy

The gateway enforces an allow-list for `tools/call` via [`policy.yaml`](./policy.yaml) in the repo root.

```yaml
tools_allowed:
  - echo
```

| Traffic | Gateway behavior |
|---------|------------------|
| `tools/call` for an allowed tool | Forwarded to upstream |
| `tools/call` for anything else | Blocked locally — JSON-RPC error, HTTP 200 |
| Everything else (`initialize`, `tools/list`, GET SSE, DELETE, …) | Pass-through unchanged |

Denied calls never reach upstream. MCP clients see a tool failure (e.g. `Tool 'ping' denied by gateway policy`), not an HTTP 4xx.

## Local running

All commands from the repo root:

| Service | Command | Port |
|---------|---------|------|
| Upstream MCP server | `uv run mcp-server` | `:8000` |
| Gateway | `uv run mcp-gateway` | `:8080` |
| Test client | `uv run mcp-client` | — (talks to gateway) |

Start server and gateway in separate terminals, then run the client when ready.

### E2E tests

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

**Manual**

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
