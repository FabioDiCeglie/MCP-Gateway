from __future__ import annotations

from config import TracingConfig
from services.tracing import TracingService


class TestTracingService:
    """OpenTelemetry bootstrap: no-op when tracing is disabled."""

    def test_start_is_noop_when_disabled(self) -> None:
        service = TracingService(TracingConfig())
        service.start()
        assert service._provider is None
