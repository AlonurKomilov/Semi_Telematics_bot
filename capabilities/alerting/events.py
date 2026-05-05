"""Safety event alert checks — crash, harsh braking, speeding."""

from __future__ import annotations

import logging

from telegram.ext import Application

import infra.cache as rcache
from capabilities.alerting.pipeline import (
    AlertSeverity, send_alert, is_vehicle_suppressed,
)
from capabilities.formatting import format_event_alert
from infra.bot_registry import get_app_for_account
from infra.context import set_tenant_display
from infra.isolation import run_account_job
from infra.services import get_client, get_platform_db, get_tenant_db

logger = logging.getLogger("bot")


# Warmup flag — first cycle only populates known IDs without sending.
_events_warmup_done: dict[int, bool] = {}  # account_id → bool

# Events dedup — track which event IDs have already been alerted
_known_event_ids: dict[int, set[str]] = {}  # account_id → set of event IDs


def _event_severity(event: dict) -> AlertSeverity:
    """Map event type + g-force to AlertSeverity for alert triage.

    Note: capabilities/events/severity.py is the SSOT for API-layer severity
    labels ("severe"/"moderate"/etc.).  This function maps those same signals
    to the alerting pipeline's three-tier enum (CRITICAL/WARNING/INFO).
    """
    etype = event.get("event_type", "")
    gf = event.get("g_force", 0.0)
    if etype == "crash":
        return AlertSeverity.CRITICAL
    if etype == "braking" and gf > 0.8:
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


async def _get_known_event_ids(account_id: int) -> set[str]:
    """Get already-alerted event IDs (Redis → in-memory)."""
    rkey = f"t:{account_id}:events_seen"
    if rcache.is_available():
        return await rcache.smembers(rkey)
    return _known_event_ids.get(account_id, set())


async def _add_known_event_ids(account_id: int, ids: set[str]):
    """Mark event IDs as alerted (Redis → in-memory)."""
    if not ids:
        return
    rkey = f"t:{account_id}:events_seen"
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
        accounts = await get_platform_db().list_accounts()
        for account in accounts:
            bot_app = get_app_for_account(account.id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping events check", account.id)
                continue
            await run_account_job(
                _check_events_account(bot_app, account),
                account_id=account.id,
                job_name="events_check",
            )

    except Exception as e:
        logger.error(f"Events check error: {e}")


async def _check_events_account(bot_app: Application, account):
    """Process safety event alerts for a single account."""
    account_id = account.id
    tenant = await get_tenant_db(account_id)
    companies = await tenant.get_account_companies(account_id)
    if not companies:
        return

    samsara = await get_client(account_id)
    await samsara.ensure_org_ids()
    display = {co.code: co.display_name or co.code for co in companies}
    set_tenant_display(display, samsara.org_ids)

    # Warehouse-first read.  ``warehouse_reader.get_safety_events`` returns
    # rows with the live-shape dict wrapped in ``raw`` (see ingestor
    # ``_safety_event_to_log_row``); when the flag is off it falls back
    # to the live ``samsara.get_events`` call we used to have here.
    from capabilities.telemetry import warehouse_reader as _wh

    async def _live():
        return await samsara.get_events(days=1)

    wh_rows = await _wh.get_safety_events(
        account_id, days=1, samsara_fallback=_live,
    )
    # Unwrap warehouse rows back to live shape so downstream code that
    # reads ``event_id`` / ``vehicle_name`` / ``time`` keeps working.
    events = [(r.get("raw") or r) for r in wh_rows]

    if not events:
        if account_id not in _events_warmup_done:
            _events_warmup_done[account_id] = True
        return

    known = await _get_known_event_ids(account_id)
    new_events = [e for e in events if e.get("event_id") not in known]

    # Mark all current event IDs as known
    all_ids = {e.get("event_id") for e in events if e.get("event_id")}
    await _add_known_event_ids(account_id, all_ids)

    # Warmup: first cycle populates known set without sending
    if not _events_warmup_done.get(account_id):
        _events_warmup_done[account_id] = True
        logger.info(
            f"Events warmup for account {account_id}: "
            f"populated {len(all_ids)} known event IDs"
        )
        return

    if not new_events:
        return

    # Get event-alert subscribers
    subscribers = await get_platform_db().get_typed_alert_subscribers(
        account_id, "events"
    )
    if not subscribers:
        return

    for event in new_events:
        try:
            vname = event.get("vehicle_name", "?")

            if await is_vehicle_suppressed(account_id, vname):
                continue

            severity = _event_severity(event)
            alert_text = format_event_alert(event)
            vid = event.get("vehicle_id", vname)
            co = event.get("_org", "?")
            vehicle_dict = {"id": vid, "name": vname, "_org": co}
            eid = event.get("event_id", "")

            await send_alert(
                bot_app,
                account_id=account_id,
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
                bot_app=bot_app,
            )
        except Exception as e:
            logger.warning(f"Event alert for {event.get('vehicle_name', '?')}: {e}")
            continue
