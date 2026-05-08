"""ARQ worker entry point.

Run with::

    arq capabilities.jobs.worker.WorkerSettings

The worker connects to the same Redis instance the API uses for caching
+ rate limiting; jobs published via ``infra.jobs.enqueue(...)`` are
picked up by any worker process.

Process model:
  * Each worker process handles up to ``max_jobs`` concurrent jobs in
    one asyncio event loop.
  * Workers are stateless across jobs — they share Redis + the per-tenant
    DB but no in-memory state survives between jobs.
  * Run multiple worker processes (one per CPU is fine) for horizontal
    throughput. They self-coordinate via ARQ's BLPOP semantics.

Cron jobs registered here are ARQ-native (separate from APScheduler).
APScheduler still owns the alerting + scheduler jobs that need a
single in-process loop with the Telegram client; ARQ owns work that's
purely about computation and can run anywhere.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Mirror ``infra.jobs._ARQ_REDIS_URL`` so worker + producer agree on Redis.
_ARQ_REDIS_URL = os.getenv("ARQ_REDIS_URL", os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0"))


async def on_startup(ctx):
    """Per-worker startup hook. Initialises platform infra so jobs can
    reach the DB + Redis cache the same way API handlers do."""
    import infra.startup as _startup
    if _startup.tenant_registry is None:
        await _startup.initialize()
    logger.info("ARQ worker startup complete")


async def on_shutdown(ctx):
    """Per-worker shutdown hook. Closes platform infra cleanly."""
    import infra.startup as _startup
    if _startup.tenant_registry is not None:
        await _startup.shutdown()
    logger.info("ARQ worker shutdown complete")


def _build_cron():
    """Lazy import so unit tests that import this module don't require
    arq itself to be installed."""
    from arq import cron

    from capabilities.jobs.functions import fanout_precompute_scorecards

    return [
        # Pre-warm the SWR scorecards cache once a day at 06:00 UTC so
        # the first dashboard request of the morning is a cache hit
        # rather than a cold-fan-out to Samsara.
        cron(
            fanout_precompute_scorecards,
            name="prewarm_scorecards_morning",
            hour=6, minute=0,
            run_at_startup=False,
        ),
    ]


def _build_settings():
    """Build WorkerSettings lazily so the module can be imported even
    when the ``arq`` package isn't installed (the Phase 3 dependency
    is optional infra — the API still works without it)."""
    from arq.connections import RedisSettings

    from capabilities.jobs.functions import (
        fanout_precompute_scorecards,
        ping,
        precompute_scorecards,
    )

    class WorkerSettings:
        """Settings consumed by the ``arq`` CLI."""

        # Redis connection
        redis_settings = RedisSettings.from_dsn(_ARQ_REDIS_URL)

        # Functions registered with this worker. ``infra.jobs.enqueue("name")``
        # looks up the function by name on the worker side.
        functions = [
            ping,
            precompute_scorecards,
            fanout_precompute_scorecards,
        ]

        # Cron jobs run by THIS worker (one of the workers wins the
        # BLPOP race; others stay idle until the cron fires again).
        cron_jobs = _build_cron()

        on_startup = on_startup
        on_shutdown = on_shutdown

        # Tunables
        max_jobs = int(os.getenv("ARQ_MAX_JOBS", "10"))
        # Default queue name — override per-call via _queue_name.
        queue_name = os.getenv("ARQ_QUEUE_NAME", "arq:queue")
        # Job timeout. Heaviest precompute is ~30s on cold cache; give
        # ample headroom so a slow Samsara call doesn't kill the job.
        job_timeout = int(os.getenv("ARQ_JOB_TIMEOUT", "300"))
        # Retry failed jobs with exponential backoff.
        max_tries = int(os.getenv("ARQ_MAX_TRIES", "3"))

    return WorkerSettings


# ARQ's CLI imports ``WorkerSettings`` from this module by name. We
# build it lazily on first attribute access so importing this module
# without arq installed (e.g. in tests) doesn't crash.

def __getattr__(name):
    if name == "WorkerSettings":
        return _build_settings()
    raise AttributeError(name)
