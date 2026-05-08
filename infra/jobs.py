"""ARQ job-queue client (Phase 3).

Thin wrapper around ``arq.create_pool`` so any code in the API or bot
process can enqueue background work with a single call:

    from infra import jobs
    job = await jobs.enqueue("precompute_scorecards", account_id=7, days=7)
    print(job.job_id)

The pool is created once at startup (``init_jobs``) and torn down on
shutdown (``close_jobs``). If ARQ isn't installed or Redis is
unreachable, the helpers fail gracefully (``enqueue`` returns ``None``
and logs a warning) so a queue outage degrades to "background work
doesn't run" rather than "API endpoint 500s".

Worker side: see ``capabilities/jobs/worker.py``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Same env var as infra/cache.py — one Redis instance, multiple databases.
# We keep the queue on db=0 by default; override via ARQ_REDIS_URL if you
# want to isolate the queue from the cache (useful when scaling Redis).
_ARQ_REDIS_URL = os.getenv("ARQ_REDIS_URL", os.getenv("REDIS_URL", "redis://127.0.0.1:8002/0"))

_pool: Any = None  # arq.connections.ArqRedis when init_jobs() succeeds


def is_available() -> bool:
    """True when the ARQ pool is connected and can enqueue work."""
    return _pool is not None


async def init_jobs() -> bool:
    """Open the ARQ connection pool. Idempotent.

    Returns True if connected, False if ARQ isn't installed or Redis
    is unreachable. Callers should not crash on False — the queue is
    optional infrastructure (the API still works without it; only
    background-deferred features become no-ops).
    """
    global _pool
    if _pool is not None:
        return True
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
    except ImportError:
        logger.info("arq not installed — job queue disabled")
        return False
    try:
        settings = RedisSettings.from_dsn(_ARQ_REDIS_URL)
        _pool = await create_pool(settings)
        logger.info("ARQ pool connected: %s", _ARQ_REDIS_URL)
        return True
    except Exception as e:
        logger.warning("ARQ pool unavailable (%s) — enqueue will be a no-op", e)
        _pool = None
        return False


async def close_jobs() -> None:
    """Close the ARQ pool. Idempotent."""
    global _pool
    if _pool is None:
        return
    try:
        await _pool.aclose()  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("ARQ pool close error: %s", e)
    finally:
        _pool = None


async def enqueue(
    function: str,
    *args: Any,
    queue: str | None = None,
    job_id: str | None = None,
    defer_seconds: int | None = None,
    **kwargs: Any,
) -> Optional[Any]:
    """Enqueue a background job by function name.

    *function* must match a callable registered in
    ``capabilities/jobs/worker.WorkerSettings.functions``.

    Optional knobs:
      *queue*           ARQ queue name (defaults to "arq:queue").
      *job_id*          stable id for de-dup; second enqueue with same
                        id while the first is pending becomes a no-op.
                        Use this for cron-like "ensure runs" semantics.
      *defer_seconds*   schedule the job N seconds in the future
                        instead of running ASAP.

    Returns the ARQ Job object on success, ``None`` if the queue is
    unavailable. Callers should treat ``None`` as "background work
    skipped, log it, keep going" — the API contract is that enqueue
    never raises.
    """
    if _pool is None:
        # Lazy first-call init so callers don't need to remember the
        # init lifecycle. Safe under contention because aioredis pool
        # creation is itself async-safe.
        await init_jobs()
    if _pool is None:
        logger.debug("enqueue(%s) skipped — queue unavailable", function)
        return None
    try:
        kw: dict[str, Any] = {}
        if queue is not None:
            kw["_queue_name"] = queue
        if job_id is not None:
            kw["_job_id"] = job_id
        if defer_seconds is not None:
            kw["_defer_by"] = defer_seconds
        job = await _pool.enqueue_job(function, *args, **kw, **kwargs)  # type: ignore[union-attr]
        if job is not None:
            logger.debug("enqueued %s job_id=%s", function, getattr(job, "job_id", "?"))
        return job
    except Exception as e:
        logger.warning("enqueue(%s) failed: %s", function, e)
        return None


async def get_job_status(job_id: str) -> Optional[dict]:
    """Look up a job by id. Returns a JSON-serialisable status dict or
    ``None`` if the job doesn't exist (or queue is down).

    Status shape (subset of ARQ JobResult fields):
      {
        "job_id": str,
        "status": "deferred" | "queued" | "in_progress" | "complete" | "not_found",
        "enqueue_time": iso8601 str | None,
        "start_time": iso8601 str | None,
        "finish_time": iso8601 str | None,
        "success": bool | None,        # only when status == "complete"
        "result": <json>,              # only when status == "complete"
        "function": str | None,
      }
    """
    if _pool is None:
        await init_jobs()
    if _pool is None:
        return None
    try:
        from arq.jobs import Job, JobStatus
    except ImportError:
        return None
    try:
        job = Job(job_id, _pool)
        status = await job.status()
        info = await job.info()
        result_info = await job.result_info() if status == JobStatus.complete else None

        def _iso(dt) -> str | None:
            return dt.isoformat() if dt is not None else None

        out: dict[str, Any] = {
            "job_id":       job_id,
            "status":       status.value if hasattr(status, "value") else str(status),
            "enqueue_time": _iso(getattr(info, "enqueue_time", None)) if info else None,
            "function":     getattr(info, "function", None) if info else None,
        }
        if result_info is not None:
            out["start_time"]  = _iso(getattr(result_info, "start_time", None))
            out["finish_time"] = _iso(getattr(result_info, "finish_time", None))
            out["success"]     = getattr(result_info, "success", None)
            try:
                out["result"] = result_info.result
            except Exception:
                out["result"] = None
        return out
    except Exception as e:
        logger.debug("get_job_status(%s) failed: %s", job_id, e)
        return None
