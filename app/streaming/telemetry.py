"""Метрики и spans без промптов, ответов, ключей или ID пользователей в labels."""

import logging
import time
from urllib.parse import urlsplit

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger("shluz.metrics")


class Telemetry:
    def __init__(self, settings):
        resource = Resource.create(
            {"service.name": "shluz", "service.version": settings.service_version}
        )
        readers = []
        self.traces = TracerProvider(resource=resource)
        if settings.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            endpoint = settings.otlp_endpoint.rstrip("/")
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("SHLUZ_OTLP_ENDPOINT must be an HTTP(S) collector base URL")
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=3),
                    export_interval_millis=30000,
                )
            )
            self.traces.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=3),
                )
            )
        self.meters = MeterProvider(resource=resource, metric_readers=readers)
        meter = self.meters.get_meter("shluz.gateway")
        self.requests = meter.create_counter("shluz.requests", unit="{request}")
        self.duration = meter.create_histogram("shluz.request.duration", unit="s")
        self.ttft = meter.create_histogram("shluz.request.first_token", unit="s")
        self.tokens = meter.create_counter("shluz.tokens", unit="{token}")
        self.cost = meter.create_counter("shluz.provider.cost", unit="USD")
        self.active = meter.create_up_down_counter("shluz.requests.active", unit="{request}")
        self.tracer = self.traces.get_tracer("shluz.gateway")

    def begin(self, provider, model, source, streamed, started=None):
        return Observation(self, provider, model, source, streamed, started)

    def close(self):
        self.meters.shutdown(timeout_millis=3000)
        self.traces.shutdown()


class Observation:
    def __init__(self, telemetry, provider, model, source, streamed, started):
        self.telemetry = telemetry
        self.start = started if started is not None else time.perf_counter()
        self.first_ms = None
        self.finished = False
        self.streamed = streamed
        self.attrs = {
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "shluz.channel": source,
            "shluz.stream": streamed,
        }
        self.span = telemetry.tracer.start_span("chat", attributes=self.attrs)
        telemetry.active.add(1, self.attrs)

    def first_content(self):
        if self.first_ms is None:
            self.first_ms = max(0, (time.perf_counter() - self.start) * 1000)

    def timing(self):
        return {
            "duration_ms": max(0, (time.perf_counter() - self.start) * 1000),
            "first_token_ms": self.first_ms,
            "streamed": self.streamed,
        }

    def finish(self, outcome, result=None):
        if self.finished:
            return
        self.finished = True
        attrs = {**self.attrs, "shluz.outcome": outcome}
        elapsed = self.timing()["duration_ms"] / 1000
        self.telemetry.active.add(-1, self.attrs)
        self.telemetry.requests.add(1, attrs)
        self.telemetry.duration.record(elapsed, attrs)
        if self.first_ms is not None:
            self.telemetry.ttft.record(self.first_ms / 1000, attrs)
        self.span.set_attribute("shluz.outcome", outcome)
        self.span.set_attribute("shluz.duration_seconds", elapsed)
        if self.first_ms is not None:
            self.span.set_attribute("shluz.first_token_seconds", self.first_ms / 1000)
        if result is not None:
            self.span.set_attribute("shluz.usage_known", result.usage_known)
            self.span.set_attribute("shluz.cost_known", result.cost_known)
            if result.usage_known:
                for kind, amount in [
                    ("input", result.prompt_tokens),
                    ("output", result.completion_tokens),
                ]:
                    self.telemetry.tokens.add(amount, {**attrs, "gen_ai.token.type": kind})
                    self.span.set_attribute(f"gen_ai.usage.{kind}_tokens", amount)
            if result.cost_known:
                self.telemetry.cost.add(float(result.cost_usd), attrs)
                self.span.set_attribute("shluz.provider.cost_usd", str(result.cost_usd))
        if outcome != "success":
            self.span.set_status(Status(StatusCode.ERROR, outcome))
        self.span.end()
