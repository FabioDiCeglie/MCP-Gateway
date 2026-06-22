from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis

RATE_LIMIT_DENIED_CODE = -32001
RATE_LIMIT_CALLS = 10
RATE_LIMIT_WINDOW_SEC = 60
REDIS_KEY_PREFIX = "mcp-gateway:rate_limit:"
JSON_HEADERS = {"content-type": "application/json"}


@dataclass(frozen=True)
class RateLimitDenial:
    status_code: int
    headers: dict[str, str]
    body: bytes


class RateLimitService:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: redis.Redis | None = None

    async def open(self) -> None:
        self._client = redis.from_url(self._redis_url, decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def check(
        self,
        client_identity: str,
        *,
        request_id: Any,
        tool_name: str,
    ) -> RateLimitDenial | None:
        if self._client is None:
            return None

        redis_key = f"{REDIS_KEY_PREFIX}{client_identity}"
        count = await self._client.incr(redis_key)
        if count == 1:
            await self._client.expire(redis_key, RATE_LIMIT_WINDOW_SEC)

        if count <= RATE_LIMIT_CALLS:
            return None

        retry_after = await self._client.ttl(redis_key)
        if retry_after < 1:
            retry_after = RATE_LIMIT_WINDOW_SEC
        return self._deny(request_id, tool_name, client_identity, retry_after)

    @staticmethod
    def _deny(
        request_id: Any,
        tool_name: str,
        client_identity: str,
        retry_after: int,
    ) -> RateLimitDenial:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": RATE_LIMIT_DENIED_CODE,
                    "message": (
                        f"Rate limit exceeded for tool '{tool_name}' "
                        f"(client '{client_identity}')"
                    ),
                },
            }
        ).encode()
        headers = dict(JSON_HEADERS)
        headers["retry-after"] = str(retry_after)
        return RateLimitDenial(status_code=429, headers=headers, body=payload)
