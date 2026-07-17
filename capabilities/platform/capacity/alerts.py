"""Capacity threshold alerts — Telegram to the platform operators.

Every 5 minutes the job reads the sampler's freshest minute rows and
compares 5-min averages against the ACT threshold (85%).  Alerting is
STATEFUL, not periodic: one message when a signal breaches, silence
while it stays breached, one recovery message when it clears — never a
message every 5 minutes.

  * State lives in Redis (``sysalert:active:<signal>``), the store all
    processes share.  Redis down → the job SKIPS entirely: without
    state it can't dedupe, and a 5-minute spam loop is worse than a
    late alert.
  * Hysteresis: breach at ≥ ACT (85%), clear only below CLEAR (80%) —
    a value flapping across 85.0 doesn't ping-pong messages.
  * Signals beyond the resource gauges: ingest stall (newest vehicle
    signal too old — the exact failure that once froze the whole fleet
    map for days), queue backlog, and the sampler itself going quiet
    (a capacity monitor whose own death is invisible isn't one).

Recipients: SYSTEM_OWNER_IDS (read at send time so operators added
without a restart still receive).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import ACT_PCT

logger = logging.getLogger("bot.scheduler")

# Hysteresis floor — a breached signal clears only below this.
CLEAR_PCT = 80.0
# Ingest stall: newest vehicle_state signal older than this (minutes).
# The Health page warns at 30/180; alerting sits between — late enough
# to skip transient Samsara hiccups, early enough to matter.
INGEST_STALL_MIN = 60
INGEST_CLEAR_MIN = 30
# arq backlog: pending jobs (normally ~0; a stuck worker piles hundreds).
QUEUE_BACKLOG = 100
QUEUE_CLEAR = 20
# Sampler quiet: no minute row for this long while history exists.
SAMPLER_STALL_MIN = 10

_CONSOLE_HINT = "→ system.4truck.us/capacity"


def _avg(rows: list[dict], col: str) -> float | None:
    vals = [r[col] for r in rows if r.get(col) is not None]
    return sum(vals) / len(vals) if vals else None


def _minutes_ago(ts_iso: str | None) -> float | None:
    if not ts_iso:
        return None
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except ValueError:
        return None


def evaluate(
    rows: list[dict],
    latest: dict | None,
    ingest_age_min: float | None,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Pure signal evaluation → ``{signal: {state, alert, recovery}}``.

    ``state`` ∈ breach / clear / hold (hysteresis zone: keep whatever
    state is currently active, say nothing).  Messages are prebuilt so
    the sender stays dumb.
    """
    now = now or datetime.now(timezone.utc)
    out: dict[str, dict] = {}

    def pct_signal(key: str, label: str, value: float | None):
        if value is None:
            return
        v = round(value)
        if value >= ACT_PCT:
            state = "breach"
        elif value < CLEAR_PCT:
            state = "clear"
        else:
            state = "hold"
        out[key] = {
            "state": state,
            "alert": (
                f"🔴 Capacity: {label} 5-min average {v}% (≥{ACT_PCT:.0f}%) — "
                f"upgrade now or shed load. {_CONSOLE_HINT}"
            ),
            "recovery": f"🟢 Capacity recovered: {label} back under {CLEAR_PCT:.0f}% (now {v}%).",
        }

    pct_signal("cpu", "CPU", _avg(rows, "cpu_pct"))
    pct_signal("mem", "memory", _avg(rows, "mem_pct"))
    # Disk is slow-moving — the latest point is the honest value.
    pct_signal("disk", "disk space", (latest or {}).get("disk_pct"))

    q = _avg(rows, "queue_depth")
    if q is not None:
        qi = int(q)
        state = "breach" if q >= QUEUE_BACKLOG else ("clear" if q < QUEUE_CLEAR else "hold")
        out["queue"] = {
            "state": state,
            "alert": (
                f"🔴 Capacity: job queue backed up — {qi} pending "
                f"(≥{QUEUE_BACKLOG}). A worker is likely stuck. {_CONSOLE_HINT}"
            ),
            "recovery": f"🟢 Capacity recovered: job queue drained (now {qi} pending).",
        }

    if ingest_age_min is not None:
        m = int(ingest_age_min)
        state = (
            "breach" if ingest_age_min >= INGEST_STALL_MIN
            else ("clear" if ingest_age_min < INGEST_CLEAR_MIN else "hold")
        )
        out["ingest"] = {
            "state": state,
            "alert": (
                f"🔴 Telemetry ingest stalled — newest vehicle signal is {m} min old "
                f"(≥{INGEST_STALL_MIN}m). Live map and alerts are flying blind. {_CONSOLE_HINT}"
            ),
            "recovery": f"🟢 Telemetry ingest recovered — newest signal {m} min old.",
        }

    # The watcher watches itself: history exists but nothing recent.
    if latest is not None:
        ts = latest.get("ts")
        age = _minutes_ago(f"{ts}:00") if ts else None
        if age is not None:
            state = (
                "breach" if age >= SAMPLER_STALL_MIN
                else ("clear" if age < 3 else "hold")
            )
            out["sampler"] = {
                "state": state,
                "alert": (
                    f"🔴 Capacity sampler quiet for {int(age)} min — the bot "
                    f"scheduler may be down; capacity history has a hole. {_CONSOLE_HINT}"
                ),
                "recovery": "🟢 Capacity sampler is writing again.",
            }

    return out


# ── Redis breach-state (tiny wrappers — tests monkeypatch these) ────

_ACTIVE_TTL = 7 * 86400   # a week-long breach still recovers correctly


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
        logger.warning("capacity alert had no recipients/bot: %s", text)
        return
    for tg_id in sorted(ids):
        try:
            await bot.send_message(chat_id=tg_id, text=text)
        except Exception as e:
            logger.warning("capacity alert to %s failed: %s", tg_id, e)


async def check_and_alert(db, app) -> list[str]:
    """One evaluation pass.  Returns the messages sent (for tests)."""
    import infra.cache as cache

    if not cache.is_available():
        # No shared state → can't dedupe → a message every 5 minutes.
        # Skip; Redis-down has its own visibility (Health page).
        logger.debug("capacity alerts skipped — redis unavailable")
        return []

    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M")
    rows = await db.get_system_metrics_minutes(since)
    latest = await db.get_system_metrics_latest()

    ingest_age: float | None = None
    try:
        cur = await db._db.execute(
            "SELECT MAX(captured_at) FROM vehicle_state WHERE captured_at <> ''"
        )
        r = await cur.fetchone()
        ingest_age = _minutes_ago(r[0] if r else None)
    except Exception as e:
        logger.debug("capacity alerts: ingest probe failed: %s", e)

    sent: list[str] = []
    for signal, spec in evaluate(rows, latest, ingest_age, now).items():
        active = await _is_active(signal)
        if spec["state"] == "breach" and not active:
            await _send_to_operators(app, spec["alert"])
            await _set_active(signal)
            sent.append(spec["alert"])
        elif spec["state"] == "clear" and active:
            await _send_to_operators(app, spec["recovery"])
            await _clear_active(signal)
            sent.append(spec["recovery"])
        # "hold" (hysteresis zone) or unchanged state: say nothing.
    return sent


async def job_capacity_alerts(app=None) -> None:
    """Scheduler entry — every 5 minutes."""
    import infra.platform as platform

    db = getattr(platform, "_db", None)
    if db is None:
        return
    try:
        await check_and_alert(db, app)
    except Exception:
        logger.exception("capacity alerts pass failed")
