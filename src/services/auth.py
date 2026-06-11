from __future__ import annotations

import jwt

from config import AuthConfig


class AuthService:
    def __init__(self, config: AuthConfig) -> None:
        self._secret = config.secret

    @property
    def enabled(self) -> bool:
        return self._secret is not None

    def authenticate(self, authorization: str | None) -> str | None:
        token = _extract_bearer_token(authorization)
        if token is None or self._secret is None:
            return None
        return self._verify_token(token)

    def _verify_token(self, token: str) -> str | None:
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None

        identity = payload.get("sub")
        if not isinstance(identity, str) or not identity:
            return None
        return identity


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return None
    return credentials.strip()
