"""Ingest watchdog — a declared dataset cannot die quietly.

Every declaration carries a freshness SLA and whether rows are
expected; every run lands in the ledger; every row carries the
provider's ``source_ts``.  This turns those three into two questions
asked of each dataset, uniformly, by machinery that knows nothing
about what any of them contain:

  * **silent** — the job ran and wrote NOTHING, repeatedly, for a
    dataset that expects rows.  This is the engine-state shape: the
    machinery healthy, the data absent.
  * **stale** — rows exist but the newest world-time they carry is
    older than the dataset's SLA.  This is the frozen-odometer shape:
    the writes flowing, the world not moving.

Alerting is STATEFUL, mirroring the capacity alerts: one message on
breach, silence while it persists, one on recovery.  State lives in
Redis; with Redis down the pass SKIPS rather than spamming — a
5-minute loop is worse than a late alert.

Deliberately quiet in two cases.  A dataset whose ``source_ts`` is
NULL everywhere is "age unknown", not stale: cache and windowed-
aggregate tables are honestly ageless and would otherwise alarm
forever.  And an account with no rows at all has nothing to be silent
about — a customer who never connected the provider is not an
incident.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..staleness import data_age_minutes
from .registry import IngestDataset, all_datasets

logger = logging.getLogger("bot.scheduler")

# Consecutive zero-row DAYS before a rows-expecting dataset is called
# silent.  Days, not runs: a single empty tick is normal (nothing
# changed), a whole day of them is not.
SILENT_DAYS = 2

# How far above its SLA a dataset's data must drift before we speak,
# and how far back below to call it recovered — hysteresis, so a feed
# hovering at the boundary does not ping-pong messages.
STALE_FACTOR = 1.0
CLEAR_FACTOR = 0.5

_ACTIVE_TTL = 7 * 86400
_CONSOLE_HINT = "→ system.4truck.us/capacity"


def _signal(dataset_key: str, kind: str) -> str:
    return f"ingest:{dataset_key}:{kind}"


async def _is_active(signal: str) -> bool:
    import infra.cache as cache
    return await cache.exists(f"sysalert:active:{signal}")


async def _set_active(signal: str) -> None:
    import infra.cache as cache
    await cache.setex_flag(f"sysalert:active:{signal}", _ACTIVE_TTL)


async def _clear_active(signal: str) -> None:
    import infra.cache as cache
    await cache.delete(f"sysalert:active:{signal}")


async def _send_to_operators(app, text: str) -> None:
    from capabilities.permissions import roles
    ids = getattr(roles, "SYSTEM_OWNER_IDS", set()) or set()
    bot = getattr(app, "bot", None)
    if bot is None or not ids:
        logger.warning("ingest watchdog had no recipients/bot: %s", text)
        return
    for tg_id in sorted(ids):
        try:
            await bot.send_message(chat_id=tg_id, text=text)
        except Exception as e:
            logger.warning("ingest alert to %s failed: %s", tg_id, e)


def evaluate_dataset(
    dataset: IngestDataset,
    *,
    zero_row_days: int,
    newest_source_ts: Any,
    has_any_rows: bool,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Pure evaluation for one dataset → ``{kind: {state, message}}``.

    ``state`` ∈ breach / clear / hold, the same vocabulary the capacity
    alerts use, so the sender stays dumb.
    """
    out: dict[str, dict] = {}

    if dataset.expect_rows:
        if zero_row_days >= SILENT_DAYS:
            state = "breach"
        elif zero_row_days == 0:
            state = "clear"
        else:
            state = "hold"
        out["silent"] = {
            "state": state,
            "message": (
                f"🕳 Ingest silent — {dataset.key} has written no rows for "
                f"{zero_row_days} day(s) while its job kept running.\n"
                f"Tables: {', '.join(dataset.tables)}\n{_CONSOLE_HINT}"
            ),
            "recovery": f"✅ Ingest recovered — {dataset.key} is writing rows again.",
        }

    # Age is only meaningful once the dataset has rows AND those rows
    # carry a world-time.  NULL everywhere means "ageless by nature"
    # (caches, windowed aggregates), not "infinitely stale".
    age = data_age_minutes(newest_source_ts, now=now)
    if has_any_rows and age is not None:
        sla = dataset.freshness_sla_min
        if age > sla * STALE_FACTOR:
            state = "breach"
        elif age <= sla * CLEAR_FACTOR:
            state = "clear"
        else:
            state = "hold"
        hours = age / 60.0
        pretty = f"{age:.0f} min" if age < 120 else f"{hours:.1f} h"
        out["stale"] = {
            "state": state,
            "message": (
                f"🧊 Ingest stale — {dataset.key}'s newest reading is "
                f"{pretty} old (SLA {sla} min).  The writes are flowing; "
                f"the world-time is not moving.\n{_CONSOLE_HINT}"
            ),
            "recovery": f"✅ Ingest fresh again — {dataset.key} is current.",
        }
    return out


async def _dataset_facts(db, dataset: IngestDataset) -> dict[str, Any]:
    """Ledger + data probes for one dataset, platform-wide."""
    today = datetime.now(timezone.utc).date()
    days = [
        (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(SILENT_DAYS + 1)
    ]
    placeholders = ", ".join("?" for _ in days)
    cur = await db._db.execute(
        f"SELECT day, SUM(rows_sum) FROM ingest_runs "
        f"WHERE dataset_key = ? AND day IN ({placeholders}) "
        f"GROUP BY day",
        (dataset.key, *days),
    )
    by_day = {str(r[0]): int(r[1] or 0) for r in await cur.fetchall()}

    # Count only days the ledger actually saw a run — a day with no
    # ledger row is a day the machinery did not run, which is the
    # scheduler's problem, not the dataset's.
    zero_days = 0
    for day in days[1:]:            # skip today: still in progress
        if day not in by_day:
            break
        if by_day[day] > 0:
            break
        zero_days += 1

    table = dataset.tables[0]
    newest = None
    has_rows = False
    try:
        cur = await db._db.execute(
            f"SELECT MAX(source_ts), COUNT(*) FROM {table}"  # noqa: S608 - table from a code-declared registry
        )
        row = await cur.fetchone()
        newest = row[0] if row else None
        has_rows = bool(row[1]) if row else False
    except Exception as e:
        logger.debug("watchdog probe failed for %s: %s", dataset.key, e)
    return {
        "zero_row_days": zero_days,
        "newest_source_ts": newest,
        "has_any_rows": has_rows,
    }


async def check_and_alert(db, app) -> list[str]:
    """One evaluation pass across every declared dataset."""
    import infra.cache as cache

    if not cache.is_available():
        # No shared state → no dedupe → a message every five minutes.
        logger.debug("ingest watchdog skipped — redis unavailable")
        return []

    from . import discover
    discover()

    sent: list[str] = []
    for dataset in all_datasets():
        try:
            facts = await _dataset_facts(db, dataset)
        except Exception:
            logger.exception("watchdog facts failed for %s", dataset.key)
            continue
        for kind, spec in evaluate_dataset(dataset, **facts).items():
            signal = _signal(dataset.key, kind)
            active = await _is_active(signal)
            if spec["state"] == "breach" and not active:
                await _send_to_operators(app, spec["message"])
                await _set_active(signal)
                sent.append(spec["message"])
            elif spec["state"] == "clear" and active:
                await _send_to_operators(app, spec["recovery"])
                await _clear_active(signal)
                sent.append(spec["recovery"])
    return sent


async def job_ingest_watchdog(app=None) -> None:
    """Scheduler entry point — every 5 minutes, platform-wide."""
    from infra.platform import get_platform_db

    try:
        await check_and_alert(get_platform_db(), app)
    except Exception:
        logger.exception("ingest watchdog pass failed")
