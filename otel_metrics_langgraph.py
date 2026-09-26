"""
STUB for integration testing only. Mirrors the public interface of the
user's real otel_metrics.py (tracer property, async trace_chat_call
context manager taking model + session_id, get_aggregated_stats) so
main.py's wiring can be exercised end-to-end in this sandbox without
their real AWS/X-Ray environment. This file is NOT part of the delivery.
"""
from contextlib import asynccontextmanager
from opentelemetry import trace


class StubTelemetry:
    def __init__(self):
        self._tracer = trace.get_tracer(__name__)
        self.last_recorded = None

    @property
    def tracer(self):
        return self._tracer

    def initialize(self):
        pass

    def start_background_monitoring(self):
        pass

    @asynccontextmanager
    async def trace_chat_call(self, model: str, session_id: str = "unknown"):
        holder = {"in": 0, "out": 0}
        yield holder
        self.last_recorded = {"model": model, "session_id": session_id, **holder}

    def get_aggregated_stats(self):
        return {"count": 1, "note": "stub stats"}


chatbot_telemetry = StubTelemetry()