from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

import httpx

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)


@dataclass
class ProxyResult:
    status_code: int
    headers: dict[str, str]
    body: bytes | None = None
    stream: AsyncIterator[bytes] | None = None


class MCPService:
    def __init__(self, client: httpx.AsyncClient, upstream_url: str) -> None:
        self._client = client
        self._upstream_url = upstream_url

    async def proxy(
        self,
        method: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> ProxyResult:
        upstream_request = self._client.build_request(
            method=method,
            url=self._upstream_url,
            headers=self._forward_request_headers(headers),
            content=body,
        )
        try:
            upstream_response = await self._client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            return ProxyResult(status_code=504, headers={}, body=b"Gateway timeout")
        except httpx.RequestError:
            return ProxyResult(status_code=502, headers={}, body=b"Bad gateway")

        response_headers = self._forward_response_headers(upstream_response.headers)

        if self._is_sse_response(upstream_response.headers):
            return ProxyResult(
                status_code=upstream_response.status_code,
                headers=response_headers,
                stream=self._stream_body(upstream_response),
            )

        content = await upstream_response.aread()
        await upstream_response.aclose()
        return ProxyResult(
            status_code=upstream_response.status_code,
            headers=response_headers,
            body=content,
        )

    @staticmethod
    async def _stream_body(response: httpx.Response) -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()

    @staticmethod
    def _forward_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }

    @staticmethod
    def _forward_response_headers(headers: httpx.Headers) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }

    @staticmethod
    def _is_sse_response(headers: httpx.Headers) -> bool:
        content_type = headers.get("content-type", "")
        return content_type.startswith("text/event-stream")
