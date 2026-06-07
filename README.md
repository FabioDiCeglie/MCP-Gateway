# MCP Gateway

A control plane between MCP clients and MCP servers — auth, policy, audit, and observability.

See [PLAN.md](./PLAN.md) for the build plan.

## Local smoke test

Three terminals from the repo root:

```bash
uv run mcp-server    # upstream on :8000
uv run mcp-gateway   # gateway on :8080
uv run mcp-client    # client → gateway → server
```

## Docker Compose smoke test

Start the stack (server + gateway):

```bash
docker compose -f docker/docker-compose.yaml up --build
```

Run the client once (separate terminal):

```bash
docker compose -f docker/docker-compose.yaml --profile test run --rm mcp-client
```

## Configuration

Gateway listens on `0.0.0.0:8080` (fixed). Copy `.env.example` to `.env` and adjust if needed:

| Variable | Default | Example (compose) |
|----------|---------|-------------------|
| `GATEWAY_UPSTREAM_URL` | `http://127.0.0.1:8000/mcp` | `http://mcp-server:8000/mcp` |

Compose loads `../.env` if present, but overrides `GATEWAY_UPSTREAM_URL` to reach the server on the Docker network.

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
