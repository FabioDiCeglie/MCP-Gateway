from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI

from config import POLICY_PATH, load_config
from routes import health_router, mcp_router
from services.audit import AuditService
from services.auth import AuthService
from services.mcp import MCPService
from services.rate_limit import RateLimitService
from services.tools_policy import ToolsPolicyService
from services.tracing import TracingService


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config
    tracing_service = TracingService(config.tracing)
    tracing_service.start()
    app.state.tracing_service = tracing_service
    audit_service = AuditService(config.audit.db_path)
    audit_service.open()
    app.state.audit_service = audit_service
    app.state.auth_service = AuthService(config.auth)
    app.state.tools_policy_service = ToolsPolicyService(config.policy)
    rate_limit_service = RateLimitService(config.rate_limit.redis_url)
    await rate_limit_service.open()
    app.state.rate_limit_service = rate_limit_service

    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.http_client = client
        app.state.mcp_service = MCPService(
            client,
            str(config.upstream.url),
            app.state.tools_policy_service,
            audit_service,
            rate_limit_service,
        )
        yield

    audit_service.close()
    await rate_limit_service.close()
    tracing_service.shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(health_router)
app.include_router(mcp_router)


def main() -> None:
    try:
        config = load_config()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    app.state.config = config

    allowed = ", ".join(config.policy.tools_allowed) or "(none)"
    auth_status = "JWT HS256" if config.auth.enabled else "disabled"
    tracing_status = (
        config.tracing.exporter_endpoint if config.tracing.enabled else "disabled"
    )
    rate_limit_status = config.rate_limit.redis_url
    print(
        f"Listening on {config.listen.host}:{config.listen.port} "
        f"→ {config.upstream.url} "
        f"(policy: {POLICY_PATH}, tools allowed: {allowed}, "
        f"audit: {config.audit.db_path}, auth: {auth_status}, "
        f"tracing: {tracing_status}, rate limit: {rate_limit_status})"
    )
    uvicorn.run(app, host=config.listen.host, port=config.listen.port)


if __name__ == "__main__":
    main()
