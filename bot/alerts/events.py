"""Safety event alert checks — crash, harsh braking, speeding."""

from __future__ import annotations

from telegram.ext import Application

from bot.config import db, logger, get_client
from formatters import format_event_alert
import bot.redis_client as rcache

from bot.alerts.pipeline import (
    AlertSeverity, send_alert, is_vehicle_suppressed,
)


# Warmup flag — first cycle only populates known IDs without sending.
_events_warmup_done: dict[int, bool] = {}  # account_id → bool

# Events dedup — track which event IDs have already been alerted
_known_event_ids: dict[int, set[str]] = {}  # account_id → set of event IDs


def _event_severity(event: dict) -> AlertSeverity:
    """Map event type + g-force to severity tier."""
    etype = event.get("event_type", "")
    gf = event.get("g_force", 0.0)
    if etype == "crash":
        return AlertSeverity.CRITICAL
    if etype == "braking" and gf > 0.8:
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


async def _get_known_event_ids(account_id: int) -> set[str]:
    """Get already-alerted event IDs (Redis → in-memory)."""
    rkey = f"events_seen:{account_id}"
    if rcache.is_available():
        return await rcache.smembers(rkey)
    return _known_event_ids.get(account_id, set())


async def _add_known_event_ids(account_id: int, ids: set[str]):
    """Mark event IDs as alerted (Redis → in-memory)."""
    if not ids:
        return
    rkey = f"events_seen:{account_id}"
    if rcache.is_available():
        existing = await rcache.smembers(rkey)
        await rcache.sset(rkey, existing | ids, ttl=86400)
    else:
        _known_event_ids.setdefault(account_id, set()).update(ids)


async def check_events(app: Application):
    """Scheduled job: poll Samsara safety events and send alerts for new ones.

    Runs every 5 minutes. Short time window (1 day) to catch recent events.
    Deduplicates by event_id so each event is only alerted once.
    First cycle per account is a warm-up (populate known IDs, don't send).
    """
    try:
        accounts = await db.list_accounts()
        for account in accounts:
            companies = await db.get_account_companies(account.id)
            if not companies:
                continue

            try:
                samsara = await get_client(account.id)
            except Exception:
                continue

            await samsara.ensure_org_ids()

            try:
                events = await samsara.get_events(days=1)
            except Exception as e:
                logger.debug(f"Events check for account {account.id}: {e}")
                continue

            if not events:
                if account.id not in _events_warmup_done:
                    _events_warmup_done[account.id] = True
                continue

            known = await _get_known_event_ids(account.id)
            new_events = [e for e in events if e.get("event_id") not in known]

            # Mark all current event IDs as known
            all_ids = {e.get("event_id") for e in events if e.get("event_id")}
            await _add_known_event_ids(account.id, all_ids)

            # Warmup: first cycle populates known set without sending
            if not _events_warmup_done.get(account.id):
                _events_warmup_done[account.id] = True
                logger.info(
                    f"Events warmup for account {account.id}: "
                    f"populated {len(all_ids)} known event IDs"
                )
                continue

            if not new_events:
                continue

            # Get event-alert subscribers
            subscribers = await db.get_typed_alert_subscribers(
                account.id, "events"
            )
            if not subscribers:
                continue

            for event in new_events:
                try:
                    vname = event.get("vehicle_name", "?")

                    if await is_vehicle_suppressed(account.id, vname):
                        continue

                    severity = _event_severity(event)
                    alert_text = format_event_alert(event)
                    vid = event.get("vehicle_id", vname)
                    co = event.get("_org", "?")
                    vehicle_dict = {"id": vid, "name": vname, "_org": co}
                    eid = event.get("event_id", "")

                    await send_alert(
                        app,
                        account_id=account.id,
                        alert_type="events",
                        severity=severity,
                        vehicle=vehicle_dict,
                        alert_text=alert_text,
                        subscribers=subscribers,
                        co=co,
                        alert_key_detail=f"{event.get('event_type', '')}:{eid}",
                        video_url=event.get("video_url") or "",
                        event_id=eid,
                        event_time=event.get("time", ""),
                    )
                except Exception as e:
                    logger.warning(f"Event alert for {event.get('vehicle_name', '?')}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Events check error: {e}")
