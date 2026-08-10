"""Machinery watchdog — watches the WORK, not the process.

Born 2026-08-10: the scheduler silently lost its lock for four nights
and every liveness monitor stayed green — the process polled Telegram
while the daily/weekly rollups quietly died (Mileage "Days" read 26 on
a 30-day window).  This module watches the OUTPUTS instead:

* tier freshness — is the newest day/hour/week row as new as the
  machinery promises?  Catches any cause: lock loss, crash, a broken
  aggregator.
* dead-man stamps — every executed scheduler job stamps Redis (see
  ``install_deadman_listener``); critical jobs are checked against a
  declared max age, so an individual job dying is caught even while
  the scheduler itself lives.

SYSTEM-side by design: platform machinery, operator audience (the
system bot, same channel as capacity alerts).  Messages carry ages and
counts only — never any tenant's data.  Alerts dedupe via Redis for
12h so a broken night pages once, not hourly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Freshness contracts: (label, SQL max-expression, max age).
_TIER_CHECKS = (
    ("day tier (vehicle_state_day)",
     "SELECT max(bucket_start) FROM vehicle_state_day",
     timedelta(hours=27)),
    ("hour tier (vehicle_state_hour)",
     "SELECT max(bucket_start) FROM vehicle_state_hour",
     timedelta(hours=3)),
    ("week tier (vehicle_state_week)",
     "SELECT max(bucket_start) FROM vehicle_state_week",
     timedelta(days=9)),
)

# Dead-man contracts: job id -> max silence before it counts as dead.
# 2x the cadence, roughly — enough slack for a restart, not a night.
DEADMAN_JOBS = {
    "warehouse_metrics_daily": timedelta(hours=26),
    "warehouse_metrics_weekly": timedelta(days=8),
    "warehouse_telemetry_hourly": timedelta(hours=3),
    "warehouse_state_snapshot": timedelta(minutes=10),
    "capacity_sample": timedelta(minutes=10),
    "data_retention": timedelta(hours=26),
    "account_lifecycle_housekeeping": timedelta(hours=26),
}

_DEDUPE_TTL_S = 12 * 3600


def _parse_ts(raw) -> datetime | None:
    try:
        s = str(raw)[:19]
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def install_deadman_listener(scheduler) -> None:
    """Stamp ``deadman:{job_id}`` in Redis after every successful job
    run.  Fire-and-forget; Redis being down never breaks a job."""
    from apscheduler.events import EVENT_JOB_EXECUTED

    import asyncio

    import infra.cache as cache

    def _on_executed(event) -> None:
        async def _stamp() -> None:
            try:
                if cache._pool is not None:
                    await cache._pool.set(
                        f"deadman:{event.job_id}",
                        datetime.now(timezone.utc).isoformat(),
                    )
            except Exception:
                pass
        try:
            asyncio.get_running_loop().create_task(_stamp())
        except RuntimeError:
            pass

    scheduler.add_listener(_on_executed, EVENT_JOB_EXECUTED)


async def collect_problems(db) -> list[str]:
    """Every currently-broken promise, as operator-ready sentences."""
    now = datetime.now(timezone.utc)
    problems: list[str] = []

    for label, sql, max_age in _TIER_CHECKS:
        try:
            cur = await db._db.execute(sql)
            row = await cur.fetchone()
            newest = _parse_ts(row[0] if row else None)
        except Exception as e:
            problems.append(f"{label}: freshness check failed ({e})")
            continue
        if newest is None:
            problems.append(f"{label}: no rows at all")
        elif now - newest > max_age:
            age_h = (now - newest).total_seconds() / 3600
            problems.append(
                f"{label}: newest row is {age_h:.0f}h old "
                f"(promise: {max_age.total_seconds() / 3600:.0f}h)")

    import infra.cache as cache
    if cache._pool is not None:
        for job_id, max_age in DEADMAN_JOBS.items():
            try:
                raw = await cache._pool.get(f"deadman:{job_id}")
            except Exception:
                continue
            if raw is None:
                continue  # no stamp yet (fresh deploy) — tiers cover it
            stamped = _parse_ts(
                raw.decode() if isinstance(raw, bytes) else raw)
            if stamped and now - stamped > max_age:
                age_h = (now - stamped).total_seconds() / 3600
                problems.append(
                    f"job {job_id}: last ran {age_h:.0f}h ago "
                    f"(promise: {max_age.total_seconds() / 3600:.0f}h)")
    return problems


async def job_machinery_watchdog(app=None) -> None:
    """Hourly: check every promise, page the operator on new breaks."""
    import infra.cache as cache

    from infra.platform import get_platform_db

    try:
        db = get_platform_db()
    except Exception:
        return
    if db is None:
        return
    problems = await collect_problems(db)
    if not problems:
        return
    fresh: list[str] = []
    for p in problems:
        key = f"watchdog:alerted:{p.split(':')[0]}"
        try:
            if cache.is_available():
                if await cache.exists(key):
                    continue
                await cache.setex_flag(key, _DEDUPE_TTL_S)
        except Exception:
            pass
        fresh.append(p)
    if not fresh:
        return
    from capabilities.platform.capacity.alerts import _send_to_operators
    text = "🩺 MACHINERY WATCHDOG\n" + "\n".join(f"• {p}" for p in fresh)
    logger.warning("machinery watchdog: %s", "; ".join(fresh))
    await _send_to_operators(app, text)
