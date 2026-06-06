# MCP Gateway — Build Plan

Control plane between MCP clients (agents, apps, IDEs) and MCP servers (tools, APIs, data).

## Problem

Agents call MCP tools directly with little governance:

- No centralized auth
- No allow/deny policies per tool or tenant
- Weak audit trails for production debugging
- No cost or latency attribution per tool call

## Architecture (target)

```
Agent / Client  →  MCP Gateway  →  MCP Server(s)
                        │
                        ├─ Auth (API keys / OAuth — later)
                        ├─ Policy engine (allow/deny tools)
                        ├─ Audit log (who called what, when)
                        └─ Tracing (OpenTelemetry — later)
```

## Non-goals (v0)

- Not a Langfuse/LangSmith clone
- Not tied to any specific product domain
- Not multi-region HA on day one

## Tech stack (TBD at M1)

| Layer | Candidate |
|-------|-----------|
| Runtime | TypeScript (Node) or Python |
| MCP | `@modelcontextprotocol/sdk` |
| Audit storage | SQLite → Postgres |
| Observability | OpenTelemetry |
| Dashboard | React (much later) |

## Milestones

| # | Goal | Done |
|---|------|------|
| M0 | Repo, PLAN.md, README | [x] |
| M1 | Pass-through proxy (client → gateway → server) | [ ] |
| M2 | Tool allowlist / denylist policy | [ ] |
| M3 | Audit log per tool call | [ ] |
| M4 | Auth on gateway ingress | [ ] |
| M5 | OTel spans + basic run docs | [ ] |

## M1 scope (next session)

1. Pick TypeScript vs Python (recommend TS — MCP SDK maturity, proxy patterns)
2. Minimal project scaffold (`package.json`, `src/`)
3. Config file with upstream MCP server URL
4. Dumb pass-through: bytes in, bytes out — no auth, no policy
5. Local run instructions (3 terminals: server, gateway, test client)

**Exit criteria:** A client can reach an MCP server only through the gateway.

## M2 preview

- YAML/JSON policy file: allowed tools per route
- Block disallowed `tools/call` with structured error
- Log policy violations

## Notes

- Keep OSS generic — no employer-specific logic
- Freshbooks overlap is conceptual (patterns), not code
- Phase 2 project (later): Agent Eval Platform — separate repo
