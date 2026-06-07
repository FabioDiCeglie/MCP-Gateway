from __future__ import annotations

import os

from pydantic import BaseModel, Field, HttpUrl, ValidationError


class ListenConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class UpstreamConfig(BaseModel):
    url: HttpUrl


class GatewayConfig(BaseModel):
    listen: ListenConfig = Field(default_factory=ListenConfig)
    upstream: UpstreamConfig


def load_config() -> GatewayConfig:
    """Load and validate gateway config."""
    upstream_url = os.environ.get("GATEWAY_UPSTREAM_URL", "http://127.0.0.1:8000/mcp")

    try:
        return GatewayConfig(
            upstream=UpstreamConfig(url=upstream_url),
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid gateway configuration:\n{exc}") from exc
