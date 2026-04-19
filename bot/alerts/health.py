"""Vehicle health alert checks — oil pressure, coolant temp, battery, DEF."""

from __future__ import annotations

from datetime import datetime, timezone
from telegram.ext import Application

from samsara_client import populate_company_display
from core.context import set_tenant_display

from bot.config import (
    logger, get_client,
    HEALTH_ALERT_COOLDOWN_HOURS,
    get_platform_db, get_tenant_db,
)
import bot.redis_client as rcache
from formatters import format_health_alert
from core.isolation import run_account_job
from core.bot_registry import get_app_for_account

from bot.alerts.pipeline import (
    AlertSeverity, COOLANT_SPNS, _warmup_done,
    send_alert, is_vehicle_suppressed,
)
from bot.alerts.escalation import _auto_resolve_vehicle_alerts
from bot.alerts.ai_maintenance import _get_ai_health_note


# ── Health-alert dedup state dicts ───────────────────────────────

_known_health: dict[str, set[str]] = {}
_health_last_sent: dict[str, float] = {}

# Health-alert severity classification
_CRITICAL_HEALTH = {"low_oil_pressure", "high_coolant_temp"}
_WARNING_HEALTH = {"low_battery", "low_def", "coolant_dtc"}


# ── Health-alert dedup helpers ───────────────────────────────────

async def _get_known_health(account_id: int, vid: str) -> set[str]:
    """Previously known health alert names for a vehicle."""
    if rcache.is_available():
        return await rcache.smembers(f"t:{account_id}:health:{vid}")
    return _known_health.get(vid, set())


async def _set_known_health(account_id: int, vid: str, alerts: set[str]):
    if rcache.is_available():
        await rcache.sset(f"t:{account_id}:health:{vid}", alerts, ttl=86400)
    else:
        _known_health[vid] = alerts


async def _get_health_last_sent(account_id: int, vid: str) -> float:
    """Get timestamp of last health alert sent for this vehicle."""
    if rcache.is_available():
        val = await rcache.get(f"t:{account_id}:health_ts:{vid}")
        return float(val) if val else 0.0
    return _health_last_sent.get(vid, 0.0)


async def _set_health_last_sent(account_id: int, vid: str):
    """Record that a health alert was just sent for this vehicle."""
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(HEALTH_ALERT_COOLDOWN_HOURS * 3600) + 300  # slight buffer
    if rcache.is_available():
        await rcache.set(f"t:{account_id}:health_ts:{vid}", now, ttl=ttl)
    else:
        _health_last_sent[vid] = now


def _is_health_on_cooldown(last_sent: float) -> bool:
    """Return True if a health alert was sent too recently."""
    if last_sent == 0.0:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - last_sent) < (HEALTH_ALERT_COOLDOWN_HOURS * 3600)


# ═══════════════════════════════════════════════════════════════════
#  Health Alerts Scheduled Job
# ═══════════════════════════════════════════════════════════════════

async def check_health_alerts(app: Application):
    """Check all accounts for vehicle health warnings and push alerts.

    Detects: low battery, low oil pressure, high coolant temp, low DEF.
    Skips seatbelt and engine-load as they are transient. Also scans
    active DTCs for coolant-related SPNs.

    Severity classification:
      CRITICAL — low oil pressure, high coolant temp (engine damage risk)
      WARNING  — low battery, low DEF, coolant DTC
    """
    try:
        subscribers = await get_platform_db().get_all_typed_subscribers("health")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        # Startup warm-up: first cycle only populates caches, no alerts.
        # Prevents burst of alerts on server restart when all dedup state
        # is empty (every existing issue looks "new").
        is_warmup = not _warmup_done.get("health", False)

        for account_id, subs in acct_subs.items():
            bot_app = get_app_for_account(account_id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping health check", account_id)
                continue
            await run_account_job(
                _check_health_account(bot_app, account_id, subs, is_warmup),
                account_id=account_id,
                job_name="health_check",
            )

        if is_warmup:
            _warmup_done["health"] = True
            logger.info("Health alert warm-up complete — caches populated, no alerts sent")

    except Exception as e:
        logger.error(f"Health check error: {e}")


async def _check_health_account(
    bot_app: Application, account_id: int, subs: list, is_warmup: bool,
):
    """Process health alerts for a single account."""
    samsara = await get_client(account_id)
    vehicles = await samsara.get_vehicle_health()

    if is_warmup:
        for v in vehicles:
            co = v.get("_org", "?")
            vid = f"{account_id}:{co}:{v['id']}"
            health_alerts = [
                a for a in v.get("_health_alerts", [])
                if a in ("low_battery", "low_oil_pressure",
                         "high_coolant_temp", "low_def")
            ]
            if health_alerts:
                await _set_known_health(account_id, vid, set(health_alerts))
                await _set_health_last_sent(account_id, vid)
        return

    tenant = await get_tenant_db(account_id)
    acct_companies = await tenant.get_account_companies(account_id)
    populate_company_display(acct_companies)
    await samsara.ensure_org_ids()
    display = {co.code: co.display_name or co.code for co in acct_companies}
    set_tenant_display(display, samsara.org_ids)
    company_codes = [o.code for o in acct_companies]

    try:
        faulted, _, _ = await samsara.get_vehicles_with_faults()
        faulted_by_id = {v["id"]: v for v in faulted}
    except Exception:
        faulted_by_id = {}

    for v in vehicles:
        try:
            if await is_vehicle_suppressed(account_id, v.get("name", "")):
                continue

            alerts = v.get("_health_alerts", [])
            push_alerts = [
                a for a in alerts
                if a in ("low_battery", "low_oil_pressure",
                         "high_coolant_temp", "low_def")
            ]

            fv = faulted_by_id.get(v["id"])
            if fv:
                has_coolant_dtc = any(
                    dtc.get("spnId") in COOLANT_SPNS
                    for dtc in fv.get("_dtcs", [])
                )
                if has_coolant_dtc and "coolant_dtc" not in push_alerts:
                    push_alerts.append("coolant_dtc")

            if not push_alerts:
                co = v.get("_org", "?")
                vid = f"{account_id}:{co}:{v['id']}"
                cleared = await tenant.clear_alert_history(
                    account_id, "health", v["id"],
                )
                for rec in cleared:
                    if rec.get("message_id") and rec.get("chat_id"):
                        try:
                            await bot_app.bot.delete_message(
                                chat_id=rec["chat_id"],
                                message_id=rec["message_id"],
                            )
                        except Exception:
                            pass
                # Auto-resolve any unacked health alerts for this vehicle
                await _auto_resolve_vehicle_alerts(
                    bot_app, account_id, "health", v["id"],
                    v.get("name", "?"), co,
                    bot_app=bot_app,
                )
                # Clear dedup so returning issues are detected as new
                await _set_known_health(account_id, vid, set())
                continue

            co = v.get("_org", "?")
            vid = f"{account_id}:{co}:{v['id']}"
            previously_known = await _get_known_health(account_id, vid)
            new_alerts = set(push_alerts) - previously_known

            if not new_alerts:
                await _set_known_health(account_id, vid, set(push_alerts))
                continue

            # ── Classify severity ────────────────────
            if new_alerts & _CRITICAL_HEALTH:
                severity = AlertSeverity.CRITICAL
            else:
                severity = AlertSeverity.WARNING

            # Cooldown: skip WARNING if recently alerted.
            # CRITICAL always bypasses cooldown (safety-first).
            if severity != AlertSeverity.CRITICAL:
                last_sent = await _get_health_last_sent(account_id, vid)
                if _is_health_on_cooldown(last_sent):
                    continue

            show_co = len(company_codes) > 1
            health = v.get("_health", {})
            alert_text = format_health_alert(
                v, list(new_alerts), health,
                show_company=show_co,
            )

            # Proactive AI — only if any subscriber enabled it
            ai_note = ""
            if any(getattr(s, 'ai_health', False) for s in subs):
                ai_note = await _get_ai_health_note(v, list(new_alerts), health)

            # ── Universal pipeline ───────────────────
            await send_alert(
                bot_app,
                account_id=account_id,
                alert_type="health",
                severity=severity,
                vehicle=v,
                alert_text=alert_text,
                subscribers=subs,
                co=co,
                ai_note=ai_note,
                alert_key_detail="-".join(sorted(new_alerts)),
                bot_app=bot_app,
            )

            await _set_known_health(account_id, vid, set(push_alerts))
            await _set_health_last_sent(account_id, vid)
        except Exception as e:
            logger.warning(f"Health check for vehicle {v.get('name', '?')}: {e}")
            continue
