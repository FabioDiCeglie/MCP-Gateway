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

## Repository layout (target)

Evolve incrementally — don't scaffold everything on day one.

```
mcp-gateway/
├── PLAN.md
├── README.md
├── pyproject.toml              # M1.1
├── gateway.yaml                # M1.2 — upstream URL, listen addr
├── src/
│   └── mcp_gateway/
│       ├── __init__.py
│       ├── __main__.py         # `uv run mcp-gateway`
│       ├── config.py           # load + validate gateway.yaml
│       └── proxy.py            # HTTP pass-through (M1)
├── examples/
│   ├── upstream_server.py      # minimal FastMCP server for local dev
│   └── test_client.py          # list tools via gateway
└── tests/                      # M1.5+
    └── test_proxy.py
```

---

## Milestones

| # | Goal | Done |
|---|------|------|
| M0 | Repo, PLAN.md, README | [x] |
| M1 | Pass-through proxy (client → gateway → server) | [ ] |
| M2 | Tool allowlist / denylist policy | [ ] |
| M3 | Audit log per tool call | [ ] |
| M4 | Auth on gateway ingress | [ ] |
| M5 | OTel spans + basic run docs | [ ] |

---

## M1 — Pass-through proxy

**Goal:** A client can reach an MCP server **only** through the gateway.

**Non-goals for M1:** auth, policy, audit, stdio bridging, multi-upstream routing, TLS termination.

### M1 sub-steps (do in order)

| Step | What | Exit check |
|------|------|------------|
| **M1.1** | Project scaffold (`pyproject.toml`, `src/mcp_gateway/`, `uv`) | `uv run python -c "import mcp_gateway"` works |
| **M1.2** | `gateway.yaml` — listen host/port + upstream URL | Invalid config fails fast with clear error |
| **M1.3** | HTTP reverse proxy — forward `GET`/`POST` on `/mcp` to upstream | `curl` through gateway returns same status/body as direct |
| **M1.4** | Header forwarding — `Accept`, `Content-Type`, `Mcp-Session-Id`, SSE headers | MCP client session survives multiple round-trips |
| **M1.5** | Example upstream server + test client script | 3-terminal demo documented in README |
| **M1.6** | Smoke test (pytest or script) | CI-ready check that `tools/list` works via gateway |

### M1 proxy behavior (spec)

```
Client                    Gateway                     Upstream
  │  POST /mcp (JSON-RPC)     │                            │
  ├──────────────────────────►│  POST upstream.url         │
  │                           ├───────────────────────────►│
  │                           │◄───────────────────────────┤
  │◄──────────────────────────┤  (stream or JSON body)     │
  │                           │                            │
  │  GET /mcp (SSE, optional) │                            │
  ├──────────────────────────►│  GET upstream.url          │
  │                           ├───────────────────────────►│
  │◄──── SSE stream ──────────┤◄──── SSE stream ───────────┤
```

- **Pass-through:** request body and response body are not parsed in M1.
- **Streaming:** if upstream returns `text/event-stream`, gateway streams chunks without buffering the full response.
- **Hop-by-hop headers** (`Connection`, `Transfer-Encoding`, etc.) are stripped; **MCP session headers** are preserved.

### Example config (`gateway.yaml`)

```yaml
listen:
  host: "127.0.0.1"
  port: 8080

upstream:
  url: "http://127.0.0.1:8000/mcp"
```

### Local demo (3 terminals)

```bash
# Terminal 1 — upstream MCP server
uv run examples/upstream_server.py

# Terminal 2 — gateway
uv run mcp-gateway --config gateway.yaml

# Terminal 3 — verify (test client or MCP Inspector → http://127.0.0.1:8080/mcp)
uv run examples/test_client.py
```

### M1 exit criteria (all must pass)

- [ ] Client connects to gateway URL, not upstream URL
- [ ] `initialize` + `tools/list` succeed through gateway
- [ ] Upstream URL is configurable via `gateway.yaml` only
- [ ] README has copy-paste run instructions

---

## M2 — Tool policy

**Goal:** Block disallowed `tools/call` before they reach upstream.

- Policy file (YAML): allowed/denied tools per route or globally
- Gateway parses JSON-RPC **only** for `tools/call` requests
- Structured MCP error on denial; log violation (stdout → structured logs in M3)

**Exit criteria:** Calling a denied tool returns gateway error; allowed tools still pass through.

---

## M3 — Audit log

**Goal:** Durable record of every tool call (and policy denial).

- SQLite first (single file, zero ops)
- Fields: timestamp, client id (placeholder until M4), tool name, allow/deny, latency_ms, request_id
- Append-only; no PII in v0 unless explicitly configured

**Exit criteria:** Query audit DB after a session; see tool calls with timestamps.

---

## M4 — Auth on ingress

**Goal:** Only authenticated clients reach upstream.

- API key header (v0 auth — simple, widely understood)
- Reject unauthenticated requests before proxy
- Optional: forward identity to audit log

**Exit criteria:** Request without key → 401; valid key → proxy works.

---

## M5 — Observability + ops docs

**Goal:** Production-debuggable gateway.

- OpenTelemetry spans: `gateway.request`, `upstream.call`, `policy.check`
- `docs/runbook.md`: deploy, config reference, troubleshooting
- Health endpoint (`GET /health`)

**Exit criteria:** Traces visible in local OTel collector or Jaeger; runbook covers common failures.

---

## Non-goals (v0)

- Not a Langfuse/LangSmith clone
- Not tied to any specific product domain
- Not multi-region HA on day one
- Not a stdio-to-HTTP bridge (use [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) upstream if needed)
- Not a UI dashboard

---

## Open questions (resolve as we go)

| Question | Lean | Revisit |
|----------|------|---------|
| Single vs multi-upstream routing | Single upstream in M1 | M2+ add named routes |
| Stateful vs stateless gateway | Stateless HTTP proxy in M1 | Re-evaluate if session stickiness needed |
| License | MIT (match MCP SDK) | Before first public release |
