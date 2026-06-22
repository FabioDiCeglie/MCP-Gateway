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
                        ├─ Policy engine (allow/deny tools — M3)
                        ├─ Audit log (what happened — M4; who — M5 fills `client_identity`)
                        ├─ Auth (JWT HS256 — M5; `sub` → audit `client_identity`)
                        ├─ Tracing (OpenTelemetry — M6)
                        └─ Rate limiter (Redis, per client — M7)
```

**M2 is intentionally dumb:** the gateway sits in the path and forwards traffic unchanged. Every later milestone adds a hook at `MCPService.proxy()` without rewriting the proxy.

---

## Design principles

These guide every milestone. If a shortcut violates one, we don't take it.

1. **One insertion point** — Clients talk to the gateway; the gateway talks to upstream. No bypass paths.
2. **Transport first, semantics later** — M2 forwards bytes (HTTP). M3+ inspects JSON-RPC only where needed (e.g. `tools/call`).
3. **Config over code** — Upstreams and policy live in env/files, not hard-coded constants.
4. **Small, reviewable diffs** — One milestone capability per PR/session when possible.
5. **OSS-generic** — No employer-specific logic; patterns only.
6. **Test the wire** — Each milestone ships a way to prove bytes or messages flow end-to-end locally (`uv run` and `docker compose up`).
7. **Thin layers** — `src/config.py` (env + Pydantic), `src/routes/` (HTTP adapters), `src/services/` (orchestration), `src/main.py` (app wiring + `uvicorn.run`). Entrypoint is `main()`.

---

## Tech stack (decided)

| Layer | Choice | Notes |
|-------|--------|-------|
| Runtime | **Python 3.11+** | Team preference; strong MCP ecosystem |
| Package manager | **uv** | Fast, lockfile, modern default for new Python projects |
| MCP SDK | **`mcp`** ([python-sdk](https://github.com/modelcontextprotocol/python-sdk)) | Official SDK; client + server + Streamable HTTP |
| HTTP / ASGI | **FastAPI + uvicorn** | Familiar route style; Starlette under the hood for proxy + middleware |
| Upstream HTTP | **httpx** | Async client for forwarding requests |
| Config | **Env vars + YAML (policy)** | Gateway via env; policy file in M3 |
| Audit storage | SQLite → Postgres | M4 |
| Observability | OpenTelemetry | M6 |
| Rate limit store | **Redis** | M7 — shared counters across gateway replicas |
| Local dev | **Docker + Docker Compose** | Introduced in M1–M2; stack grows per milestone |
| Dashboard | React | Much later; not in v0 |

### Transport choice for v0

| Transport | M2 | Notes |
|-----------|----|-------|
| **Streamable HTTP** | ✅ Primary | Spec-recommended; gateway-friendly; one `/mcp` endpoint |
| stdio | ❌ Later | Clients spawn processes; gateway wraps via `mcp-proxy` or similar |
| SSE (legacy) | ❌ | Deprecated; not worth building on |

Gateway assumes upstream speaks **Streamable HTTP** at a known URL (e.g. `GATEWAY_UPSTREAM_URL=http://127.0.0.1:8000/mcp`).

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
| M2 | Pass-through proxy | [x] |
| M3 | Tool policy | [x] |
| M4 | Audit log | [x] |
| M5 | Auth + `client_identity` in audit | [x] |
| M6 | Observability | [x] |
| M7 | Rate limiter | [x] |

### M0 — Repo

Planning repo with README and build plan. No code yet.

**Done:** [x]

### M1 — Scaffold + config

Set up the Python project and config loading — nothing proxied yet.

- `pyproject.toml` with uv; deps: `mcp`, `httpx`, `fastapi`, `uvicorn`, `pyyaml`, `pydantic`
- Package layout: `src/config.py` + `src/main.py`
- Entrypoint: `uv run mcp-gateway` → `main:main` (plain `main()`, not `cli()`)
- `GET /health` stub (upstream URL in response)
- Gateway config: listen on `0.0.0.0:8080`; upstream via `GATEWAY_UPSTREAM_URL` env
- Pydantic validation at startup; clear error on bad config
- `docker/Dockerfile`: `python:3.11-slim`, install `uv`, `uv sync --frozen`, expose **8080**
- `docker/docker-compose.yaml`: bind-mount `src/` so rebuilds aren't needed for every edit

**Done:** [x]

**Done when:** `uv run mcp-gateway` starts and reads config without crashing (proxy can be a stub); `docker build -f docker/Dockerfile .` succeeds and container starts.

### M2 — Pass-through proxy

First working gateway — bytes in, bytes out. Client talks to gateway; gateway talks to upstream.

- `src/routes/mcp.py` — HTTP adapter on `/mcp` (`GET`, `POST`, `DELETE`)
- `src/services/mcp.py` — `MCPService.proxy()` forwards to upstream; streams SSE or returns buffered JSON; 502/504 on upstream errors
- `src/routes/health.py` — `GET /health` (status + configured upstream URL)
- `src/main.py` — FastAPI app, lifespan (shared `httpx.AsyncClient`), router registration, config load
- Stream SSE responses without buffering the full body
- Forward MCP-relevant headers (`Accept`, `Content-Type`, `Mcp-Session-Id`, etc.); strip hop-by-hop headers
- No auth, no policy, no JSON-RPC parsing
- Gateway config via env (`GATEWAY_UPSTREAM_URL`); listen on `0.0.0.0:8080`
- `docker/docker-compose.yaml`: `gateway`, `mcp-server`, `mcp-client` services; bind-mount `src/`
- Smoke test: `initialize` + `tools/list` via `http://127.0.0.1:8080/mcp` — `tests/e2e-local.sh`, `tests/e2e-docker.sh`

**Done:** [x]

**Done when:** client reaches MCP server only through the gateway; smoke test passes with `uv run` and `docker compose up`.

### M3 — Tool policy

First control-plane feature — decide which tools may run before they hit upstream.

- Policy file (YAML): global or per-route allow/deny lists
- Gateway parses JSON-RPC **only** for incoming `tools/call` requests
- Allowed calls pass through unchanged; denied calls return structured MCP error
- Everything else (`initialize`, `tools/list`, resources, etc.) still pass-through
- Mount `policy.yaml` in compose so policy changes don't require image rebuild

**Done:** [x]

**Done when:** calling a denied tool fails at the gateway; allowed tools still work end-to-end via compose smoke test.

### M4 — Audit log

Durable record of what happened — for debugging and compliance.

- SQLite append-only store (single file, zero ops for v0)
- Log every `tools/call`: timestamp, tool name, allow/deny, latency_ms, request_id
- Log policy denials from M3
- Client identity field reserved (populated once M5 lands)
- Compose: Postgres service + `postgres-data` volume; SQLite file for local `uv run`
- `GATEWAY_AUDIT_DB_PATH` — file path (SQLite) or connection URL (Postgres)

**Done:** [x]

**Done when:** after a compose smoke session, querying the DB (host or `docker compose exec postgres`) shows tool calls with timestamps and outcomes.

### M5 — Auth

Lock down ingress — only authenticated clients reach upstream. **Completes the audit story:** M4 reserved `client_identity`; M5 fills it from the JWT `sub` claim.

- JWT via `Authorization: Bearer <token>` — HS256 shared secret (`GATEWAY_JWT_SECRET`); unset = auth disabled (local dev)
- `deps/authenticate` on `/mcp` — 401 before proxy / policy / audit when auth enabled
- Valid token → `sub` passed into `AuditService.record_tool_call(..., client_identity=...)`; `Authorization` forwarded to upstream
- Every audited `tools/call` row includes **who** called it — not just what and when
- Compose: `GATEWAY_JWT_SECRET` via `.env` (gitignored); `mcp-client` auto-signs for smoke tests (production: token from `/login` or IdP)
- E2E: 401 without token; `client_identity` in audit (SQLite local, Postgres docker)

**Done:** [x]

**Done when:** request without token → 401; valid JWT → full proxy flow works via compose; audit query shows `client_identity` populated for allowed and denied tool calls.

### M6 — Observability

Make the gateway operable in production.

- `GET /health` — liveness (`status` + configured upstream URL; always 200 when the process is up)
- OpenTelemetry spans: `gateway.request`, `upstream.call`, `policy.check`
- `TracingService` + `GATEWAY_OTEL_*` env; unset = tracing disabled
- Compose: `jaeger` service; gateway exports OTLP HTTP via env (compose overrides endpoint for the Docker network)
- E2E: span verification in Jaeger (`e2e-local.sh`, `e2e-docker.sh`); interactive prompt to inspect traces before cleanup

**Done:** [x]

**Done when:** e2e scripts verify `gateway.request`, `policy.check`, and `upstream.call` in Jaeger UI; `./tests/e2e-docker.sh` leaves the stack up on demand so traces can be inspected at `:16686`.

### M7 — Rate limiter

Protect upstream and control cost — cap how often authenticated clients may invoke tools before traffic reaches policy/upstream.

- `RateLimitService` + hook in `MCPService.proxy()` after auth identity is known, before policy/upstream
- Limits apply to **`tools/call` only** (pass-through for `initialize`, `tools/list`, etc.)
- **Redis** fixed window (`INCR` + `EXPIRE`); key `mcp-gateway:rate_limit:{client_identity}` — shared across gateway replicas
- Limits as constants in `src/services/rate_limit.py` (`10` calls / `60`s window); `GATEWAY_REDIS_URL` for store (default `redis://127.0.0.1:6379/0`)
- Key by `client_identity` when auth enabled; auth disabled → rate limiting skipped (no IP fallback)
- Over limit → **429** with structured MCP JSON-RPC error (`-32001`) + `Retry-After` header; audit row with outcome `rate_limited`
- OpenTelemetry span: `rate_limit.check` (`tool.name`, `client.identity`, `rate_limit.outcome`)
- Compose: `redis` service; gateway `GATEWAY_REDIS_URL` override
- E2E: shared probe in `tests/redis.sh` — burst → 429 → wait → OK (`e2e-local.sh`, `e2e-docker.sh`); audit `rate_limited`; Jaeger `rate_limit.check`

**Done:** [x]

**Done when:** compose smoke test proves a client cannot exceed the `tools/call` budget; audit DB shows `rate_limited` rows; Jaeger shows `rate_limit.check` spans.

---

## Non-goals (v0)

- Not Langfuse/LangSmith
- Not multi-region HA
- Not stdio bridging (use [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) upstream)
- Not a dashboard
- Not Kubernetes / production-hardened images (non-root, distroless) — compose is dev-only for v0
