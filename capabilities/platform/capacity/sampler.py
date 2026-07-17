"""The 60s capacity sampler — one scheduler job, every signal.

Runs in the bot process (host metrics are host-wide, so any resident
process can read them).  Every tick collects host + Postgres + Redis +
queue + request-rate probes and inserts ONE row into
``system_metrics_minute``; each probe is individually best-effort — a
failed probe lands as NULL, never sinks the row.

At the top of each hour it also folds the previous hour into
``system_metrics_hourly`` (avg + PEAK — capacity planning reads peaks)
and flushes the per-account request hash so a crash never loses more
than an hour of metering.

psutil rate counters (CPU %, disk busy, net bytes) are deltas between
calls — module state carries the previous tick's readings, so the
first tick after a restart reports NULL rates rather than garbage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("bot.scheduler")

# Previous-tick psutil counters (None until the second tick).
_prev: dict | None = None


def _host_probe() -> dict:
    """CPU / memory / disk / network snapshot via psutil (deltas where
    the metric is a rate).  Raises only on psutil import failure."""
    global _prev
    import os
    import time

    import psutil

    now = time.monotonic()
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    dio = psutil.disk_io_counters()
    net = psutil.net_io_counters()

    out: dict = {
        # cpu_percent(None) measures since the previous call in THIS
        # process — exactly the per-tick window we want (first call
        # returns 0.0; treated as a warm-up NULL below).
        "cpu_pct": psutil.cpu_percent(interval=None),
        "load1": round(os.getloadavg()[0], 2),
        "mem_pct": round(vm.percent, 1),
        "mem_used_mb": int((vm.total - vm.available) / (1024 * 1024)),
        "disk_pct": round(du.percent, 1),
        "disk_used_gb": round(du.used / (1024 ** 3), 1),
        "disk_busy_pct": None,
        "net_rx_kbps": None,
        "net_tx_kbps": None,
    }

    if _prev is not None and dio is not None and net is not None:
        dt = max(now - _prev["t"], 1.0)
        if _prev.get("disk_busy_ms") is not None and getattr(dio, "busy_time", None) is not None:
            busy_ms = dio.busy_time - _prev["disk_busy_ms"]
            out["disk_busy_pct"] = round(min(max(busy_ms / (dt * 1000) * 100, 0), 100), 1)
        out["net_rx_kbps"] = round(max(net.bytes_recv - _prev["net_rx"], 0) / dt / 1024, 1)
        out["net_tx_kbps"] = round(max(net.bytes_sent - _prev["net_tx"], 0) / dt / 1024, 1)
    else:
        out["cpu_pct"] = None  # warm-up tick — 0.0 would read as "idle"

    _prev = {
        "t": now,
        "disk_busy_ms": getattr(dio, "busy_time", None) if dio else None,
        "net_rx": net.bytes_recv if net else 0,
        "net_tx": net.bytes_sent if net else 0,
    }
    return out


async def _pg_probe(db) -> dict:
    out: dict = {"pg_connections": None, "pg_size_mb": None}
    try:
        cur = await db._db.execute("SELECT COUNT(*) FROM pg_stat_activity")
        row = await cur.fetchone()
        out["pg_connections"] = int(row[0]) if row else None
    except Exception as e:
        logger.debug("capacity: pg_stat_activity probe failed: %s", e)
    try:
        cur = await db._db.execute(
            "SELECT pg_database_size(current_database()) / 1048576"
        )
        row = await cur.fetchone()
        out["pg_size_mb"] = int(row[0]) if row else None
    except Exception as e:
        logger.debug("capacity: pg_database_size probe failed: %s", e)
    return out


async def _counts_probe(db) -> dict:
    out: dict = {"vehicles_active": None, "accounts_active": None}
    try:
        cur = await db._db.execute(
            "SELECT COUNT(*) FROM vehicles WHERE is_active = 1"
        )
        row = await cur.fetchone()
        out["vehicles_active"] = int(row[0]) if row else None
    except Exception as e:
        logger.debug("capacity: vehicles count probe failed: %s", e)
    try:
        cur = await db._db.execute(
            "SELECT COUNT(*) FROM accounts WHERE is_active = 1"
        )
        row = await cur.fetchone()
        out["accounts_active"] = int(row[0]) if row else None
    except Exception as e:
        logger.debug("capacity: accounts count probe failed: %s", e)
    return out


async def job_capacity_sample(_app=None) -> None:
    """Every 60s: collect one minute row; at :00 also roll the previous
    hour + flush today's per-account metering (crash resilience)."""
    import os

    import infra.cache as cache
    import infra.platform as platform

    from . import requests as metering

    db = getattr(platform, "_db", None)
    if db is None:
        return

    now = datetime.now(timezone.utc)
    sample: dict = {"ts": now.strftime("%Y-%m-%dT%H:%M")}

    try:
        sample.update(_host_probe())
    except Exception as e:  # psutil missing/broken — keep the row alive
        logger.warning("capacity: host probe unavailable: %s", e)

    sample.update(await _pg_probe(db))
    sample.update(await _counts_probe(db))
    sample["redis_mb"] = await cache.used_memory_mb()
    sample["queue_depth"] = await cache.llen(
        os.getenv("ARQ_QUEUE_NAME", "arq:queue")
    )
    sample["requests_min"] = await metering.requests_last_minute(now)

    try:
        await db.insert_system_metrics_minute(sample)
    except Exception:
        logger.exception("capacity: minute insert failed")
        return

    # Top of the hour: fold the finished hour + flush metering.  The
    # window is minutes 0-1 (not ==0) so a tick lost to coalescing
    # under load doesn't orphan the hour — the rollup is an idempotent
    # upsert, running twice is free.
    if now.minute <= 1:
        prev_hour = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00")
        try:
            await db.rollup_system_metrics_hour(prev_hour)
        except Exception:
            logger.exception("capacity: hourly rollup failed for %s", prev_hour)
        await _flush_account_usage(db, now.strftime("%Y-%m-%d"))


async def _flush_account_usage(db, day: str) -> None:
    """Persist the day's per-account request hash (additive — GREATEST
    upsert, so hourly + nightly flushes never regress each other)."""
    from . import requests as metering

    try:
        counts = await metering.account_counts(day)
        if counts:
            await db.upsert_account_usage_daily(day, counts)
    except Exception:
        logger.exception("capacity: account-usage flush failed for %s", day)


async def job_capacity_flush_yesterday(_app=None) -> None:
    """00:10 UTC: final flush of YESTERDAY's per-account metering (the
    hourly flushes cover today; this closes out the finished day after
    its Redis hash stops changing)."""
    import infra.platform as platform

    db = getattr(platform, "_db", None)
    if db is None:
        return
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    await _flush_account_usage(db, yesterday)
