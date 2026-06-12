from __future__ import annotations

from fastapi import APIRouter, Request

from config import GatewayConfig

router = APIRouter()


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
