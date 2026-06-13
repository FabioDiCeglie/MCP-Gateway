from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import GatewayConfig

router = APIRouter()

_UPSTREAM_PROBE_TIMEOUT = 3.0


@router.api_route(
    "/health",
    methods=["GET"],
    responses={
        503: {"description": "Service unavailable — gateway not ready"},
        500: {"description": "Internal server error"},
    },
)
async def health(request: Request):
    config: GatewayConfig = request.app.state.config
    return {"status": "ok", "upstream": str(config.upstream.url)}


@router.get(
    "/health/upstream",
    responses={
        503: {"description": "Upstream MCP server unreachable"},
    },
)
async def upstream_health(request: Request):
    """Readiness-style check: probe whether the configured upstream responds."""
    config: GatewayConfig = request.app.state.config
    client: httpx.AsyncClient = request.app.state.http_client
    upstream = str(config.upstream.url)

    try:
        response = await client.get(upstream, timeout=_UPSTREAM_PROBE_TIMEOUT)
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "upstream": upstream, "error": "timeout"},
        )
    except httpx.RequestError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "upstream": upstream,
                "error": "unreachable",
            },
        )

    return {
        "status": "ok",
        "upstream": upstream,
        "http_status": response.status_code,
    }
