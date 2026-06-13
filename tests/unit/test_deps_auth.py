from __future__ import annotations

import jwt
import pytest
from fastapi import HTTPException, Request
from starlette.applications import Starlette

from config import AuthConfig
from deps.auth import authenticate
from services.auth import AuthService

_TEST_SECRET = "test-secret-with-at-least-32-bytes"


def _request(auth_service: AuthService, authorization: str | None = None) -> Request:
    app = Starlette()
    app.state.auth_service = auth_service
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "app": app,
        }
    )


class TestAuthenticateDependency:
    """FastAPI auth dependency: optional when disabled, 401 when enabled and invalid."""

    @pytest.fixture
    def enabled_service(self) -> AuthService:
        return AuthService(AuthConfig(secret=_TEST_SECRET))

    @pytest.fixture
    def disabled_service(self) -> AuthService:
        return AuthService(AuthConfig(secret=None))

    @pytest.mark.anyio
    async def test_returns_none_when_auth_disabled(
        self, disabled_service: AuthService
    ) -> None:
        assert await authenticate(_request(disabled_service)) is None

    @pytest.mark.anyio
    async def test_returns_identity_for_valid_token(
        self, enabled_service: AuthService
    ) -> None:
        token = jwt.encode({"sub": "alice"}, _TEST_SECRET, algorithm="HS256")
        assert await authenticate(_request(enabled_service, f"Bearer {token}")) == "alice"

    @pytest.mark.anyio
    async def test_raises_401_when_token_missing(self, enabled_service: AuthService) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await authenticate(_request(enabled_service))

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.anyio
    async def test_raises_401_when_token_invalid(self, enabled_service: AuthService) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await authenticate(_request(enabled_service, "Bearer not-a-valid-jwt"))

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"
