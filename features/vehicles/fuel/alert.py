"""Low-fuel alert checks with hysteresis dedup."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from cachetools import LRUCache
from telegram.ext import Application

import infra.cache as rcache
from adapters.samsara.client import populate_company_display
from capabilities.alerting.ai_maintenance import _notify_api_errors
from capabilities.alerting.escalation import _auto_resolve_vehicle_alerts
from capabilities.alerting.pipeline import (
    AlertSeverity, _warmup_done,
    send_alert, is_vehicle_suppressed,
)
from capabilities.formatting import format_low_fuel_alert
from capabilities.telemetry.service import get_low_fuel_vehicles as _svc_low_fuel
from infra.bot_registry import get_app_for_account
from infra.config import FUEL_THRESHOLD, FUEL_HYSTERESIS_PERCENT
from infra.context import set_tenant_display
from infra.isolation import run_account_job
from infra.services import get_client, get_platform_db, get_tenant_db
from capabilities.alerting.registry import register_alert_source
from infra.config import ALERT_INTERVAL

logger = logging.getLogger("bot")


# ── Low fuel dedup state (bounded LRU cache) ─────────────────────
# Bounded to 10k keys; Redis is the primary store, this is the fallback.

_known_low_fuel: LRUCache = LRUCache(maxsize=10_000)

# Fuel severity threshold: below this percentage → CRITICAL
FUEL_CRITICAL_PCT = 10


async def _is_known_low_fuel(account_id: int, vid: str) -> bool:
    if rcache.is_available():
        return await rcache.exists(f"t:{account_id}:lowfuel:{vid}")
    return _known_low_fuel.get(f"{account_id}:{vid}", False)


async def _set_low_fuel_flag(account_id: int, vid: str, flagged: bool):
    if rcache.is_available():
        if flagged:
            await rcache.setex_flag(f"t:{account_id}:lowfuel:{vid}", 86400)
        else:
            # Let it expire naturally; no explicit delete needed
            pass
    else:
        if flagged:
            _known_low_fuel[f"{account_id}:{vid}"] = True
        else:
            _known_low_fuel.pop(f"{account_id}:{vid}", None)


# ═══════════════════════════════════════════════════════════════════
#  Low Fuel Alerts Scheduled Job
# ═══════════════════════════════════════════════════════════════════

@register_alert_source("fuel_check", trigger="interval", minutes=ALERT_INTERVAL)
async def check_low_fuel(app: Application):
    """Check all accounts for vehicles below the fuel threshold and push alerts.

    Uses hysteresis: alerts when fuel drops below FUEL_THRESHOLD, but
    only clears the dedup flag when fuel rises above
    FUEL_THRESHOLD + FUEL_HYSTERESIS_PERCENT to prevent oscillation spam.

    Severity classification:
      CRITICAL — fuel below FUEL_CRITICAL_PCT (e.g. <10%)
      WARNING  — fuel below FUEL_THRESHOLD but above FUEL_CRITICAL_PCT
    """
    try:
        subscribers = await get_platform_db().get_all_typed_subscribers("fuel")
        if not subscribers:
            return

        clear_threshold = FUEL_THRESHOLD + FUEL_HYSTERESIS_PERCENT

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            # Startup warm-up for fuel (per-account)
            is_warmup = account_id not in _warmup_done.get("fuel", set())

            bot_app = get_app_for_account(account_id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping fuel check", account_id)
                continue
            tenant = await get_tenant_db(account_id)
            await run_account_job(
                _check_fuel_account(bot_app, account_id, subs, is_warmup, clear_threshold),
                account_id=account_id,
                job_name="fuel_check",
                tenant_db=tenant,
            )

            if is_warmup:
                _warmup_done.setdefault("fuel", set()).add(account_id)
                logger.info("Fuel alert warm-up complete for account %d", account_id)

    except Exception as e:
        logger.error(f"Fuel check error: {e}")


async def _check_fuel_account(
    bot_app: Application, account_id: int, subs: list,
    is_warmup: bool, clear_threshold: float,
):
    """Process fuel alerts for a single account."""
    samsara = await get_client(account_id)
    low_fuel = await _svc_low_fuel(account_id, FUEL_THRESHOLD)

    if samsara.last_skipped:
        await _notify_api_errors(bot_app, account_id, samsara.last_skipped)

    if is_warmup:
        for v in low_fuel:
            co = v.get("_org", "?")
            vid = f"{account_id}:{co}:{v['id']}"
            await _set_low_fuel_flag(account_id, vid, True)
        return

    tenant = await get_tenant_db(account_id)
    acct_companies = await tenant.get_account_companies(account_id)
    populate_company_display(acct_companies)
    await samsara.ensure_org_ids()
    display = {co.code: co.display_name or co.code for co in acct_companies}
    set_tenant_display(display, samsara.org_ids)
    company_codes = [o.code for o in acct_companies]

    low_fuel_ids = set()

    for v in low_fuel:
        co = v.get("_org", "?")
        vid = f"{account_id}:{co}:{v['id']}"
        low_fuel_ids.add(vid)

        if await is_vehicle_suppressed(account_id, v.get("name", "")):
            continue

        if await _is_known_low_fuel(account_id, vid):
            continue

        fuel_pct = v.get("_fuel_pct", 0)
        show_co = len(company_codes) > 1
        # Detection time = now (this poll cycle saw fuel cross the
        # threshold).  Driver is best-effort — populated only when the
        # vehicle dict came from a flow that joined drivers in.
        detected_at = datetime.now(timezone.utc).isoformat()
        alert_text = format_low_fuel_alert(
            v, fuel_pct, show_company=show_co,
            driver_name=v.get("_driver_name"),
            detected_at=detected_at,
        )

        # ── Classify severity ────────────────────
        severity = (AlertSeverity.CRITICAL if fuel_pct < FUEL_CRITICAL_PCT
                    else AlertSeverity.WARNING)

        # ── Universal pipeline ───────────────────
        await send_alert(
            bot_app,
            account_id=account_id,
            alert_type="fuel",
            severity=severity,
            vehicle=v,
            alert_text=alert_text,
            subscribers=subs,
            co=co,
            alert_key_detail=f"fuel:{fuel_pct:.0f}",
            bot_app=bot_app,
        )

        await _set_low_fuel_flag(account_id, vid, True)

    # Hysteresis: only clear dedup for vehicles that rose well
    # above the threshold
    stale_keys = [
        k for k in list(_known_low_fuel.keys())
        if k.startswith(f"{account_id}:") and k not in low_fuel_ids
    ]
    if stale_keys:
        try:
            all_vehicles = await _svc_low_fuel(account_id, clear_threshold)
            still_below_clear = {
                f"{account_id}:{v.get('_org', '?')}:{v['id']}"
                for v in all_vehicles
            }
        except Exception:
            still_below_clear = set()

        tenant = await get_tenant_db(account_id)
        for k in stale_keys:
            if k not in still_below_clear:
                await _set_low_fuel_flag(account_id, k, False)
                parts = k.split(":", 2)
                if len(parts) == 3:
                    co = parts[1]
                    v_id = parts[2]
                    await _auto_resolve_vehicle_alerts(
                        bot_app, account_id, "fuel", v_id, "", co,
                        bot_app=bot_app,
                    )
