from __future__ import annotations

from fastapi import APIRouter, Request

from config import GatewayConfig

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    config: GatewayConfig = request.app.state.config
    return {"status": "ok", "upstream": str(config.upstream.url)}
