"""Phase 6 — Prometheus metrics + OpenTelemetry tracing.

One module that owns ALL observability wiring. The two contracts:

  1. ``init_observability(app)``   — call from FastAPI ``create_api``
     to mount ``/metrics``, register prometheus middleware, wire OTel
     auto-instrumentation. Idempotent. No-op when deps aren't installed.

  2. Domain metrics surface as module-level objects:
       SAMSARA_LATENCY      — histogram, labelled (endpoint, status)
       SAMSARA_REQUESTS     — counter
       CACHE_RESULT         — counter, labelled (key_prefix, status)
       SINGLE_FLIGHT_COLLAPSE — counter
       SCORECARD_STAGE      — histogram per stage
       ARQ_JOB              — histogram per function name
       ARQ_QUEUE_DEPTH      — gauge

     Each is a no-op stub when prometheus_client isn't installed —
     callers can ``observability.SAMSARA_LATENCY.labels(...).observe(...)``
     without checking for None.

Design rules:
  - Optional dependency. The app must boot identically when
    prometheus-client / OTel libs are missing — this is required for
    minimal Docker images and for dev environments.
  - Cardinality control. Only label by VALUES that have <100 distinct
    states (status, error_class, endpoint name). NEVER by user_id,
    account_id, request URL.
  - Thin wrappers. Domain code should call simple helpers
    (``observability.record_samsara(endpoint, status, ms)``) not have
    Prometheus types in its critical path.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Feature flag — disable observability entirely without uninstalling deps
_OBS_ENABLED = os.getenv("OBSERVABILITY_ENABLED", "1").lower() in ("1", "true", "yes")
_OTEL_ENABLED = bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""))


# ── Stub when prometheus_client isn't installed ───────────────
#
# Stubs preserve the Prometheus client API shape so domain code can
# call `.labels(...).observe(...)` / `.inc()` without conditionals.

class _StubMetric:
    def labels(self, *args: Any, **kwargs: Any) -> "_StubMetric":
        return self

    def observe(self, *args: Any, **kwargs: Any) -> None:
        pass

    def inc(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set(self, *args: Any, **kwargs: Any) -> None:
        pass


_STUB = _StubMetric()


# ── Metric registry ───────────────────────────────────────────
#
# Module-level objects exported to domain code. Always assigned to
# something callable — real metrics when prom is installed, stubs
# otherwise. This means the domain code never branches.

SAMSARA_LATENCY: Any = _STUB
SAMSARA_REQUESTS: Any = _STUB
CACHE_RESULT: Any = _STUB
SINGLE_FLIGHT_COLLAPSE: Any = _STUB
SCORECARD_STAGE: Any = _STUB
ARQ_JOB: Any = _STUB
ARQ_QUEUE_DEPTH: Any = _STUB


def _build_metrics() -> bool:
    """Replace the module-level stubs with real Prometheus metrics.

    Returns True when prometheus_client is available and metrics were
    registered. Idempotent — second call is a no-op.
    """
    global SAMSARA_LATENCY, SAMSARA_REQUESTS, CACHE_RESULT
    global SINGLE_FLIGHT_COLLAPSE, SCORECARD_STAGE, ARQ_JOB, ARQ_QUEUE_DEPTH

    if not isinstance(SAMSARA_LATENCY, _StubMetric):
        return True  # already built

    try:
        from prometheus_client import Counter, Gauge, Histogram
    except ImportError:
        logger.info("prometheus-client not installed — metrics disabled")
        return False

    # External-API-call latency. Cardinality bounded by Samsara's API
    # surface (~30 endpoints) + 3 status values.
    SAMSARA_LATENCY = Histogram(
        "samsara_request_seconds",
        "Latency of Samsara API calls in seconds",
        labelnames=("endpoint", "status"),
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    )
    SAMSARA_REQUESTS = Counter(
        "samsara_requests_total",
        "Total Samsara API calls",
        labelnames=("endpoint", "status"),
    )

    # SWR cache effectiveness. ``key_prefix`` is the part before the
    # first colon (e.g. ``scorecards:composite:42:vehicle:7:_`` →
    # ``scorecards``) so cardinality stays bounded.
    CACHE_RESULT = Counter(
        "cache_get_or_compute_total",
        "SWR cache result by status",
        labelnames=("key_prefix", "status"),  # status: fresh|stale|miss|computed
    )
    SINGLE_FLIGHT_COLLAPSE = Counter(
        "cache_single_flight_collapsed_total",
        "Concurrent cold-cache requests collapsed onto one in-flight future",
        labelnames=("key_prefix",),
    )

    # Scoring service stage timings — already structured-logged in
    # capabilities/scoring/service.py. Promoting to a histogram so
    # Grafana can plot them next to /api/safety/scorecards/composite
    # request latency.
    SCORECARD_STAGE = Histogram(
        "scorecard_stage_seconds",
        "Per-stage latency inside evaluate_subjects",
        labelnames=("stage", "subject"),
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    )

    # ARQ job throughput.
    ARQ_JOB = Histogram(
        "arq_job_seconds",
        "ARQ job execution time",
        labelnames=("function", "status"),  # status: success|failed|retried
        buckets=(0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 300),
    )
    ARQ_QUEUE_DEPTH = Gauge(
        "arq_queue_depth",
        "Current pending job count",
        labelnames=("queue",),
    )

    logger.info("Prometheus metrics registered (7 instruments)")
    return True


# ── Helpers — domain code calls these ─────────────────────────

def record_samsara(endpoint: str, status: str, seconds: float) -> None:
    """Record one Samsara API call. ``status`` is one of "ok", "5xx", "timeout"."""
    SAMSARA_LATENCY.labels(endpoint=endpoint, status=status).observe(seconds)
    SAMSARA_REQUESTS.labels(endpoint=endpoint, status=status).inc()


def record_cache(key: str, status: str) -> None:
    """Record one SWR cache result. ``status`` ∈ {fresh, stale, miss, computed}."""
    prefix = key.split(":", 1)[0] if ":" in key else key
    CACHE_RESULT.labels(key_prefix=prefix, status=status).inc()


def record_single_flight_collapsed(key: str) -> None:
    prefix = key.split(":", 1)[0] if ":" in key else key
    SINGLE_FLIGHT_COLLAPSE.labels(key_prefix=prefix).inc()


def record_scorecard_stage(stage: str, subject: str, seconds: float) -> None:
    """Record one stage of evaluate_subjects (prepare_companies, signals_gather, ...)."""
    SCORECARD_STAGE.labels(stage=stage, subject=subject).observe(seconds)


def record_arq_job(function: str, status: str, seconds: float) -> None:
    """Record one completed ARQ job."""
    ARQ_JOB.labels(function=function, status=status).observe(seconds)


def time_block(timings: dict, name: str):
    """Context manager — record elapsed ms of a block into a dict.

    Usage::

        timings: dict[str, float] = {}
        with observability.time_block(timings, "fetch"):
            await fetch_stuff()
        logger.info("job=foo timings=%s", timings)

    Cheap (sub-microsecond), no Prometheus dependency. Use this for
    per-job stage timing in scheduled jobs (ingestor, parking, coaching,
    alerting) where dashboards aren't yet wired but a structured log
    line is enough to diagnose slow cycles.
    """
    import time as _time
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        t0 = _time.perf_counter()
        try:
            yield
        finally:
            timings[name] = round((_time.perf_counter() - t0) * 1000, 1)

    return _cm()


def set_arq_queue_depth(queue: str, depth: int) -> None:
    ARQ_QUEUE_DEPTH.labels(queue=queue).set(depth)


# ── /metrics endpoint + OTel wiring ───────────────────────────

def init_observability(app: Any) -> None:
    """Mount /metrics + register OTel auto-instrumentation.

    Called from ``interfaces.api.app.create_api``. Idempotent and
    failure-tolerant: any layer that can't initialise (missing dep,
    misconfigured exporter) is logged and skipped — the app still
    serves traffic.
    """
    if not _OBS_ENABLED:
        logger.info("Observability disabled via OBSERVABILITY_ENABLED=0")
        return

    built = _build_metrics()

    # ── Prometheus FastAPI instrumentator (per-route latency + RPS) ─
    if built:
        try:
            from prometheus_fastapi_instrumentator import Instrumentator

            Instrumentator(
                # Don't expose /metrics for /metrics calls themselves.
                excluded_handlers=["/metrics"],
                # Group identical routes (e.g. /api/admin/users/{id})
                # so Prometheus cardinality stays bounded.
                should_group_status_codes=True,
                should_ignore_untemplated=True,
                should_group_untemplated=True,
            ).instrument(app).expose(
                app, endpoint="/metrics", include_in_schema=False,
            )
            logger.info("Prometheus instrumentator + /metrics endpoint wired")
        except ImportError:
            logger.info("prometheus-fastapi-instrumentator not installed — /metrics disabled")
        except Exception as e:
            logger.warning("Prometheus instrumentator init failed: %s", e)

    # ── OpenTelemetry tracing ───────────────────────────────
    # Only enabled when an OTLP exporter endpoint is configured. Skipping
    # when unset means dev environments don't accidentally ship traces
    # to the wrong backend.
    if _OTEL_ENABLED:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            resource = Resource.create({
                "service.name": os.getenv("OTEL_SERVICE_NAME", "4truck-api"),
                "service.version": os.getenv("APP_VERSION", "1.0.0"),
                "deployment.environment": os.getenv("DEPLOYMENT_ENV", "production"),
            })
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter()  # reads OTEL_EXPORTER_OTLP_ENDPOINT from env
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)

            FastAPIInstrumentor.instrument_app(
                app,
                excluded_urls="/metrics,/api/health",
            )
            # Best-effort instrument the I/O libs we use heavily.
            for mod, fn in (
                ("opentelemetry.instrumentation.aiohttp_client", "AioHttpClientInstrumentor"),
                ("opentelemetry.instrumentation.asyncpg",        "AsyncPGInstrumentor"),
                ("opentelemetry.instrumentation.redis",          "RedisInstrumentor"),
            ):
                try:
                    m = __import__(mod, fromlist=[fn])
                    getattr(m, fn)().instrument()
                except Exception as e:
                    logger.debug("OTel %s skipped: %s", fn, e)

            logger.info(
                "OpenTelemetry tracing enabled (exporter=%s service=%s)",
                os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
                resource.attributes.get("service.name"),
            )
        except ImportError as e:
            logger.info("opentelemetry not installed (%s) — tracing disabled", e)
        except Exception as e:
            logger.warning("OpenTelemetry init failed: %s", e)
    else:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set — tracing disabled")


# ── ARQ queue-depth poller ────────────────────────────────────
#
# Prometheus needs gauge-style metrics polled periodically. The API
# kicks off a background task that snaps queue length every 15s. Stays
# a no-op when the queue or prometheus deps aren't installed.

async def poll_arq_queue_depth(interval_seconds: int = 15) -> None:
    """Background loop — record arq queue depth into the gauge.

    Cancelled by FastAPI lifespan teardown. Failures are caught and
    re-tried on the next tick rather than killing the loop.
    """
    import asyncio
    while True:
        try:
            from infra import jobs as _jobs
            if _jobs.is_available() and _jobs._pool is not None:  # type: ignore[attr-defined]
                # ARQ queues are Redis lists named "arq:queue" by default.
                queue_name = os.getenv("ARQ_QUEUE_NAME", "arq:queue")
                pool = _jobs._pool  # type: ignore[attr-defined]
                depth = await pool.llen(queue_name)  # type: ignore[union-attr]
                set_arq_queue_depth(queue_name, int(depth))
        except Exception as e:
            logger.debug("arq_queue_depth poll failed: %s", e)
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
