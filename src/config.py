from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError

POLICY_PATH = Path("policy.yaml")


class ListenConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class UpstreamConfig(BaseModel):
    url: HttpUrl


class PolicyConfig(BaseModel):
    tools_allowed: list[str] = Field(default_factory=list)


class AuditConfig(BaseModel):
    # File path for local SQLite, or postgres:// URL in Docker.
    db_path: str = "data/audit.db"


class GatewayConfig(BaseModel):
    listen: ListenConfig = Field(default_factory=ListenConfig)
    upstream: UpstreamConfig
    policy: PolicyConfig
    audit: AuditConfig = Field(default_factory=AuditConfig)


def load_policy() -> PolicyConfig:
    """Load and validate tool policy from policy.yaml."""
    if not POLICY_PATH.is_file():
        raise ValueError(f"Policy file not found: {POLICY_PATH}")

    try:
        raw = yaml.safe_load(POLICY_PATH.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid policy YAML in {POLICY_PATH}:\n{exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Policy file must be a YAML mapping, got {type(raw).__name__}")

    try:
        return PolicyConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid policy configuration in {POLICY_PATH}:\n{exc}") from exc


def load_config() -> GatewayConfig:
    """Load and validate gateway config."""
    upstream_url = os.environ.get("GATEWAY_UPSTREAM_URL", "http://127.0.0.1:8000/mcp")
    audit_db_path = os.environ.get("GATEWAY_AUDIT_DB_PATH", "data/audit.db")

    try:
        return GatewayConfig(
            upstream=UpstreamConfig(url=upstream_url),
            policy=load_policy(),
            audit=AuditConfig(db_path=audit_db_path),
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid gateway configuration:\n{exc}") from exc
