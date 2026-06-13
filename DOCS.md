# MCP Gateway — Documentation

Design reference for the gateway. For how to run and test locally, see [README.md](./README.md).

---

## Problem

Agents and apps call MCP tools directly with little governance:

- No centralized auth at the tool boundary
- No allow/deny policies per tool
- Weak audit trails for production debugging
- No single place to observe or control tool traffic

This project is a **control-plane gateway** — a single choke point between MCP clients and MCP servers. It is not an agent framework.

---

## Transport: Streamable HTTP

The gateway sits between clients and upstream MCP servers on a single `/mcp` endpoint. One client run is not a single HTTP call — Streamable HTTP opens a session, streams on a GET, sends RPCs over POST, then closes with DELETE:


| Call              | Why                                                      |
| ----------------- | -------------------------------------------------------- |
| `POST /mcp` 200   | `initialize`                                             |
| `POST /mcp` 202   | Session created (`Mcp-Session-Id`)                       |
| `GET /mcp` 200    | SSE stream — server can push messages on that connection |
| `POST /mcp` 200   | `tools/list`, `tools/call`, …                            |
| `DELETE /mcp` 200 | Client closes the session                                |


Allowed traffic shows the same pattern on `:8080` (gateway) and `:8000` (upstream). Flow: **client → gateway → server**.

MCP-relevant headers (`Mcp-Session-Id`, `Accept`, `Content-Type`, …) are forwarded; hop-by-hop headers are stripped. SSE responses are streamed without buffering the full body.

---

## Architecture

```
Agent / Client  →  MCP Gateway  →  MCP Server(s)
                        │
                        ├─ Tool policy (allow/deny tools)
                        ├─ Audit log (who called what, when)
                        ├─ Auth (JWT HS256)
                        └─ Tracing (OpenTelemetry)
```

---

## Tool policy

Decide which tools may run before they hit upstream. Applies **only** to incoming `POST` bodies where JSON-RPC `method == "tools/call"`. Everything else (`initialize`, `tools/list`, GET, DELETE) passes through unchanged.

### Flow

```mermaid
sequenceDiagram
    participant C as MCP Client
    participant R as routes/mcp.py
    participant P as MCPService
    participant T as ToolsPolicyService
    participant S as MCP Server

    C->>R: POST /mcp (tools/call)
    R->>P: proxy()
    P->>T: check_post(body)

    alt tool in tools_allowed
        T-->>P: pass
        P->>S: forward
        S-->>P: result
        P-->>C: HTTP 200
    else tool not allowed
        T-->>P: PolicyDenial
        P-->>C: HTTP 200 + JSON-RPC error
        Note over S: never called
    end
```



### Config

Policy lives in `[policy.yaml](./policy.yaml)` at the repo root:

```yaml
tools_allowed:
  - echo
```

- **Allow-list, default deny** — only listed tools may run; anything else is blocked at the gateway before reaching upstream.
- **Why allow-list over deny-list** — for a governance gateway, default deny is the safer posture. A new tool added upstream is automatically blocked until explicitly permitted. A deny-list would silently allow it.
- **Extensible schema** — future keys (e.g. `resources_allowed`) can live in the same file without renaming the loader.
- **Docker** — `policy.yaml` is bind-mounted into the gateway container; edit and restart, no image rebuild.

Configuration: upstream URL via `GATEWAY_UPSTREAM_URL` (default `http://127.0.0.1:8000/mcp`, see `.env.example`); gateway listens on `0.0.0.0:8080`; missing or invalid `policy.yaml` exits at startup.

**Why only `tools/call`?** That is where side effects happen — API calls, writes, shell commands. Discovery and read paths stay untouched; control is applied at the execution boundary only.

**Why only `POST`?** All JSON-RPC calls travel over POST with a body. GET opens an SSE stream; DELETE closes a session — neither carries a `tools/call` payload.

### Denial response

Denied calls return **HTTP 200** with a JSON-RPC error body:

```json
{
  "jsonrpc": "2.0",
  "id": "<request id>",
  "error": {
    "code": -32000,
    "message": "Tool 'ping' denied by gateway policy"
  }
}
```

**Why HTTP 200, not 4xx?** MCP / JSON-RPC treats HTTP as transport. The real result lives in the message envelope — `result` on success, `error` on failure — both on HTTP 200. Returning a 403 would break MCP clients that expect a parseable JSON-RPC body, and it conflates "bad HTTP request" with "valid request, blocked by policy". MCP clients surface this as a failed tool call; a chat UI shows the error message, not an HTTP status code. Denied calls **never reach upstream**.

---

## Audit log

Append-only record of every `tools/call` — for debugging and compliance.

### Flow

```mermaid
sequenceDiagram
    participant C as MCP Client
    participant P as MCPService
    participant A as AuditService
    participant D as Audit DB
    participant S as MCP Server

    Note over C,P: tools/call only — after policy check

    alt allowed
        P->>S: forward
        S-->>P: result
        P->>A: record(allowed, latency_ms, client_identity)
        A->>D: INSERT audit_events
        P-->>C: HTTP 200
    else denied at gateway
        P->>A: record(denied, client_identity)
        A->>D: INSERT audit_events
        P-->>C: HTTP 200 + JSON-RPC error
        Note over S: never called
    end
```



Denied calls are logged **before** the policy error is returned. Allowed calls are logged after a successful upstream response. Gateway errors (502/504) are not audited.

### What gets logged


| Field             | Description                                                          |
| ----------------- | -------------------------------------------------------------------- |
| `timestamp`       | UTC ISO-8601                                                         |
| `tool_name`       | From JSON-RPC `params.name`                                          |
| `outcome`         | `allowed` or `denied`                                                |
| `latency_ms`      | Upstream round-trip for allowed calls; `0` for denials               |
| `request_id`      | JSON-RPC `id`                                                        |
| `client_identity` | JWT `sub` claim from authenticated client; `NULL` when auth disabled |


### Storage

Configured via `GATEWAY_AUDIT_DB_PATH`:


| Environment    | Value                                | Backend                   |
| -------------- | ------------------------------------ | ------------------------- |
| Local `uv run` | `data/audit.db` (default)            | SQLite file, auto-created |
| Docker Compose | `postgresql://…@postgres:5432/audit` | Postgres service          |


Postgres credentials live in `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`). Compose builds the gateway URL from those vars.

---

## Auth (JWT)

Ingress authentication on `/mcp` only. `/health` (liveness) and `/health/upstream` (readiness — probes upstream, 503 if unreachable) stay public. Runs **before** policy and audit.

### Config


| Variable             | Description                                 |
| -------------------- | ------------------------------------------- |
| `GATEWAY_JWT_SECRET` | HS256 shared secret. Unset = auth disabled. |


### Flow

```mermaid
sequenceDiagram
    participant C as MCP Client
    participant R as routes/mcp.py
    participant J as AuthService
    participant P as MCPService
    participant S as MCP Server

    C->>R: POST /mcp + Authorization: Bearer jwt
    R->>J: authenticate(authorization)

    alt valid JWT
        J-->>R: sub (client_identity)
        R->>P: proxy(..., client_identity)
        P->>S: forward (Authorization included)
        S-->>P: response
        P-->>C: HTTP 200
        Note over P: tools/call → audit logs client_identity
    else missing or invalid JWT
        J-->>R: None
        R-->>C: 401 Unauthorized
        Note over P,S: proxy never runs
    end
```



Identity comes from the JWT `sub` claim — not from a client-supplied header. The gateway validates; it does not issue tokens. When `GATEWAY_JWT_SECRET` is unset, `authenticate` is a no-op and all requests pass through without identity.

### What we ship today (local approach)

Current auth is a **dev/local shortcut only**:

- One HS256 shared secret (`GATEWAY_JWT_SECRET`) — gateway validates, smoke client signs
- `mcp-client` auto-signs a JWT with a stand-in `CLIENT` object (`sub: smoke-client`)
- No `/login` route, no IdP integration, no token refresh

This is **not** a production auth setup.

### Production target (not implemented yet)


| Piece              | Production approach                                                      |
| ------------------ | ------------------------------------------------------------------------ |
| **Who signs?**     | `/login` route or external IdP (Auth0, Keycloak, …) — not the MCP client |
| **Who validates?** | Gateway only — same `sub` → `client_identity` flow as today              |
| **Secret**         | Client never holds the signing secret; it only sends a token it received |


Secret rotation changes the key, not `sub` — audit identity stays stable.

### Denial

Missing or invalid token → **HTTP 401** before policy or audit run:

```json
{ "detail": "Unauthorized" }
```

---

## Tracing (OpenTelemetry)

Distributed traces for every authenticated `/mcp` request — latency breakdown across the gateway layers. Spans export via OTLP HTTP when configured; unset endpoint = tracing disabled (no overhead).

Auth failures (**401**) are **not** traced — `authenticate` runs before the route handler, so no `gateway.request` span is created.

### Flow

```mermaid
sequenceDiagram
    participant C as MCP Client
    participant R as routes/mcp.py
    participant P as MCPService
    participant T as TracingService
    participant S as MCP Server
    participant J as Jaeger

    C->>R: POST /mcp (tools/call)
    Note over R: after auth passes
    R->>T: get_tracer()
    R->>R: span gateway.request

    alt tools/call
        R->>P: proxy()
        P->>P: span policy.check
        alt tool allowed
            P->>P: policy.outcome = allowed
            P->>S: span upstream.call → forward
            S-->>P: response
            P-->>R: HTTP 200
        else tool denied
            P->>P: policy.outcome = denied
            P-->>R: HTTP 200 + JSON-RPC error
            Note over S: never called
        end
    else not tools/call
        R->>P: proxy()
        P->>S: span upstream.call → forward
        S-->>P: response
        P-->>R: HTTP 200
    end

    R->>J: export spans (OTLP, batched)
```

Each HTTP request to `/mcp` is one trace. `tools/call` adds `policy.check`; denied calls stop before `upstream.call`.

### Config

| Variable | Description |
| -------- | ----------- |
| `GATEWAY_OTEL_EXPORTER_ENDPOINT` | OTLP HTTP URL. Unset = tracing off. |
| `GATEWAY_OTEL_SERVICE_NAME` | Service name on exported spans (default `mcp-gateway`). |

`TracingService` starts in app lifespan (`start()` / `shutdown()`). Shutdown flushes batched spans on exit.

### Spans

Nested waterfall — one trace per `/mcp` HTTP request:

```
gateway.request          ← routes/mcp.py
  ├─ policy.check        ← services/mcp.py (tools/call only)
  └─ upstream.call       ← services/mcp.py (skipped when policy denies)
```

| Span | Attributes | When |
| ---- | ---------- | ---- |
| `gateway.request` | `http.method`, `http.route`, `client.identity`, `http.status_code` | Every authenticated `/mcp` request |
| `policy.check` | `tool.name`, `policy.outcome` (`allowed` / `denied`) | `tools/call` POST only |
| `upstream.call` | `upstream.url`, `http.status_code` | Request reaches upstream (502/504 on failure) |

**Why layered spans?** Each span answers one question: how long at the HTTP boundary (`gateway.request`), was policy involved (`policy.check`), how slow was upstream (`upstream.call`).

