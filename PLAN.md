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
6. **Test the wire** — Each milestone ships a way to prove bytes or messages flow end-to-end locally.

---

## Tech stack (decided)

| Layer | Choice | Notes |
|-------|--------|-------|
| Runtime | **Python 3.11+** | Team preference; strong MCP ecosystem |
| Package manager | **uv** | Fast, lockfile, modern default for new Python projects |
| MCP SDK | **`mcp`** ([python-sdk](https://github.com/modelcontextprotocol/python-sdk)) | Official SDK; client + server + Streamable HTTP |
| HTTP / ASGI | **Starlette + uvicorn** | Lightweight; enough for proxy + future middleware |
| Upstream HTTP | **httpx** | Async client for forwarding requests |
| Config | **YAML + Pydantic** | Human-readable; validates at startup |
| Audit storage | SQLite → Postgres | M3 |
| Observability | OpenTelemetry | M5 |
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
| M1 | Scaffold + config | [ ] |
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

- `pyproject.toml` with uv; deps: `mcp`, `httpx`, `uvicorn`, `pyyaml`, `pydantic`
- Package layout: `src/mcp_gateway/` with entrypoint (`uv run mcp-gateway`)
- `gateway.yaml`: listen host/port + upstream URL
- Pydantic validation at startup; clear error on bad config

**Done when:** `uv run mcp-gateway --config gateway.yaml` starts and reads config without crashing (proxy can be a stub).

### M2 — Pass-through proxy

First working gateway — bytes in, bytes out. Client talks to gateway; gateway talks to upstream.

- HTTP reverse proxy on `/mcp`: forward `GET` and `POST` to upstream URL from config
- Stream SSE responses without buffering the full body
- Forward MCP-relevant headers (`Accept`, `Content-Type`, `Mcp-Session-Id`, etc.); strip hop-by-hop headers
- No auth, no policy, no JSON-RPC parsing
- `examples/upstream_server.py` (minimal FastMCP server) + `examples/test_client.py`
- Smoke test: `initialize` + `tools/list` succeed via `http://127.0.0.1:8080/mcp`

**Done when:** client reaches MCP server only through the gateway; upstream URL is config-only.

### M3 — Tool policy

First control-plane feature — decide which tools may run before they hit upstream.

- Policy file (YAML): global or per-route allow/deny lists
- Gateway parses JSON-RPC **only** for incoming `tools/call` requests
- Allowed calls pass through unchanged; denied calls return structured MCP error
- Everything else (`initialize`, `tools/list`, resources, etc.) still pass-through

**Done when:** calling a denied tool fails at the gateway; allowed tools still work end-to-end.

### M4 — Audit log

Durable record of what happened — for debugging and compliance.

- SQLite append-only store (single file, zero ops for v0)
- Log every `tools/call`: timestamp, tool name, allow/deny, latency_ms, request_id
- Log policy denials from M3
- Client identity field reserved (populated once M5 lands)

**Done when:** after a test session, querying the DB shows tool calls with timestamps and outcomes.

### M5 — Auth

Lock down ingress — only known clients reach upstream.

- API key via header (e.g. `Authorization: Bearer <key>` or `X-API-Key`)
- Reject unauthenticated requests with 401 before proxy logic runs
- Valid keys pass through; key identity written to audit log

**Done when:** request without key → 401; valid key → full proxy flow works.

### M6 — Observability

Make the gateway operable in production.

- `GET /health` — liveness/readiness for orchestrators
- OpenTelemetry spans: `gateway.request`, `upstream.call`, `policy.check`
- `docs/runbook.md`: config reference, local run, deploy notes, common failures

**Done when:** traces visible in a local collector (Jaeger/OTel); runbook covers setup and troubleshooting.

---

## Non-goals (v0)

- Not Langfuse/LangSmith
- Not multi-region HA
- Not stdio bridging (use [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) upstream)
- Not a dashboard
