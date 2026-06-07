# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

See [PLAN.md](./PLAN.md) for the build plan.

## Local smoke test

Run all three from the repo root in separate terminals. The client talks to the gateway on `:8080`, not the server directly.

```bash
uv run mcp-server    # upstream MCP server (:8000)
uv run mcp-gateway   # gateway (:8080)
uv run mcp-client    # client → gateway → server
```

Expected client output: `Tools: echo`

## Docker smoke test

Same flow, but everything runs in containers. Compose overrides the upstream URL so the gateway reaches `mcp-server` on the Docker network.

```bash
cd docker
docker compose build
docker compose up -d
```

Or run the full e2e script from the repo root (cleans stack, starts services, runs the client):

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
