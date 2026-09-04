"""Geofence Alerts — list geofences and poll for entry/exit events."""

from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t
from features.geofencing.geometry import is_inside_geofence as _is_inside_geofence, geofence_shape_type as _geofence_shape_type, distance_to_geofence as _distance_to_geofence
from features.geofencing.service import get_platform_geofences

from telegram import Update
from telegram.ext import ContextTypes, Application

from capabilities.permissions.roles import can
from adapters.samsara.client import populate_company_display

from interfaces.bot.config import logger
from interfaces.bot.state import get_platform_db, get_tenant_db
from infra.isolation import run_account_job
from infra.bot_registry import get_app_for_account
from infra.services import get_tenant_db as _get_tenant_db_rls
from interfaces.bot.keyboards import back_kb, geofence_list_kb
from interfaces.bot.helpers import (
    _show, _show_loading, _safe_error, reply_dashboard_redirect,
)
from interfaces.bot.auth import _require_registered
from capabilities.alerting.registry import register_alert_source

try:
    import infra.cache as rcache
except ImportError:
    rcache = None


# ── Bot display commands ──────────────────────────────────────────────────────

@_require_registered
async def cmd_geofences(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        company: str | None = None):
    """List geofences — Samsara zones + platform zones."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_view_geofence")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)

    await _show_loading(update, context, t('geofence.loading'))

    try:
        from features.geofencing.service import get_geofences as _svc_geofences
        samsara_zones = await _svc_geofences(user.account_id, company=company)
        platform_zones = await tenant.get_platform_geofences(user.account_id, is_active=True)

        now_et = _dt.now(_TZ_ET)
        sep = t("alert_format.separator")
        total = len(samsara_zones) + len(platform_zones)
        shown = min(total, 20)
        text = (
            f"{sep}\n"
            f"  {t('geofence.header')}\n"
            f"{sep}\n"
            f"\n  {t('geofence.showing').format(shown=shown, total=total)}\n"
            f"  {now_et:%b %d, %Y %I:%M %p ET}\n"
        )

        # Platform zones (first, most relevant)
        if platform_zones:
            text += "\n  <b>── Platform Zones ──</b>\n"
            for z in platform_zones[:10]:
                shape = "⭕" if z["shape_type"] == "circle" else "🔷"
                ztype = z.get("geofence_type", "custom")
                text += f"\n  {shape} <b>{z['name']}</b> [{ztype}]"

        # Samsara zones
        remaining = max(0, 10 - len(platform_zones))
        if samsara_zones and remaining > 0:
            text += "\n\n  <b>── Samsara Zones ──</b>\n"
            for gf in samsara_zones[:remaining]:
                name = gf.get("name", "Unknown")
                org = gf.get("_org", "")
                gf_type = t('geofence.type_circle') if _geofence_shape_type(gf) == 'circle' else t('geofence.type_polygon')
                text += f"\n  {gf_type} <b>{name}</b>"
                if org:
                    text += f" [{org}]"

        kb = geofence_list_kb(samsara_zones)
        await _show(update, context, [text], keyboard=kb)

    except Exception as e:
        logger.error(f"Geofences error: {e}")
        await _show(update, context, [_safe_error(e)], keyboard=back_kb())


# ── Zone management commands (retired from bot) ────────────────────────────────
#
# Creating / editing / deleting platform geofence zones requires
# lat/lng coordinates + role-checkbox selection, which is genuinely
# dashboard-shaped work — the bot wizard was clunky and the
# lat/lng-via-text-message step was the worst part of it.  These
# commands now redirect to the dashboard.  The read-only
# ``/geofences`` listing (above) stays for quick "what zones do we
# have?" lookups, and the entry/exit alert scheduler (below) keeps
# firing on geofence events.  The text-input wizard helper
# ``handle_add_zone_text`` was also removed; its callers fall
# through to the default text handler.


@_require_registered
async def cmd_add_zone(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect to the dashboard Geofences page."""
    await reply_dashboard_redirect(
        update,
        title="📍 Geofences moved",
        body=(
            "Creating geofence zones (name, coordinates, radius, role "
            "notifications) lives on the dashboard now — pick a point "
            "on the map instead of typing lat/lng."
        ),
        path="/geofences",
        label="Open Geofences",
    )


@_require_registered
async def cmd_list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect — the read-only ``/geofences`` listing (above) covers
    the quick "what zones do we have?" question; the manage view
    lives on the dashboard."""
    await reply_dashboard_redirect(
        update,
        title="📍 Geofences moved",
        body=(
            "Detailed zone management (edit name, change radius, "
            "tweak role notifications) lives on the dashboard.  Use "
            "<code>/geofences</code> for a quick read-only list."
        ),
        path="/geofences",
        label="Open Geofences",
    )


@_require_registered
async def cmd_delete_zone(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa: ARG001
    """Redirect — deletion confirmation lives on the dashboard."""
    await reply_dashboard_redirect(
        update,
        title="📍 Geofences moved",
        body=(
            "Deleting geofence zones lives on the dashboard now "
            "(deletion is destructive — the dashboard has a proper "
            "confirmation dialog)."
        ),
        path="/geofences",
        label="Open Geofences",
    )


# ── Scheduled poller ──────────────────────────────────────────────────────────

# Distance from zone boundary that counts as "approaching" (1 mile)
_NEARBY_THRESHOLD_M: float = 1_609.344

@register_alert_source("geofence_check", trigger="interval", minutes=5)
async def check_geofence_events(app: Application):
    """Scheduled job: poll vehicle locations against platform geofences.

    Runs every 5 minutes. GPS coordinates come from Samsara. Zone definitions
    come from the platform DB (platform_geofences table). Samsara's own zone
    engine is not used for alerts — only for GPS data.
    """
    try:
        accounts = await get_platform_db().list_accounts()
        for account in accounts:
            bot_app = get_app_for_account(account.id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping geofence check", account.id)
                continue
            tenant_db_rls = await _get_tenant_db_rls(account.id)
            await run_account_job(
                _check_geofences_account(bot_app, account),
                account_id=account.id,
                job_name="geofence_check",
                tenant_db=tenant_db_rls,
            )

    except Exception as e:
        logger.error(f"Geofence check error: {e}")


async def _check_geofences_account(bot_app: Application, account):
    """Process platform geofence events for a single account.

    - Zone definitions: platform DB (not Samsara)
    - Vehicle GPS: Samsara get_vehicles_overview() — lat/lng only
    - Notifications: filtered by zone.notify_roles + driver isolation
    - Maintenance cross-reference: appended to entry alerts
    """
    tenant = await get_tenant_db(account.id)

    # Load platform zones — bail early if none configured
    try:
        platform_zones = await get_platform_geofences(account.id, tenant)
    except Exception as e:
        logger.debug("Could not load platform zones for account %d: %s", account.id, e)
        return

    if not platform_zones:
        return

    # GPS from warehouse (60s-fresh) with live-Samsara fallback
    try:
        from features.geofencing.service import get_fleet_for_geofence_check
        vehicles = await get_fleet_for_geofence_check(account.id)
    except Exception as e:
        logger.debug("Geofence GPS fetch for account %d: %s", account.id, e)
        return

    if not vehicles:
        return

    from capabilities.alerting import send_alert, AlertSeverity, is_vehicle_suppressed

    # All subscribers with alert_geofence=1 for this account
    all_subscribers = await get_platform_db().get_typed_alert_subscribers(
        account.id, "geofence"
    )

    for v in vehicles:
        loc = v.get("location", {})
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is None or lng is None:
            continue

        vid = v.get("id") or v.get("name", "")
        vname = v.get("name", "?")
        co = v.get("_org", "?")

        for zone in platform_zones:
            zone_id = zone["id"]
            zone_name = zone["name"]
            zone_type = zone.get("geofence_type", "custom")
            zone_roles = set(zone.get("notify_roles") or [])

            # ── 3-state machine: outside / approaching / inside ──────────────
            inside = _is_inside_geofence(lat, lng, zone)
            if inside:
                current_state = "inside"
            else:
                dist_m = _distance_to_geofence(lat, lng, zone)
                current_state = "approaching" if dist_m <= _NEARBY_THRESHOLD_M else "outside"

            state_key = f"t:{account.id}:geofence:{vid}:{zone_id}"

            # Read previous state from Redis (None = first poll, no history)
            prev_state = None
            if rcache and rcache.is_available():
                raw = await rcache.get(state_key)
                if raw is not None:
                    prev_state = raw.get("state")

            # Persist current state (24 h TTL)
            if rcache and rcache.is_available():
                await rcache.cache_set(state_key, {"state": current_state}, ttl=86400)

            # No alert on first poll or no change
            if prev_state is None or prev_state == current_state:
                continue

            # ── Determine event type ─────────────────────────────────────────
            # approaching→outside: vehicle drifted away — silent, no alert
            if prev_state == "approaching" and current_state == "outside":
                continue

            if current_state == "inside":
                event_word = "entered"
                emoji = "📍"
            elif current_state == "approaching":
                if prev_state == "inside":
                    # Truck crossed out of zone but is still close
                    event_word = "exited"
                    emoji = "📤"
                else:
                    # outside → approaching
                    event_word = "approaching"
                    emoji = "🔔"
            else:
                # current_state == "outside", prev was inside
                event_word = "exited"
                emoji = "📤"

            if await is_vehicle_suppressed(account.id, vname):
                continue

            # Auto-resolve alert history on exit
            if event_word == "exited":
                cleared = await tenant.clear_alert_history(account.id, "geofence", vid)
                for rec in cleared:
                    if rec.get("message_id") and rec.get("chat_id"):
                        try:
                            await bot_app.bot.delete_message(
                                chat_id=rec["chat_id"],
                                message_id=rec["message_id"],
                            )
                        except Exception as exc:
                            logger.debug("Could not delete geofence msg: %s", exc)

            # ── Build alert text ─────────────────────────────────────────────
            if event_word == "approaching":
                dist_m = _distance_to_geofence(lat, lng, zone)
                dist_mi = dist_m / 1609.344
                alert_text = (
                    f"{emoji} <b>Approaching Zone</b>\n\n"
                    f"  🚛 <b>{vname}</b> is {dist_mi:.1f} mi away from\n"
                    f"  📍 <b>{zone_name}</b> [{zone_type}]\n"
                )
            else:
                alert_text = (
                    f"{emoji} <b>Geofence Alert</b>\n\n"
                    f"  🚛 <b>{vname}</b> {event_word}\n"
                    f"  📍 <b>{zone_name}</b> [{zone_type}]\n"
                )

            # Maintenance cross-reference on entry (not on approaching)
            if event_word == "entered":
                try:
                    tasks = await tenant.get_maintenance_tasks(
                        account.id, vehicle_name=vname
                    )
                    due_tasks = [
                        t for t in tasks
                        if t.get("status") in ("pending", "overdue")
                    ]
                    if due_tasks:
                        task_lines = ", ".join(
                            t.get("task_type") or t.get("description") or "task"
                            for t in due_tasks[:5]
                        )
                        alert_text += f"\n  ⚠️ Pending maintenance: {task_lines}\n"
                except Exception as exc:
                    logger.debug("Maintenance cross-ref failed for %s: %s", vname, exc)

            # Role-based subscriber filtering
            filtered_subscribers = _filter_subscribers_for_zone(
                all_subscribers, zone_roles, vname
            )
            if not filtered_subscribers:
                continue

            vehicle_dict = {"id": vid, "name": vname, "_org": co}
            await send_alert(
                bot_app,
                account_id=account.id,
                alert_type="geofence",
                severity=AlertSeverity.INFO,
                vehicle=vehicle_dict,
                alert_text=alert_text,
                subscribers=filtered_subscribers,
                co=co,
                alert_key_detail=f"{event_word} {zone_name}",
                bot_app=bot_app,
            )


def _filter_subscribers_for_zone(
    subscribers: list,
    zone_roles: set,
    vehicle_name: str,
) -> list:
    """Return subscribers whose role is in zone_roles, with driver isolation.

    Drivers are included only if the vehicle matches their assigned truck_num.
    """
    result = []
    for sub in subscribers:
        role = getattr(sub, "role", "")
        if role not in zone_roles:
            continue
        # Driver isolation: assigned width — only own vehicle
        if role == "driver":
            truck = getattr(sub, "truck_num", None)
            if truck and truck != vehicle_name:
                continue
        result.append(sub)
    return result

