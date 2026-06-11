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

**Working in progress**: Tracing will get its own section later.

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



Denied calls are logged **before** the policy error is returned. Allowed calls are logged after the upstream responds (or times out).

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

Ingress authentication on `/mcp` only. `/health` stays public. Runs **before** policy and audit.

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

