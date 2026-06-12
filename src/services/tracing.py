from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from config import TracingConfig


class TracingService:
    def __init__(self, config: TracingConfig) -> None:
        self._config = config
        self._provider: TracerProvider | None = None

    def start(self) -> None:
        if not self._config.enabled:
            return

        resource = Resource.create({"service.name": self._config.service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=self._config.exporter_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        self._provider = provider

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()
            self._provider = None

    def get_tracer(self) -> trace.Tracer:
        return trace.get_tracer(self._config.service_name)
