"""Geofence Alerts — list geofences and poll for entry/exit events."""

from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t
from capabilities.geofencing.geometry import is_inside_geofence as _is_inside_geofence, geofence_shape_type as _geofence_shape_type

from telegram import Update
from telegram.ext import ContextTypes, Application

from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display

from interfaces.bot.config import logger, get_client, get_platform_db, get_tenant_db
from core.isolation import run_account_job
from core.bot_registry import get_app_for_account
from interfaces.bot.keyboards import back_kb, geofence_list_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered

try:
    import adapters.cache.redis as rcache
except ImportError:
    rcache = None


@_require_registered
async def cmd_geofences(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        company: str | None = None):
    """List geofences from Samsara."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_geofence_all") or can(user.role, "can_geofence_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)
    samsara = await get_client(user.account_id)

    await _show_loading(update, context, t('geofence.loading'))

    try:
        geofences = await samsara.get_geofences(company=company)
        if not geofences:
            await _show(update, context, [
                t('geofence.empty')
            ], keyboard=back_kb())
            return

        now_et = _dt.now(_TZ_ET)
        total = len(geofences)
        shown = min(total, 15)
        sep = t("alert_format.separator")
        text = (
            f"{sep}\n"
            f"  {t('geofence.header')}\n"
            f"{sep}\n"
            f"\n  {t('geofence.showing').format(shown=shown, total=total)}\n"
            f"  {now_et:%b %d, %Y %I:%M %p ET}\n"
        )

        for gf in geofences[:15]:
            name = gf.get("name", "Unknown")
            org = gf.get("_org", "")
            gf_type = t('geofence.type_circle') if _geofence_shape_type(gf) == 'circle' else t('geofence.type_polygon')
            text += f"\n  {gf_type} <b>{name}</b>"
            if org:
                text += f" [{org}]"

        kb = geofence_list_kb(geofences)
        await _show(update, context, [text], keyboard=kb)

    except Exception as e:
        logger.error(f"Geofences error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


async def check_geofence_events(app: Application):
    """Scheduled job: poll vehicle locations against geofences.

    Runs every 5 minutes. Compares current positions to geofence boundaries.
    On state change (enter/exit), sends alert via the universal pipeline
    with INFO severity (no ACK required).
    """
    try:
        accounts = await get_platform_db().list_accounts()
        for account in accounts:
            bot_app = get_app_for_account(account.id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping geofence check", account.id)
                continue
            await run_account_job(
                _check_geofences_account(bot_app, account),
                account_id=account.id,
                job_name="geofence_check",
            )

    except Exception as e:
        logger.error(f"Geofence check error: {e}")


async def _check_geofences_account(bot_app: Application, account):
    """Process geofence events for a single account."""
    tenant = await get_tenant_db(account.id)
    companies = await tenant.get_account_companies(account.id)
    if not companies:
        return

    try:
        samsara = await get_client(account.id)
    except Exception:
        return

    try:
        geofences = await samsara.get_geofences()
        vehicles = await samsara.get_fleet_overview()
    except Exception as e:
        logger.debug(f"Geofence check for account {account.id}: {e}")
        return

    if not geofences or not vehicles:
        return

    for v in vehicles:
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue

        vid = v.get("id", v.get("name", ""))
        vname = v.get("name", "?")

        for gf in geofences:
            gfid = gf.get("id", "")
            gfname = gf.get("name", "?")
            inside = _is_inside_geofence(lat, lng, gf)
            state_key = f"t:{account.id}:geofence:{vid}:{gfid}"

            # Get previous state
            prev_state = None
            if rcache and rcache.is_available():
                raw = await rcache.get(state_key)
                if raw is not None:
                    prev_state = raw.get("state")
            current_state = "inside" if inside else "outside"

            # Store current state
            if rcache and rcache.is_available():
                await rcache.cache_set(state_key, {"state": current_state}, ttl=86400)

            # Detect state change
            if prev_state is not None and prev_state != current_state:
                event = t('geofence.event_entered') if inside else t('geofence.event_exited')
                emoji = "📍" if inside else "📤"

                from capabilities.alerting import is_vehicle_suppressed
                if await is_vehicle_suppressed(account.id, vname):
                    continue

                # Auto-resolve: on exit, clear geofence alert history
                if event == "exited":
                    cleared = await tenant.clear_alert_history(
                        account.id, "geofence", vid,
                    )
                    for rec in cleared:
                        if rec.get("message_id") and rec.get("chat_id"):
                            try:
                                await bot_app.bot.delete_message(
                                    chat_id=rec["chat_id"],
                                    message_id=rec["message_id"],
                                )
                            except Exception as e:
                                logger.debug("Could not delete geofence alert message: %s", e)

                alert_text = (
                    f"{emoji} {t('geofence.alert_title')}\n\n"
                    f"  🚛 <b>{vname}</b> {event}\n"
                    f"  📍 <b>{gfname}</b>\n"
                )

                # Use universal alert pipeline
                from capabilities.alerting import send_alert, AlertSeverity
                subscribers = await get_platform_db().get_typed_alert_subscribers(
                    account.id, "geofence"
                )
                co = v.get("_org", "?")
                vehicle_dict = {"id": vid, "name": vname, "_org": co}

                await send_alert(
                    bot_app,
                    account_id=account.id,
                    alert_type="geofence",
                    severity=AlertSeverity.INFO,
                    vehicle=vehicle_dict,
                    alert_text=alert_text,
                    subscribers=subscribers,
                    co=co,
                    alert_key_detail=f"{event} {gfname}",
                    bot_app=bot_app,
                )
