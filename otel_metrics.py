"""
OpenTelemetry Metrics & Tracing Setup for AI Chatbot
Decoupled telemetry engine handling GenAI metrics, tracing spans, and host resources.
"""

import os
import time
import asyncio
import logging
import psutil
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from contextlib import contextmanager, asynccontextmanager

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
    ConsoleMetricExporter,
)
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as OTLPMetricExporterGRPC
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatbot_otel")


@dataclass
class ChatRecord:
    """Single chat transaction record for in-memory sliding window"""
    model: str
    latency_ms: float
    timestamp: float
    success: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ChatbotTelemetry:
    """
    Production-grade Singleton Telemetry Manager for the AI Chatbot.
    Handles Traces, GenAI Semantic Metrics, and System Resource Gauges.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.service_name = os.getenv("OTEL_SERVICE_NAME", "chatbot-service")
        self.environment = os.getenv("DEPLOYMENT_ENV", "development")
        self.otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        self.export_interval_ms = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "15000"))
        self.collect_interval_sec = int(os.getenv("OTEL_COLLECT_INTERVAL_SEC", "15"))

        self._tracer = None
        self._meter = None
        self._collection_task: Optional[asyncio.Task] = None
        self._started = False

        # GenAI Metric Instruments
        self._chat_histogram = None
        self._input_tokens_counter = None
        self._output_tokens_counter = None

        # Sliding window fallback buffer
        self._max_records = 500
        self._chat_records: List[ChatRecord] = []
        self._chat_stats = {
            "count": 0, "total_ms": 0, "min_ms": float('inf'), "max_ms": 0,
            "errors": 0, "total_input_tokens": 0, "total_output_tokens": 0
        }

        # Cached hardware values for observable gauges
        self._cpu_percent = 0.0
        self._memory_rss_kb = 0.0
        self._memory_percent = 0.0

    @classmethod
    def get_instance(cls) -> 'ChatbotTelemetry':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self) -> None:
        """Initialize global OpenTelemetry Providers, Tracers, and Meters"""
        # 1. Inject trace context into standard python logger
        LoggingInstrumentor().instrument(set_logging_format=True)

        resource = Resource.create({
            SERVICE_NAME: self.service_name,
            "deployment.environment": self.environment
        })

        # # 2. Setup Tracing Provider
        # trace_provider = TracerProvider(resource=resource)
        # try:
        #     span_exporter = OTLPSpanExporter(endpoint=self.otlp_endpoint, insecure=True)
        #     trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        # except Exception as e:
        #     logger.warning(f"Failed to bind OTLP trace exporter: {e}")
        # trace.set_tracer_provider(trace_provider)
        # self._tracer = trace.get_tracer(__name__)

        # # 3. Setup Metrics Provider (with fallback to console if network endpoint drops)
        # readers = []
        # try:
        #     metric_exporter = OTLPMetricExporterGRPC(endpoint=self.otlp_endpoint, insecure=True)
        #     readers.append(PeriodicExportingMetricReader(metric_exporter, export_interval_millis=self.export_interval_ms))
        # except Exception:
        #     readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=self.export_interval_ms))

        # meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        # metrics.set_meter_provider(meter_provider)
        # self._meter = metrics.get_meter(__name__)

        # self._register_instruments()
        # logger.info("OTel Chatbot Telemetry initialized successfully.")

        #######################################################################

        # 2. Setup Tracing Provider with OTLP exporter (to ADOT collector sidecar)
        trace_provider = TracerProvider(resource=resource)
        try:
            span_exporter = OTLPSpanExporter(endpoint=self.otlp_endpoint, insecure=True)
            trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            logger.info(f"OTLP trace exporter connected to {self.otlp_endpoint}")
        except Exception as e:
            logger.warning(f"Failed to bind OTLP trace exporter: {e}")
        trace.set_tracer_provider(trace_provider)
        self._tracer = trace.get_tracer(__name__)

        # 3. Setup Metrics Provider with OTLP exporter (fallback to console if it fails)
        readers = []
        try:
            metric_exporter = OTLPMetricExporterGRPC(endpoint=self.otlp_endpoint, insecure=True)
            readers.append(PeriodicExportingMetricReader(metric_exporter, export_interval_millis=self.export_interval_ms))
        except Exception:
            readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=self.export_interval_ms))

        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(meter_provider)
        self._meter = metrics.get_meter(__name__)

        self._register_instruments()
        logger.info(f"OTel Chatbot Telemetry initialized with OTLP exporter targeting {self.otlp_endpoint}")


        #######################################################################

        # # 2. Setup Tracing Provider with Console Span Exporter
        # from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
        # trace_provider = TracerProvider(resource=resource)
        # # SimpleSpanProcessor prints traces to console immediately as they finish
        # trace_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        # trace.set_tracer_provider(trace_provider)
        # self._tracer = trace.get_tracer(__name__)

        # # 3. Setup Metrics Provider with Console Metric Exporter
        # readers = [
        #     PeriodicExportingMetricReader(
        #         ConsoleMetricExporter(), 
        #         export_interval_millis=self.export_interval_ms
        #     )
        # ]

        # meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        # metrics.set_meter_provider(meter_provider)
        # self._meter = metrics.get_meter(__name__)

        # self._register_instruments()
        # logger.info("OTel Chatbot Telemetry initialized with Console Exporters (AWS-ready stdout mode).")


    def _register_instruments(self) -> None:
        """Register GenAI semantic conventions and system observable gauges"""
        if not self._meter:
            return

        # GenAI Metrics
        self._chat_histogram = self._meter.create_histogram(
            name="gen_ai.client.operation.duration",
            description="LLM chat completion operation duration",
            unit="ms"
        )
        self._input_tokens_counter = self._meter.create_counter(
            name="gen_ai.usage.input_tokens",
            description="Total prompt input tokens",
            unit="{tokens}"
        )
        self._output_tokens_counter = self._meter.create_counter(
            name="gen_ai.usage.output_tokens",
            description="Total generated output tokens",
            unit="{tokens}"
        )

        # Observable Hardware Gauges
        svc_attrs = {"service_name": self.service_name}
        self._meter.create_observable_gauge(
            name="system.cpu.utilization",
            description="Current system CPU utilization percentage",
            unit="%",
            callbacks=[lambda options: [metrics.Observation(self._cpu_percent, svc_attrs)]]
        )
        self._meter.create_observable_gauge(
            name="process.memory.rss",
            description="Process Resident Set Size in KB",
            unit="KB",
            callbacks=[lambda options: [metrics.Observation(self._memory_rss_kb, svc_attrs)]]
        )

    # @property
    # def tracer(self):
    #     return self._tracer


    @property
    def tracer(self):
        # If initialize() wasn't executed or failed, provide a working No-Op tracer instead of None
        if self._tracer is None:
            from opentelemetry import trace
            return trace.get_tracer(__name__)
        return self._tracer

    def record_chat_metrics(self, model: str, latency_ms: float, success: bool = True,
                            input_tokens: int = 0, output_tokens: int = 0, **metadata) -> None:
        """Record chat completion execution metrics and update sliding window cache"""
        attrs = {"gen_ai.system": "gcp.vertex_ai", "gen_ai.request.model": model, "success": str(success)}

        if self._chat_histogram:
            self._chat_histogram.record(latency_ms, attrs)
        if self._input_tokens_counter and input_tokens > 0:
            self._input_tokens_counter.add(input_tokens, attrs)
        if self._output_tokens_counter and output_tokens > 0:
            self._output_tokens_counter.add(output_tokens, attrs)

        # Update running stats
        self._chat_stats["count"] += 1
        self._chat_stats["total_ms"] += latency_ms
        self._chat_stats["min_ms"] = min(self._chat_stats["min_ms"], latency_ms)
        self._chat_stats["max_ms"] = max(self._chat_stats["max_ms"], latency_ms)
        if not success:
            self._chat_stats["errors"] += 1
        self._chat_stats["total_input_tokens"] += input_tokens
        self._chat_stats["total_output_tokens"] += output_tokens

        # Store in rolling buffer
        record = ChatRecord(model=model, latency_ms=latency_ms, timestamp=time.time(),
                            success=success, input_tokens=input_tokens, output_tokens=output_tokens, metadata=metadata)
        self._chat_records.append(record)
        if len(self._chat_records) > self._max_records:
            self._chat_records.pop(0)

    # @asynccontextmanager
    # async def trace_chat_call(self, model: str):
    #     """Async context manager to trace and measure LLM chatbot transactions"""
    #     if not self._tracer:
    #         yield None
    #         return

    #     with self._tracer.start_as_current_span("gen_ai.chat") as span:
    #         span.set_attribute("gen_ai.system", "openai")
    #         span.set_attribute("gen_ai.request.model", model)
            
    #         start_time = time.perf_counter()
    #         success = True
    #         tokens_in, tokens_out = 0, 0
            
    #         try:
    #             # Provide custom context object to write tokens back from main app
    #             ctx = {"set_tokens": lambda i, o: nonlocal_set(i, o)}
    #             # Helper container for closure assignment
    #             holder = {"in": 0, "out": 0}
    #             def nonlocal_set(i, o):
    #                 holder["in"] = i
    #                 holder["out"] = o
    #                 span.set_attribute("gen_ai.usage.input_tokens", i)
    #                 span.set_attribute("gen_ai.usage.output_tokens", o)

    #             yield holder
    #             tokens_in, tokens_out = holder["in"], holder["out"]
    #         except Exception as exc:
    #             success = False
    #             span.record_exception(exc)
    #             span.set_status(trace.StatusCode.ERROR, str(exc))
    #             raise
    #         finally:
    #             latency_ms = (time.perf_counter() - start_time) * 1000
    #             span.set_attribute("gen_ai.response.latency", latency_ms)
    #             self.record_chat_metrics(model, latency_ms, success, tokens_in, tokens_out)


    ###################################

    # @asynccontextmanager
    # async def trace_chat_call(self, model: str):
    #     """Async context manager to trace and measure LLM chatbot transactions"""
    #     if not self._tracer:
    #         yield None
    #         return

    #     with self._tracer.start_as_current_span("gen_ai.chat") as span:
    #         span.set_attribute("gen_ai.system", "openai")
    #         span.set_attribute("gen_ai.request.model", model)
            
    #         start_time = time.perf_counter()
    #         success = True
    #         holder = {"in": 0, "out": 0}
            
    #         try:
    #             yield holder
    #         except Exception as exc:
    #             success = False
    #             span.record_exception(exc)
    #             span.set_status(trace.StatusCode.ERROR, str(exc))
    #             raise
    #         finally:
    #             tokens_in, tokens_out = holder["in"], holder["out"]
    #             span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
    #             span.set_attribute("gen_ai.usage.output_tokens", tokens_out)

    #             latency_ms = (time.perf_counter() - start_time) * 1000
    #             span.set_attribute("gen_ai.response.latency", latency_ms)
    #             self.record_chat_metrics(model, latency_ms, success, tokens_in, tokens_out)


########################################################
    @asynccontextmanager
    async def trace_chat_call(self, model: str, session_id: str = "unknown"):
        """Async context manager to trace and measure LLM chatbot transactions"""
        if not self._tracer:
            yield None
            return

        with self._tracer.start_as_current_span("gen_ai.chat") as span:
            span.set_attribute("gen_ai.system", "openai")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("session_id", session_id)
            
            start_time = time.perf_counter()
            success = True
            holder = {"in": 0, "out": 0}
            
            try:
                yield holder
            except Exception as exc:
                success = False
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise
            finally:
                tokens_in, tokens_out = holder["in"], holder["out"]
                span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
                span.set_attribute("gen_ai.usage.output_tokens", tokens_out)

                latency_ms = (time.perf_counter() - start_time) * 1000
                span.set_attribute("gen_ai.response.latency", latency_ms)
                self.record_chat_metrics(model, latency_ms, success, tokens_in, tokens_out)

    def _collect_hardware_stats(self):
        """Scrape local hardware metrics using psutil"""
        try:
            self._cpu_percent = psutil.cpu_percent(interval=None)
            process = psutil.Process()
            mem_info = process.memory_info()
            self._memory_rss_kb = round(mem_info.rss / 1024, 2)
            self._memory_percent = round(process.memory_percent(), 2)
        except Exception:
            pass

    async def _hardware_monitoring_loop(self):
        while self._started:
            self._collect_hardware_stats()
            await asyncio.sleep(self.collect_interval_sec)

    def start_background_monitoring(self):
        """Start async system scraper task"""
        if self._started:
            return
        self._started = True
        try:
            loop = asyncio.get_running_loop()
            self._collection_task = loop.create_task(self._hardware_monitoring_loop())
        except RuntimeError:
            pass

    def get_aggregated_stats(self) -> Dict[str, Any]:
        """Expose clean statistics for diagnostic API endpoints"""
        stats = self._chat_stats.copy()
        if stats["count"] > 0:
            stats["avg_ms"] = round(stats["total_ms"] / stats["count"], 2)
            stats["min_ms"] = round(stats["min_ms"], 2) if stats["min_ms"] != float('inf') else 0
            stats["error_rate"] = round((stats["errors"] / stats["count"]) * 100, 2)
        else:
            stats["avg_ms"] = 0
            stats["min_ms"] = 0
            stats["error_rate"] = 0
        return stats



chatbot_telemetry = ChatbotTelemetry.get_instance()
