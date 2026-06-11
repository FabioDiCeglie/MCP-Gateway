from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg

AuditOutcome = Literal["allowed", "denied"]

# For local development environment, use SQLite.
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('allowed', 'denied')),
    latency_ms INTEGER NOT NULL,
    request_id TEXT,
    client_identity TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_events_tool_name_timestamp
    ON audit_events (tool_name, timestamp);
"""

# For Docker environment or production, use PostgreSQL.
_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id SERIAL PRIMARY KEY,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('allowed', 'denied')),
    latency_ms INTEGER NOT NULL,
    request_id TEXT,
    client_identity TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_events_tool_name_timestamp
    ON audit_events (tool_name, timestamp);
"""


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    tool_name: str
    outcome: AuditOutcome
    latency_ms: int
    request_id: str | None
    client_identity: str | None = None


def _is_postgres(dsn: str) -> bool:
    return dsn.startswith("postgresql://") or dsn.startswith("postgres://")


class AuditService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._backend = "postgres" if _is_postgres(db_path) else "sqlite"
        self._conn: sqlite3.Connection | psycopg.Connection | None = None

    def open(self) -> None:
        if self._backend == "postgres":
            self._open_postgres()
        else:
            self._open_sqlite()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def record(
        self,
        *,
        tool_name: str,
        outcome: AuditOutcome,
        latency_ms: int,
        request_id: Any,
        client_identity: str | None = None,
    ) -> None:
        if self._conn is None:
            raise RuntimeError("AuditService is not open")

        event = AuditEvent(
            timestamp=datetime.now(UTC).isoformat(),
            tool_name=tool_name,
            outcome=outcome,
            latency_ms=latency_ms,
            request_id=_serialize_request_id(request_id),
            client_identity=client_identity,
        )
        params = (
            event.timestamp,
            event.tool_name,
            event.outcome,
            event.latency_ms,
            event.request_id,
            event.client_identity,
        )

        if self._backend == "postgres":
            assert isinstance(self._conn, psycopg.Connection)
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events (
                        timestamp, tool_name, outcome, latency_ms, request_id, client_identity
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    params,
                )
            self._conn.commit()
            return

        assert isinstance(self._conn, sqlite3.Connection)
        self._conn.execute(
            """
            INSERT INTO audit_events (
                timestamp, tool_name, outcome, latency_ms, request_id, client_identity
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        self._conn.commit()

    def record_tool_call(
        self,
        *,
        tool_name: str,
        request_id: Any,
        outcome: AuditOutcome,
        started_at: float | None = None,
        client_identity: str | None = None,
    ) -> None:
        latency_ms = 0
        if outcome == "allowed" and started_at is not None:
            latency_ms = max(0, int((time.perf_counter() - started_at) * 1000))

        self.record(
            tool_name=tool_name,
            outcome=outcome,
            latency_ms=latency_ms,
            request_id=request_id,
            client_identity=client_identity,
        )

    def _open_sqlite(self) -> None:
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SQLITE_SCHEMA)
        self._conn.commit()

    def _open_postgres(self) -> None:
        self._conn = psycopg.connect(self._db_path)
        statements = [
            statement.strip()
            for statement in _POSTGRES_SCHEMA.split(";")
            if statement.strip()
        ]
        with self._conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
        self._conn.commit()


def _serialize_request_id(request_id: Any) -> str | None:
    if request_id is None:
        return None
    if isinstance(request_id, str):
        return request_id
    return json.dumps(request_id)
