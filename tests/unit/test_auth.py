from __future__ import annotations

import jwt
import pytest

from config import AuthConfig
from services.auth import AuthService

_TEST_SECRET = "test-secret-with-at-least-32-bytes"


class TestAuthService:
    """JWT bearer auth: verify HS256 tokens and return the subject identity."""

    @pytest.fixture
    def service(self) -> AuthService:
        return AuthService(AuthConfig(secret=_TEST_SECRET))

    @pytest.fixture
    def disabled_service(self) -> AuthService:
        return AuthService(AuthConfig(secret=None))

    # --- enabled flag ---

    def test_enabled_when_secret_configured(self, service: AuthService) -> None:
        assert service.enabled is True

    def test_disabled_when_no_secret(self, disabled_service: AuthService) -> None:
        assert disabled_service.enabled is False

    # --- authenticate: auth disabled ---

    def test_authenticate_returns_none_when_auth_disabled(
        self, disabled_service: AuthService
    ) -> None:
        token = jwt.encode({"sub": "alice"}, _TEST_SECRET, algorithm="HS256")
        assert disabled_service.authenticate(f"Bearer {token}") is None

    # --- authenticate: missing or malformed Authorization header ---

    def test_authenticate_returns_none_for_missing_header(
        self, service: AuthService
    ) -> None:
        assert service.authenticate(None) is None

    def test_authenticate_returns_none_for_non_bearer_scheme(
        self, service: AuthService
    ) -> None:
        token = jwt.encode({"sub": "alice"}, _TEST_SECRET, algorithm="HS256")
        assert service.authenticate(f"Basic {token}") is None

    def test_authenticate_returns_none_for_empty_bearer_credentials(
        self, service: AuthService
    ) -> None:
        assert service.authenticate("Bearer ") is None

    # --- authenticate: token verification ---

    def test_authenticate_returns_identity_for_valid_token(
        self, service: AuthService
    ) -> None:
        token = jwt.encode({"sub": "alice"}, _TEST_SECRET, algorithm="HS256")
        assert service.authenticate(f"Bearer {token}") == "alice"

    def test_authenticate_returns_none_for_invalid_token(
        self, service: AuthService
    ) -> None:
        assert service.authenticate("Bearer not-a-valid-jwt") is None

    def test_authenticate_returns_none_for_wrong_secret(
        self, service: AuthService
    ) -> None:
        token = jwt.encode(
            {"sub": "alice"}, "other-secret-with-32-byte-minimum", algorithm="HS256"
        )
        assert service.authenticate(f"Bearer {token}") is None

    def test_authenticate_returns_none_for_token_without_sub(
        self, service: AuthService
    ) -> None:
        token = jwt.encode({"role": "admin"}, _TEST_SECRET, algorithm="HS256")
        assert service.authenticate(f"Bearer {token}") is None

    def test_authenticate_returns_none_for_token_with_empty_sub(
        self, service: AuthService
    ) -> None:
        token = jwt.encode({"sub": ""}, _TEST_SECRET, algorithm="HS256")
        assert service.authenticate(f"Bearer {token}") is None
