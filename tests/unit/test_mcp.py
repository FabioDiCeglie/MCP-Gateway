from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import fakeredis.aioredis
import httpx
import pytest

from config import PolicyConfig
from services.audit import AuditService
from services.mcp import MCPService
from services.rate_limit import RATE_LIMIT_DENIED_CODE, RateLimitService
from services.tools_policy import POLICY_DENIED_CODE, ToolsPolicyService

_AUDIT_DB = "audit.db"
_UPSTREAM_URL = "http://upstream.test/mcp"


def _tools_call_body(tool_name: str, request_id: int | str = 1) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
        }
    ).encode()


def _fetch_events(
    audit: AuditService,
) -> list[tuple[str, str, int, str | None, str | None]]:
    conn = sqlite3.connect(audit._db_path)
    try:
        return conn.execute(
            """
            SELECT tool_name, outcome, latency_ms, request_id, client_identity
            FROM audit_events
            ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()


class TestMCPServiceHelpers:
    """Header forwarding and SSE detection."""

    def test_forward_request_headers_strips_hop_by_hop(self) -> None:
        headers = {
            "Host": "localhost:8080",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }

        assert MCPService._forward_request_headers(headers) == {
            "Accept": "application/json",
        }

    def test_forward_response_headers_strips_hop_by_hop(self) -> None:
        headers = httpx.Headers(
            {
                "content-type": "application/json",
                "transfer-encoding": "chunked",
            }
        )

        assert MCPService._forward_response_headers(headers) == {
            "content-type": "application/json",
        }

    def test_is_sse_response_detects_event_stream(self) -> None:
        headers = httpx.Headers({"content-type": "text/event-stream; charset=utf-8"})
        assert MCPService._is_sse_response(headers) is True

    def test_is_sse_response_rejects_other_content_types(self) -> None:
        headers = httpx.Headers({"content-type": "application/json"})
        assert MCPService._is_sse_response(headers) is False


class TestMCPServiceProxy:
    """Proxy orchestration: policy, audit, and upstream forwarding."""

    @pytest.fixture
    def policy(self) -> ToolsPolicyService:
        return ToolsPolicyService(PolicyConfig(tools_allowed=["echo"]))

    @pytest.fixture
    def audit(self, tmp_path: Path) -> Iterator[AuditService]:
        service = AuditService(str(tmp_path / _AUDIT_DB))
        service.open()
        yield service
        service.close()

    @pytest.fixture
    def rate_limit(self) -> RateLimitService:
        service = RateLimitService("redis://unused")
        service._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        return service

    @pytest.mark.anyio
    async def test_rate_limit_allows_tool_call_under_limit(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        upstream_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request)
            return httpx.Response(200, content=b"upstream-ok")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy(
                "POST",
                {},
                _tools_call_body("echo"),
                client_identity="alice",
            )

        assert result.status_code == 200
        assert result.body == b"upstream-ok"
        assert len(upstream_calls) == 1

        tool_name, outcome, latency_ms, request_id, client_identity = _fetch_events(
            audit
        )[0]
        assert tool_name == "echo"
        assert outcome != "rate_limited"
        assert latency_ms >= 0
        assert request_id == "1"
        assert client_identity == "alice"

    @pytest.mark.anyio
    async def test_rate_limited_tool_call_returns_429_before_upstream(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("services.rate_limit.RATE_LIMIT_CALLS", 1)
        upstream_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request)
            return httpx.Response(200, content=b"upstream-ok")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            await service.proxy(
                "POST",
                {},
                _tools_call_body("echo"),
                client_identity="alice",
            )
            result = await service.proxy(
                "POST",
                {},
                _tools_call_body("echo", request_id=2),
                client_identity="alice",
            )

        assert len(upstream_calls) == 1
        assert result.status_code == 429
        assert result.body is not None
        error = json.loads(result.body)["error"]
        assert error["code"] == RATE_LIMIT_DENIED_CODE
        assert result.headers["retry-after"].isdigit()
        assert _fetch_events(audit)[-1][1] == "rate_limited"

    @pytest.mark.anyio
    async def test_denied_tool_call_returns_policy_error_without_upstream(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        upstream_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request)
            return httpx.Response(200, content=b"should not happen")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy(
                "POST",
                {},
                _tools_call_body("ping"),
                client_identity="alice",
            )

        assert upstream_calls == []
        assert result.body is not None
        error = json.loads(result.body)["error"]
        assert error["code"] == POLICY_DENIED_CODE
        assert _fetch_events(audit) == [("ping", "denied", 0, "1", "alice")]

    @pytest.mark.anyio
    async def test_allowed_tool_call_forwards_to_upstream_and_audits(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        upstream_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request)
            return httpx.Response(200, content=b"upstream-ok")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy(
                "POST",
                {"Host": "localhost", "Accept": "application/json"},
                _tools_call_body("echo"),
                client_identity="bob",
            )

        assert len(upstream_calls) == 1
        assert upstream_calls[0].method == "POST"
        assert str(upstream_calls[0].url) == _UPSTREAM_URL
        assert upstream_calls[0].headers.get("accept") == "application/json"

        assert result.status_code == 200
        assert result.body == b"upstream-ok"
        assert result.stream is None

        tool_name, outcome, latency_ms, request_id, client_identity = _fetch_events(
            audit
        )[0]
        assert tool_name == "echo"
        assert outcome == "allowed"
        assert latency_ms >= 0
        assert request_id == "1"
        assert client_identity == "bob"

    @pytest.mark.anyio
    async def test_non_tool_call_post_forwards_without_audit(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        upstream_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request)
            return httpx.Response(200, content=b"{}")

        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        ).encode()
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy("POST", {}, body)

        assert len(upstream_calls) == 1
        assert result.body == b"{}"
        assert _fetch_events(audit) == []

    @pytest.mark.anyio
    async def test_get_request_forwards_without_audit(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        upstream_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request)
            return httpx.Response(204)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy("GET", {}, b"")

        assert len(upstream_calls) == 1
        assert upstream_calls[0].method == "GET"
        assert result.status_code == 204
        assert _fetch_events(audit) == []

    @pytest.mark.anyio
    async def test_upstream_timeout_returns_504(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy("POST", {}, _tools_call_body("echo"))

        assert result.status_code == 504
        assert result.body == b"Gateway timeout"

    @pytest.mark.anyio
    async def test_upstream_error_returns_502(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy("POST", {}, _tools_call_body("echo"))

        assert result.status_code == 502
        assert result.body == b"Bad gateway"

    @pytest.mark.anyio
    async def test_sse_upstream_response_returns_stream(
        self,
        policy: ToolsPolicyService,
        audit: AuditService,
        rate_limit: RateLimitService,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "transfer-encoding": "chunked",
                },
                content=b"data: hello\n\n",
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            service = MCPService(client, _UPSTREAM_URL, policy, audit, rate_limit)
            result = await service.proxy("POST", {}, _tools_call_body("echo"))

        assert result.stream is not None
        assert result.body is None
        assert "transfer-encoding" not in {key.lower() for key in result.headers}

        chunks: list[bytes] = []
        async for chunk in result.stream:
            chunks.append(chunk)

        assert b"".join(chunks) == b"data: hello\n\n"
