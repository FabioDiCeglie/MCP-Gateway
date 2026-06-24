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


class AuthConfig(BaseModel):
    # HS256 shared secret. Unset = auth disabled.
    secret: str | None = None

    @property
    def enabled(self) -> bool:
        return self.secret is not None


class TracingConfig(BaseModel):
    # OTLP HTTP endpoint, e.g. http://jaeger:4318/v1/traces. Unset = tracing disabled.
    exporter_endpoint: str | None = None
    service_name: str = "mcp-gateway"

    @property
    def enabled(self) -> bool:
        return self.exporter_endpoint is not None


class RateLimitConfig(BaseModel):
    redis_url: str = "redis://127.0.0.1:6379/0"


class GatewayConfig(BaseModel):
    listen: ListenConfig = Field(default_factory=ListenConfig)
    upstream: UpstreamConfig
    policy: PolicyConfig
    audit: AuditConfig = Field(default_factory=AuditConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


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
        raise ValueError(
            f"Policy file must be a YAML mapping, got {type(raw).__name__}"
        )

    try:
        return PolicyConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid policy configuration in {POLICY_PATH}:\n{exc}"
        ) from exc


def _load_auth_config() -> AuthConfig:
    secret = os.environ.get("GATEWAY_JWT_SECRET", "").strip() or None
    return AuthConfig(secret=secret)


def _load_tracing_config() -> TracingConfig:
    endpoint = os.environ.get("GATEWAY_OTEL_EXPORTER_ENDPOINT", "").strip() or None
    service_name = os.environ.get("GATEWAY_OTEL_SERVICE_NAME", "mcp-gateway").strip()
    return TracingConfig(exporter_endpoint=endpoint, service_name=service_name)


def _load_rate_limit_config() -> RateLimitConfig:
    redis_url = os.environ.get("GATEWAY_REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    return RateLimitConfig(redis_url=redis_url)


def load_config() -> GatewayConfig:
    """Load and validate gateway config."""
    upstream_url = os.environ.get("GATEWAY_UPSTREAM_URL", "http://127.0.0.1:8000/mcp")
    audit_db_path = os.environ.get("GATEWAY_AUDIT_DB_PATH", "data/audit.db")

    try:
        return GatewayConfig(
            upstream=UpstreamConfig(url=upstream_url),
            policy=load_policy(),
            audit=AuditConfig(db_path=audit_db_path),
            auth=_load_auth_config(),
            tracing=_load_tracing_config(),
            rate_limit=_load_rate_limit_config(),
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid gateway configuration:\n{exc}") from exc
