from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from deps import authenticate
from services.mcp import MCPService
from services.tracing import TracingService

router = APIRouter()

MCP_PROXY_METHODS = ["GET", "POST", "DELETE"]


@router.api_route(
    "/mcp",
    methods=MCP_PROXY_METHODS,
    responses={
        401: {"description": "Unauthorized — missing or invalid JWT"},
        429: {"description": "Too many requests — rate limit exceeded"},
        502: {"description": "Bad gateway — upstream unreachable"},
        504: {"description": "Gateway timeout — upstream did not respond in time"},
    },
)
async def proxy_mcp(
    request: Request,
    client_identity: str | None = Depends(authenticate),
) -> Response:
    mcp_service: MCPService = request.app.state.mcp_service
    tracing_service: TracingService = request.app.state.tracing_service
    tracer = tracing_service.get_tracer()

    with tracer.start_as_current_span("gateway.request") as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.route", "/mcp")
        if client_identity is not None:
            span.set_attribute("client.identity", client_identity)

        result = await mcp_service.proxy(
            method=request.method,
            headers=request.headers,
            body=await request.body(),
            client_identity=client_identity,
        )
        span.set_attribute("http.status_code", result.status_code)

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
