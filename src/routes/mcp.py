from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from config import GatewayConfig
from services.audit import AuditService
from services.mcp import MCPService
from services.tools_policy import ToolsPolicyService

router = APIRouter()

MCP_PROXY_METHODS = ["GET", "POST", "DELETE"]


@router.api_route(
    "/mcp",
    methods=MCP_PROXY_METHODS,
    responses={
        502: {"description": "Bad gateway — upstream unreachable"},
        504: {"description": "Gateway timeout — upstream did not respond in time"},
    },
)
async def proxy_mcp(request: Request) -> Response:
    config: GatewayConfig = request.app.state.config
    client: httpx.AsyncClient = request.app.state.http_client
    audit_service: AuditService = request.app.state.audit_service

    tools_policy_service = ToolsPolicyService(config.policy)
    mcp_service = MCPService(
        client,
        str(config.upstream.url),
        tools_policy_service,
        audit_service,
    )
    result = await mcp_service.proxy(
        method=request.method,
        headers=request.headers,
        body=await request.body(),
    )

    if result.stream is not None:
        return StreamingResponse(
            result.stream,
            status_code=result.status_code,
            headers=result.headers,
            media_type="text/event-stream",
        )

    return Response(
        content=result.body,
        status_code=result.status_code,
        headers=result.headers,
    )
