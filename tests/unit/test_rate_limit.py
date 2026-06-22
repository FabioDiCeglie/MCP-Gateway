from __future__ import annotations

import json
from collections.abc import Iterator

import fakeredis.aioredis
import pytest

from services.rate_limit import (
    RATE_LIMIT_CALLS,
    RATE_LIMIT_DENIED_CODE,
    RATE_LIMIT_WINDOW_SEC,
    REDIS_KEY_PREFIX,
    RateLimitService,
)


@pytest.fixture
def service() -> Iterator[RateLimitService]:
    rate_limit = RateLimitService("redis://unused")
    rate_limit._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield rate_limit


class TestRateLimitService:
    @pytest.mark.anyio
    async def test_allows_calls_up_to_limit(self, service: RateLimitService) -> None:
        for _ in range(RATE_LIMIT_CALLS):
            denial = await service.check(
                "alice",
                request_id=1,
                tool_name="echo",
            )
            assert denial is None

    @pytest.mark.anyio
    async def test_denies_call_over_limit(
        self, service: RateLimitService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("services.rate_limit.RATE_LIMIT_CALLS", 2)

        assert (
            await service.check("alice", request_id=1, tool_name="echo") is None
        )
        assert (
            await service.check("alice", request_id=2, tool_name="echo") is None
        )

        denial = await service.check("alice", request_id=3, tool_name="echo")
        assert denial is not None
        assert denial.status_code == 429

    @pytest.mark.anyio
    async def test_deny_includes_retry_after_header(
        self, service: RateLimitService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("services.rate_limit.RATE_LIMIT_CALLS", 1)
        await service.check("alice", request_id=1, tool_name="echo")

        denial = await service.check("alice", request_id=2, tool_name="echo")
        assert denial is not None
        assert denial.headers["retry-after"].isdigit()
        assert int(denial.headers["retry-after"]) >= 1

    @pytest.mark.anyio
    async def test_deny_returns_json_rpc_error(
        self, service: RateLimitService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("services.rate_limit.RATE_LIMIT_CALLS", 1)
        await service.check("alice", request_id=1, tool_name="echo")

        denial = await service.check("alice", request_id=42, tool_name="echo")
        assert denial is not None

        payload = json.loads(denial.body)
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 42
        assert payload["error"]["code"] == RATE_LIMIT_DENIED_CODE
        assert "echo" in payload["error"]["message"]
        assert "alice" in payload["error"]["message"]

    @pytest.mark.anyio
    async def test_tracks_clients_separately(
        self, service: RateLimitService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("services.rate_limit.RATE_LIMIT_CALLS", 1)
        await service.check("alice", request_id=1, tool_name="echo")

        denial = await service.check("alice", request_id=2, tool_name="echo")
        assert denial is not None

        assert (
            await service.check("bob", request_id=3, tool_name="echo") is None
        )

    @pytest.mark.anyio
    async def test_sets_key_expiry_on_first_call(
        self, service: RateLimitService
    ) -> None:
        await service.check("alice", request_id=1, tool_name="echo")

        ttl = await service._client.ttl(f"{REDIS_KEY_PREFIX}alice")
        assert 0 < ttl <= RATE_LIMIT_WINDOW_SEC

    @pytest.mark.anyio
    async def test_check_returns_none_when_not_open(self) -> None:
        service = RateLimitService("redis://unused")

        denial = await service.check("alice", request_id=1, tool_name="echo")

        assert denial is None
