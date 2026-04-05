"""Low-fuel alert checks with hysteresis dedup."""

from __future__ import annotations

from datetime import datetime, timezone
from telegram.ext import Application

from samsara_client import populate_company_display
from formatters import format_low_fuel_alert

from bot.config import (
    db, logger, get_client,
    FUEL_THRESHOLD, FUEL_HYSTERESIS_PERCENT,
)
import bot.redis_client as rcache

from bot.alerts.pipeline import (
    AlertSeverity, _warmup_done,
    send_alert, is_vehicle_suppressed,
)
from bot.alerts.escalation import _auto_resolve_vehicle_alerts


# ── Low fuel dedup state ─────────────────────────────────────────

_known_low_fuel: dict[str, bool] = {}

# Fuel severity threshold: below this percentage → CRITICAL
FUEL_CRITICAL_PCT = 10


async def _is_known_low_fuel(vid: str) -> bool:
    if rcache.is_available():
        return await rcache.exists(f"lowfuel:{vid}")
    return _known_low_fuel.get(vid, False)


async def _set_low_fuel_flag(vid: str, flagged: bool):
    if rcache.is_available():
        if flagged:
            await rcache.setex_flag(f"lowfuel:{vid}", 86400)
        else:
            # Let it expire naturally; no explicit delete needed
            pass
    else:
        if flagged:
            _known_low_fuel[vid] = True
        else:
            _known_low_fuel.pop(vid, None)


# ═══════════════════════════════════════════════════════════════════
#  Low Fuel Alerts Scheduled Job
# ═══════════════════════════════════════════════════════════════════

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
        subscribers = await db.get_all_typed_subscribers("fuel")
        if not subscribers:
            return

        clear_threshold = FUEL_THRESHOLD + FUEL_HYSTERESIS_PERCENT

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        # Startup warm-up for fuel (same as health)
        is_warmup = not _warmup_done.get("fuel", False)

        for account_id, subs in acct_subs.items():
            try:
                samsara = await get_client(account_id)
                low_fuel = await samsara.get_low_fuel_vehicles(FUEL_THRESHOLD)

                if is_warmup:
                    for v in low_fuel:
                        co = v.get("_org", "?")
                        vid = f"{account_id}:{co}:{v['id']}"
                        await _set_low_fuel_flag(vid, True)
                    continue

                acct_companies = await db.get_account_companies(account_id)
                populate_company_display(acct_companies)
                await samsara.ensure_org_ids()
                company_codes = [o.code for o in acct_companies]

                low_fuel_ids = set()

                for v in low_fuel:
                    co = v.get("_org", "?")
                    vid = f"{account_id}:{co}:{v['id']}"
                    low_fuel_ids.add(vid)

                    if await is_vehicle_suppressed(account_id, v.get("name", "")):
                        continue

                    if await _is_known_low_fuel(vid):
                        continue

                    fuel_pct = v.get("_fuel_pct", 0)
                    show_co = len(company_codes) > 1
                    alert_text = format_low_fuel_alert(
                        v, fuel_pct, show_company=show_co,
                    )

                    # ── Classify severity ────────────────────
                    severity = (AlertSeverity.CRITICAL if fuel_pct < FUEL_CRITICAL_PCT
                                else AlertSeverity.WARNING)

                    # ── Universal pipeline ───────────────────
                    await send_alert(
                        app,
                        account_id=account_id,
                        alert_type="fuel",
                        severity=severity,
                        vehicle=v,
                        alert_text=alert_text,
                        subscribers=subs,
                        co=co,
                        alert_key_detail=f"fuel:{fuel_pct:.0f}",
                    )

                    await _set_low_fuel_flag(vid, True)

                # Hysteresis: only clear dedup for vehicles that rose well
                # above the threshold
                stale_keys = [
                    k for k in list(_known_low_fuel.keys())
                    if k.startswith(f"{account_id}:") and k not in low_fuel_ids
                ]
                if stale_keys:
                    try:
                        all_vehicles = await samsara.get_low_fuel_vehicles(
                            clear_threshold
                        )
                        still_below_clear = {
                            f"{account_id}:{v.get('_org', '?')}:{v['id']}"
                            for v in all_vehicles
                        }
                    except Exception:
                        still_below_clear = set()

                    for k in stale_keys:
                        if k not in still_below_clear:
                            await _set_low_fuel_flag(k, False)
                            parts = k.split(":", 2)
                            if len(parts) == 3:
                                co = parts[1]
                                v_id = parts[2]
                                cleared = await db.clear_alert_history(
                                    account_id, "fuel", v_id,
                                )
                                for rec in cleared:
                                    if rec.get("message_id") and rec.get("chat_id"):
                                        try:
                                            await app.bot.delete_message(
                                                chat_id=rec["chat_id"],
                                                message_id=rec["message_id"],
                                            )
                                        except Exception:
                                            pass
                                # Auto-resolve unacked fuel alerts
                                await _auto_resolve_vehicle_alerts(
                                    app, account_id, "fuel", v_id, "", co,
                                )

            except Exception as e:
                logger.error(f"Fuel check for account {account_id}: {e}")

        if is_warmup:
            _warmup_done["fuel"] = True
            logger.info("Fuel alert warm-up complete — caches populated, no alerts sent")

    except Exception as e:
        logger.error(f"Fuel check error: {e}")
