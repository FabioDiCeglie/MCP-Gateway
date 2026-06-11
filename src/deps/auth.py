from __future__ import annotations

from fastapi import HTTPException, Request

from services.auth import AuthService


async def authenticate(request: Request) -> str | None:
    auth_service: AuthService = request.app.state.auth_service
    if not auth_service.enabled:
        return None

    identity = auth_service.authenticate(request.headers.get("authorization"))
    if identity is None:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity
