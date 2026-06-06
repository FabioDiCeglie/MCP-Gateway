# MCP Gateway — Build Plan

Control plane between MCP clients (agents, apps, IDEs) and MCP servers (tools, APIs, data).

---

## Problem

Agents call MCP tools directly with little governance:

- No centralized auth
- No allow/deny policies per tool or tenant
- Weak audit trails for production debugging
- No cost or latency attribution per tool call

Production teams need a **single choke point** — not another agent framework.

---

## Target architecture

```
Agent / Client  →  MCP Gateway  →  MCP Server(s)
                        │
                        ├─ Auth (API keys / OAuth — M4)
                        ├─ Policy engine (allow/deny tools — M2)
                        ├─ Audit log (who called what, when — M3)
                        └─ Tracing (OpenTelemetry — M5)
```

**M1 is intentionally dumb:** the gateway sits in the path and forwards traffic unchanged. Every later milestone adds a hook at that same insertion point without rewriting the proxy.

---

## Design principles

These guide every milestone. If a shortcut violates one, we don't take it.

1. **One insertion point** — Clients talk to the gateway; the gateway talks to upstream. No bypass paths.
2. **Transport first, semantics later** — M1 forwards bytes (HTTP). M2+ inspects JSON-RPC only where needed (e.g. `tools/call`).
3. **Config over code** — Routes, upstreams, and policy live in files, not hard-coded constants.
4. **Small, reviewable diffs** — One milestone capability per PR/session when possible.
5. **OSS-generic** — No employer-specific logic; patterns only.
6. **Test the wire** — Each milestone ships a way to prove bytes or messages flow end-to-end locally (`uv run` and, from M2, `docker compose up`).
7. **Minimal layout** — `src/config.py` (YAML + Pydantic) and `src/main.py` (FastAPI app, routes, `uvicorn.run`). No nested package folder; entrypoint is `main()`.

---

## Tech stack (decided)

| Layer | Choice | Notes |
|-------|--------|-------|
| Runtime | **Python 3.11+** | Team preference; strong MCP ecosystem |
| Package manager | **uv** | Fast, lockfile, modern default for new Python projects |
| MCP SDK | **`mcp`** ([python-sdk](https://github.com/modelcontextprotocol/python-sdk)) | Official SDK; client + server + Streamable HTTP |
| HTTP / ASGI | **FastAPI + uvicorn** | Familiar route style; Starlette under the hood for proxy + middleware |
| Upstream HTTP | **httpx** | Async client for forwarding requests |
| Config | **YAML + Pydantic** | Human-readable; validates at startup |
| Audit storage | SQLite → Postgres | M3 |
| Observability | OpenTelemetry | M5 |
| Local dev | **Docker + Docker Compose** | Introduced in M1–M2; stack grows per milestone |
| Dashboard | React | Much later; not in v0 |

### Transport choice for v0

| Transport | M1 | Notes |
|-----------|----|-------|
| **Streamable HTTP** | ✅ Primary | Spec-recommended; gateway-friendly; one `/mcp` endpoint |
| stdio | ❌ Later | Clients spawn processes; gateway wraps via `mcp-proxy` or similar |
| SSE (legacy) | ❌ | Deprecated; not worth building on |

M1 assumes upstream speaks **Streamable HTTP** at a known URL (e.g. `http://127.0.0.1:8000/mcp`).

**Legacy (HTTP + SSE)** — two endpoints, two connections:

- `POST /messages` → send JSON-RPC to server
- `GET /events` (SSE) → always listen here for server → client traffic

Client juggles two URLs. Older spec; deprecated.

```
┌────────┐                              ┌────────┐
│ Client │                              │ Server │
└───┬────┘                              └───┬────┘
    │                                       │
    │  ① POST /messages  (send JSON-RPC)   │
    ├──────────────────────────────────────►│
    │                                       │
    │  ② GET /events  (SSE, always open)    │
    ├──────────────────────────────────────►│
    │◄──────────── server pushes ───────────┤
    │         (notifications, etc.)         │
    │                                       │
    ▼                                       ▼

Two separate paths:
  /messages  →  client talks TO server
  /events    →  client listens FROM server (SSE)
```

**Streamable HTTP** — one endpoint:

- `POST /mcp` → send JSON-RPC; response is either one JSON or an SSE stream
- `GET /mcp` (optional) → open SSE when server needs to push without a POST

```
┌────────┐                              ┌────────┐
│ Client │                              │ Server │
└───┬────┘                              └───┬────┘
    │                                       │
    │  POST /mcp  (send JSON-RPC)           │
    ├──────────────────────────────────────►│
    │◄── JSON (one shot) ──────────────────┤   ← simple call
    │   OR                                  │
    │◄── SSE stream (many messages) ───────┤   ← streaming call
    │                                       │
    │  GET /mcp  (optional, SSE)            │
    ├──────────────────────────────────────►│
    │◄──────── server pushes ───────────────┤   ← push without POST
    │                                       │
    ▼                                       ▼

One path for everything:
  /mcp  →  send AND receive (JSON or SSE depending on the request)
```

---

## Milestones

| # | Goal | Done |
|---|------|------|
| M0 | Repo, PLAN.md, README | [x] |
| M1 | Scaffold + config | [x] |
| M2 | Pass-through proxy | [ ] |
| M3 | Tool policy | [ ] |
| M4 | Audit log | [ ] |
| M5 | Auth | [ ] |
| M6 | Observability | [ ] |

### M0 — Repo

Planning repo with README and build plan. No code yet.

**Done:** [x]

### M1 — Scaffold + config

Set up the Python project and config loading — nothing proxied yet.

- `pyproject.toml` with uv; deps: `mcp`, `httpx`, `fastapi`, `uvicorn`, `pyyaml`, `pydantic`
- Package layout: `src/config.py` + `src/main.py` (no nested package folder)
- Entrypoint: `uv run mcp-gateway` → `main:main` (plain `main()`, not `cli()`)
- Routes defined FastAPI-style in `main()` after config load (e.g. `@app.get("/health")` stub today; `/mcp` proxy in M2)
- `gateway.yaml`: listen host/port + upstream URL
- Pydantic validation at startup; clear error on bad config
- `docker/Dockerfile`: `python:3.11-slim`, install `uv`, `uv sync --frozen`, expose **8080**
- `docker/compose.dev.yaml`: bind-mount `src/` + `gateway.yaml` so rebuilds aren't needed for every edit

**Done:** [x]

**Done when:** `uv run mcp-gateway --config gateway.yaml` starts and reads config without crashing (proxy can be a stub); `docker build -f docker/Dockerfile .` succeeds and container starts with mounted config.

### M2 — Pass-through proxy

First working gateway — bytes in, bytes out. Client talks to gateway; gateway talks to upstream.

- HTTP reverse proxy on `/mcp` in `main.py`: forward `GET` and `POST` to upstream URL from config (httpx; FastAPI route or middleware)
- Stream SSE responses without buffering the full body
- Forward MCP-relevant headers (`Accept`, `Content-Type`, `Mcp-Session-Id`, etc.); strip hop-by-hop headers
- No auth, no policy, no JSON-RPC parsing
- `examples/upstream_server.py` (minimal FastMCP server) + `examples/test_client.py`
- `docker/compose.yaml`: `gateway` (**8080**) + `upstream` (**8000**) on `mcp-net`; config uses `http://upstream:8000/mcp` (service name, not `127.0.0.1`)
- Optional `client` service (compose profile `test`) runs `examples/test_client.py` on the compose network
- Smoke test: `initialize` + `tools/list` via `http://127.0.0.1:8080/mcp` — works with `uv run` **and** `docker compose -f docker/compose.yaml up --build`

**Done when:** client reaches MCP server only through the gateway; upstream URL is config-only; compose stack is the default way to run the full path locally.

### M3 — Tool policy

First control-plane feature — decide which tools may run before they hit upstream.

- Policy file (YAML): global or per-route allow/deny lists
- Gateway parses JSON-RPC **only** for incoming `tools/call` requests
- Allowed calls pass through unchanged; denied calls return structured MCP error
- Everything else (`initialize`, `tools/list`, resources, etc.) still pass-through
- Mount `policy.yaml` in compose (alongside `gateway.yaml`) so policy changes don't require image rebuild

**Done when:** calling a denied tool fails at the gateway; allowed tools still work end-to-end via compose smoke test.

### M4 — Audit log

Durable record of what happened — for debugging and compliance.

- SQLite append-only store (single file, zero ops for v0)
- Log every `tools/call`: timestamp, tool name, allow/deny, latency_ms, request_id
- Log policy denials from M3
- Client identity field reserved (populated once M5 lands)
- Compose: named volume (or bind-mount `./data`) for SQLite file so audit data survives `docker compose down`

**Done when:** after a compose smoke session, querying the DB (host or `docker compose exec gateway`) shows tool calls with timestamps and outcomes.

### M5 — Auth

Lock down ingress — only known clients reach upstream.

- API key via header (e.g. `Authorization: Bearer <key>` or `X-API-Key`)
- Reject unauthenticated requests with 401 before proxy logic runs
- Valid keys pass through; key identity written to audit log
- Compose: API keys via env / `.env` (gitignored); `test_client` service passes key from same source

**Done when:** request without key → 401; valid key → full proxy flow works via compose.

### M6 — Observability

Make the gateway operable in production.

- `GET /health` — liveness/readiness for orchestrators
- OpenTelemetry spans: `gateway.request`, `upstream.call`, `policy.check`
- `docs/runbook.md`: config reference, `uv run` + compose quick start, deploy notes, common failures
- Compose: add `jaeger` (or `otel-collector`) service; gateway exporter endpoint via env

**Done when:** `docker compose up` shows traces in Jaeger UI; runbook documents the full compose stack (gateway, upstream, audit volume, tracing).

---

## Non-goals (v0)

- Not Langfuse/LangSmith
- Not multi-region HA
- Not stdio bridging (use [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) upstream)
- Not a dashboard
- Not Kubernetes / production-hardened images (non-root, distroless) — compose is dev-only for v0
