from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from services.audit import AuditService

_AUDIT_DB = "audit.db"


def _fetch_events(service: AuditService) -> list[tuple[str, str, int, str | None, str | None]]:
    conn = sqlite3.connect(service._db_path)
    try:
        return conn.execute(
            """
            SELECT tool_name, outcome, latency_ms, request_id, client_identity
            FROM audit_events
            ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()


class TestAuditService:
    """Audit logging: persist tool call outcomes to SQLite."""

    @pytest.fixture
    def service(self, tmp_path: Path) -> Iterator[AuditService]:
        audit = AuditService(str(tmp_path / _AUDIT_DB))
        audit.open()
        yield audit
        audit.close()

    # --- lifecycle ---

    def test_record_raises_when_not_open(self, tmp_path: Path) -> None:
        audit = AuditService(str(tmp_path / _AUDIT_DB))
        with pytest.raises(RuntimeError, match="AuditService is not open"):
            audit.record(
                tool_name="echo",
                outcome="allowed",
                latency_ms=0,
                request_id="req-1",
            )

    def test_open_creates_database_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / _AUDIT_DB
        audit = AuditService(str(db_path))
        audit.open()
        try:
            assert db_path.is_file()
        finally:
            audit.close()

    # --- record ---

    def test_record_persists_allowed_event(self, service: AuditService) -> None:
        service.record(
            tool_name="echo",
            outcome="allowed",
            latency_ms=42,
            request_id="req-1",
            client_identity="alice",
        )

        assert _fetch_events(service) == [("echo", "allowed", 42, "req-1", "alice")]

    def test_record_persists_denied_event(self, service: AuditService) -> None:
        service.record(
            tool_name="ping",
            outcome="denied",
            latency_ms=0,
            request_id="req-2",
        )

        assert _fetch_events(service) == [("ping", "denied", 0, "req-2", None)]

    def test_record_serializes_non_string_request_id(self, service: AuditService) -> None:
        service.record(
            tool_name="echo",
            outcome="allowed",
            latency_ms=0,
            request_id=7,
        )

        assert _fetch_events(service) == [("echo", "allowed", 0, "7", None)]

    def test_record_stores_none_request_id(self, service: AuditService) -> None:
        service.record(
            tool_name="echo",
            outcome="allowed",
            latency_ms=0,
            request_id=None,
        )

        assert _fetch_events(service) == [("echo", "allowed", 0, None, None)]

    # --- record_tool_call ---

    def test_record_tool_call_denied_has_zero_latency(self, service: AuditService) -> None:
        service.record_tool_call(
            tool_name="ping",
            request_id="req-3",
            outcome="denied",
            started_at=time.perf_counter(),
        )

        assert _fetch_events(service) == [("ping", "denied", 0, "req-3", None)]

    def test_record_tool_call_allowed_without_started_at_has_zero_latency(
        self, service: AuditService
    ) -> None:
        service.record_tool_call(
            tool_name="echo",
            request_id="req-4",
            outcome="allowed",
        )

        assert _fetch_events(service) == [("echo", "allowed", 0, "req-4", None)]

    def test_record_tool_call_allowed_computes_latency_from_started_at(
        self, service: AuditService
    ) -> None:
        started_at = time.perf_counter() - 0.05
        service.record_tool_call(
            tool_name="echo",
            request_id="req-5",
            outcome="allowed",
            started_at=started_at,
            client_identity="bob",
        )

        tool_name, outcome, latency_ms, request_id, client_identity = _fetch_events(service)[0]
        assert tool_name == "echo"
        assert outcome == "allowed"
        assert latency_ms >= 40
        assert request_id == "req-5"
        assert client_identity == "bob"
