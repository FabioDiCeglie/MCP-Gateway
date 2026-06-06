from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError


class ListenConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)


class UpstreamConfig(BaseModel):
    url: HttpUrl


class GatewayConfig(BaseModel):
    listen: ListenConfig = Field(default_factory=ListenConfig)
    upstream: UpstreamConfig


def load_config(path: Path) -> GatewayConfig:
    """Load and validate gateway config from a YAML file."""
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        raw: Any = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if raw is None:
        raise ValueError(f"Config file is empty: {path}")

    try:
        return GatewayConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid config in {path}:\n{exc}") from exc
