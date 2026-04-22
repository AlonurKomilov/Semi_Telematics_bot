"""Fault alert checks — detect new DTC codes and send alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from cachetools import LRUCache
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

import adapters.cache.redis as rcache
from adapters.samsara.client import populate_company_display
from capabilities.alerting.ai_maintenance import (
    _get_ai_diagnosis_note, auto_create_maintenance_from_faults,
)
from capabilities.alerting.escalation import _auto_resolve_vehicle_alerts
from capabilities.alerting.pipeline import (
    AlertSeverity, COOLANT_SPNS,
    send_alert, is_vehicle_suppressed,
)
from capabilities.formatting import format_new_fault_alert, format_critical_fault_alert
from core.bot_registry import get_app_for_account
from core.config import FAULT_ALERT_COOLDOWN_HOURS
from core.context import set_tenant_display
from core.isolation import run_account_job
from core.services import get_client, get_platform_db, get_tenant_db

logger = logging.getLogger("bot")

# Fault-code dedup cache — canonical home (used only here).
_known_faults: dict = LRUCache(maxsize=10_000)  # "acct:ORG:vid" → set(codes)

# J1939 SPNs related to coolant system — re-exported from pipeline for
# backward-compat but canonical definition lives there.


# ── Fault-alert dedup helpers (Redis → in-memory) ────────────────

async def _get_known_faults(account_id: int, vid: str) -> set[str]:
    """Get previously known fault codes for a vehicle (Redis → in-memory)."""
    if rcache.is_available():
        return await rcache.smembers(f"t:{account_id}:faults:{vid}")
    return _known_faults.get(f"{account_id}:{vid}", set())


async def _set_known_faults(account_id: int, vid: str, codes: set[str]):
    """Store known fault codes for a vehicle (Redis → in-memory)."""
    if rcache.is_available():
        await rcache.sset(f"t:{account_id}:faults:{vid}", codes, ttl=86400)
    else:
        _known_faults[f"{account_id}:{vid}"] = codes


# ── Fault alert cooldown helpers ─────────────────────────────────

_fault_last_sent: dict[str, float] = {}


async def _get_fault_last_sent(account_id: int, vid: str) -> float:
    """Get timestamp of last fault alert sent for this vehicle."""
    if rcache.is_available():
        val = await rcache.get(f"t:{account_id}:fault_ts:{vid}")
        return float(val) if val else 0.0
    return _fault_last_sent.get(f"{account_id}:{vid}", 0.0)


async def _set_fault_last_sent(account_id: int, vid: str):
    """Record that a fault alert was just sent for this vehicle."""
    now = datetime.now(timezone.utc).timestamp()
    ttl = int(FAULT_ALERT_COOLDOWN_HOURS * 3600) + 300
    if rcache.is_available():
        await rcache.cache_set(f"t:{account_id}:fault_ts:{vid}", now, ttl=ttl)
    else:
        _fault_last_sent[f"{account_id}:{vid}"] = now


def _is_fault_on_cooldown(last_sent: float) -> bool:
    """Return True if a fault alert was sent too recently."""
    if last_sent == 0.0:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - last_sent) < (FAULT_ALERT_COOLDOWN_HOURS * 3600)


# ── API Health Notifications ────────────────────────────────────

_API_ALERT_COOLDOWN_S = 6 * 3600
_api_alert_sent: dict[str, float] = {}


async def _notify_api_errors(
    bot_app: Application,
    account_id: int,
    skipped_codes: list[str],
):
    """Send a one-time Telegram alert to account owners/admins when
    a company's Samsara API call fails.  Throttled to one alert per
    company per _API_ALERT_COOLDOWN_S seconds.
    """
    now = datetime.now(timezone.utc).timestamp()
    codes_to_alert = []
    for code in skipped_codes:
        mem_key = f"{account_id}:{code}"
        redis_key = f"t:{account_id}:api_alert:{code}"

        # Check cooldown — Redis first, then in-memory fallback
        if rcache.is_available():
            if await rcache.exists(redis_key):
                continue
            codes_to_alert.append(code)
            await rcache.setex_flag(redis_key, _API_ALERT_COOLDOWN_S)
        else:
            last = _api_alert_sent.get(mem_key, 0)
            if now - last >= _API_ALERT_COOLDOWN_S:
                codes_to_alert.append(code)
                _api_alert_sent[mem_key] = now

    if not codes_to_alert:
        return

    admins = await get_platform_db().get_account_admins(account_id)
    if not admins:
        return

    codes_str = ", ".join(codes_to_alert)
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🚨  <b>API ERROR</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Company: <b>{codes_str}</b>\n"
        "\n"
        "  The Samsara API returned an error\n"
        "  for this company. Reports will be\n"
        "  missing data until the issue is fixed.\n"
        "\n"
        "  <b>Possible causes:</b>\n"
        "  • API token expired or revoked\n"
        "  • Token missing required permissions\n"
        "  • Samsara service outage\n"
        "\n"
        "  Check your Samsara dashboard or\n"
        "  re-add the API key to resolve."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Check Status", callback_data="cmd_api_status")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])

    for admin in admins:
        try:
            await bot_app.bot.send_message(
                chat_id=admin.telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception as e:
            logger.error(f"API alert to admin {admin.telegram_id}: {e}")

    logger.warning(
        f"API error alert sent for account {account_id}, "
        f"companies: {codes_str}"
    )


# ═══════════════════════════════════════════════════════════════════
#  Main fault-check scheduled job
# ═══════════════════════════════════════════════════════════════════

async def check_new_faults(app: Application):
    """Check all accounts with fault-alert subscribers for new faults.

    Classifies faults into CRITICAL (STOP/PROTECT/EMISSIONS lights or
    "Most Severe" FMI) and WARNING (normal faults). Uses the universal
    send_alert() pipeline for delivery.
    """
    try:
        subscribers = await get_platform_db().get_all_typed_subscribers("faults")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            bot_app = get_app_for_account(account_id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping fault check", account_id)
                continue
            await run_account_job(
                _check_faults_account(bot_app, account_id, subs),
                account_id=account_id,
                job_name="fault_check",
            )

    except Exception as e:
        logger.error(f"Fault check error: {e}")


async def _check_faults_account(bot_app: Application, account_id: int, subs: list):
    """Process fault alerts for a single account."""
    samsara = await get_client(account_id)
    faulted, _, _ = await samsara.get_vehicles_with_faults()

    if samsara.last_skipped:
        await _notify_api_errors(bot_app, account_id, samsara.last_skipped)

    tenant = await get_tenant_db(account_id)
    acct_companies = await tenant.get_account_companies(account_id)
    populate_company_display(acct_companies)
    await samsara.ensure_org_ids()
    display = {co.code: co.display_name or co.code for co in acct_companies}
    set_tenant_display(display, samsara.org_ids)
    company_codes = [o.code for o in acct_companies]

    for v in faulted:
        try:
            co = v.get("_org", "?")
            vid = f"{account_id}:{co}:{v['id']}"

            if await is_vehicle_suppressed(account_id, v["name"]):
                continue

            current_codes = set()
            for dtc in v.get("_dtcs", []):
                key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                current_codes.add(key)

            previously_known = await _get_known_faults(account_id, vid)
            new_codes = current_codes - previously_known

            if new_codes:
                new_dtcs = [
                    dtc for dtc in v.get("_dtcs", [])
                    if f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}" in new_codes
                ]
                if new_dtcs:
                    # ── Classify severity ────────────────
                    lights = v.get("_lights", {})
                    is_critical = (
                        lights.get("stopIsOn", False)
                        or lights.get("protectIsOn", False)
                        or lights.get("emissionsIsOn", False)
                    )
                    if not is_critical:
                        for dtc in new_dtcs:
                            fmi_desc = dtc.get("fmiDescription", "").lower()
                            if "most severe" in fmi_desc:
                                is_critical = True
                                break

                    severity = (AlertSeverity.CRITICAL if is_critical
                                else AlertSeverity.WARNING)

                    # Cooldown: skip WARNING faults if recently alerted
                    if severity != AlertSeverity.CRITICAL:
                        last_sent = await _get_fault_last_sent(account_id, vid)
                        if _is_fault_on_cooldown(last_sent):
                            await _set_known_faults(account_id, vid, current_codes)
                            continue

                    # Check for coolant-related DTCs
                    has_coolant_dtc = any(
                        dtc.get("spnId") in COOLANT_SPNS
                        for dtc in new_dtcs
                    )

                    show_co = len(company_codes) > 1
                    if severity == AlertSeverity.CRITICAL:
                        alert_text = format_critical_fault_alert(
                            v, new_dtcs, lights, show_company=show_co,
                        )
                    else:
                        alert_text = format_new_fault_alert(
                            v, new_dtcs, show_company=show_co,
                        )

                    if has_coolant_dtc:
                        alert_text += (
                            "\n\n  🌡 <b>Coolant system fault detected</b>"
                            "\n  Check coolant level and temp"
                        )

                    # Proactive AI — only if any subscriber enabled it
                    ai_note = ""
                    if any(getattr(s, 'ai_fault', False) for s in subs):
                        ai_note = await _get_ai_diagnosis_note(v, new_dtcs)

                    # Build fault detail with descriptions
                    fault_details = []
                    for dtc in new_dtcs:
                        spn = dtc.get('spnId', '?')
                        fmi = dtc.get('fmiId', '?')
                        desc = dtc.get('spnDescription', '')
                        fault_details.append(f"{spn}-{fmi}:{desc}")
                    fault_detail_str = "|".join(sorted(fault_details))

                    # ── Universal pipeline ───────────────
                    await send_alert(
                        bot_app,
                        account_id=account_id,
                        alert_type="fault",
                        severity=severity,
                        vehicle=v,
                        alert_text=alert_text,
                        subscribers=subs,
                        co=co,
                        ai_note=ai_note,
                        alert_key_detail=fault_detail_str,
                        bot_app=bot_app,
                    )

                    if severity == AlertSeverity.CRITICAL:
                        await auto_create_maintenance_from_faults(
                            account_id, v["name"], new_dtcs,
                        )

                    await _set_fault_last_sent(account_id, vid)

            await _set_known_faults(account_id, vid, current_codes)
        except Exception as e:
            logger.warning(f"Fault check for vehicle {v.get('name', '?')}: {e}")
            continue

    # Clear fault alert history for vehicles that no longer
    # have faults — delete old messages from chat
    current_faulted_vids = {
        f"{account_id}:{v.get('_org', '?')}:{v['id']}"
        for v in faulted
    }
    stale_fault_keys = [
        k for k in list(_known_faults.keys())
        if k.startswith(f"{account_id}:")
        and k not in current_faulted_vids
    ]
    tenant = await get_tenant_db(account_id)
    for k in stale_fault_keys:
        parts = k.split(":", 2)
        if len(parts) == 3:
            co = parts[1]
            v_id = parts[2]
            cleared = await tenant.clear_alert_history(
                account_id, "fault", v_id,
            )
            for rec in cleared:
                if rec.get("message_id") and rec.get("chat_id"):
                    try:
                        await bot_app.bot.delete_message(
                            chat_id=rec["chat_id"],
                            message_id=rec["message_id"],
                        )
                    except Exception as e:
                        logger.debug("Could not delete fault alert message: %s", e)
            await _auto_resolve_vehicle_alerts(
                bot_app, account_id, "fault", v_id, "", co,
                bot_app=bot_app,
            )
        await _set_known_faults(account_id, k, set())


async def initialize_known_faults():
    """Pre-populate known faults for all accounts with alert subscribers."""
    try:
        subscribers = await get_platform_db().get_all_alert_subscribers()
        account_ids = set(s.account_id for s in subscribers)

        for account_id in account_ids:
            try:
                samsara = await get_client(account_id)
                faulted, _, _ = await samsara.get_vehicles_with_faults()
                for v in faulted:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    codes = set()
                    for dtc in v.get("_dtcs", []):
                        key = f"{dtc.get('spnId', '?')}-{dtc.get('fmiId', '?')}"
                        codes.add(key)
                    await _set_known_faults(account_id, vid, codes)
            except Exception as e:
                logger.error(f"Init faults for account {account_id}: {e}")

        logger.info(f"Known faults loaded for {len(_known_faults)} vehicles")
    except Exception as e:
        logger.error(f"Init faults failed: {e}")
