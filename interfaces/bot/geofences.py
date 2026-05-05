"""Geofence Alerts — list geofences and poll for entry/exit events."""

from datetime import datetime as _dt
from constants import TZ_ET as _TZ_ET
from capabilities.localization.i18n import t
from capabilities.geofencing.geometry import is_inside_geofence as _is_inside_geofence, geofence_shape_type as _geofence_shape_type, distance_to_geofence as _distance_to_geofence
from capabilities.geofencing.service import get_platform_geofences

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application

from capabilities.iam.permissions import can
from adapters.samsara.client import populate_company_display

from interfaces.bot.config import logger, get_platform_db, get_tenant_db
from infra.isolation import run_account_job
from infra.bot_registry import get_app_for_account
from interfaces.bot.keyboards import back_kb, geofence_list_kb
from interfaces.bot.helpers import _show, _show_loading, _safe_error
from interfaces.bot.auth import _require_registered

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
    if not (can(user.role, "can_geofence_all") or can(user.role, "can_geofence_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    companies = await tenant.get_account_companies(user.account_id)
    populate_company_display(companies)

    await _show_loading(update, context, t('geofence.loading'))

    try:
        from capabilities.geofencing.service import get_geofences as _svc_geofences
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


# ── Zone management commands ──────────────────────────────────────────────────

@_require_registered
async def cmd_add_zone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the conversation to create a platform geofence zone."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_geofence_all"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    context.user_data["_add_zone"] = {}
    await _show(update, context, [
        "📍 <b>Create Zone</b>\n\n"
        "Step 1/5 — Enter a <b>name</b> for this zone:\n"
        "<i>(e.g. Main Yard, Central Oil Shop, Customer Site A)</i>"
    ], keyboard=back_kb())
    context.user_data["_add_zone_step"] = "name"


@_require_registered
async def cmd_list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List platform-owned zones for this account."""
    user = context.user_data["_db_user"]
    if not (can(user.role, "can_geofence_all") or can(user.role, "can_geofence_own")):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    zones = await tenant.get_platform_geofences(user.account_id, is_active=True)

    if not zones:
        await _show(update, context, [
            "📍 <b>Platform Zones</b>\n\nNo zones created yet.\n"
            "Use /add_zone to create your first zone."
        ], keyboard=back_kb())
        return

    sep = "─" * 28
    lines = [f"📍 <b>Platform Zones</b>  ({len(zones)})\n{sep}"]
    for z in zones:
        shape = "⭕ Circle" if z["shape_type"] == "circle" else "🔷 Polygon"
        ztype = z.get("geofence_type", "custom")
        roles = ", ".join(z.get("notify_roles") or [])
        lines.append(
            f"\n<b>{z['name']}</b> [{ztype}]\n"
            f"  Shape: {shape}\n"
            f"  Notify: {roles}"
        )

    await _show(update, context, ["\n".join(lines)], keyboard=back_kb())


@_require_registered
async def cmd_delete_zone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show platform zones for deletion."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_geofence_all"):
        if update.callback_query:
            await update.callback_query.answer(t("access.no_access"), show_alert=True)
        return

    tenant = await get_tenant_db(user.account_id)
    zones = await tenant.get_platform_geofences(user.account_id, is_active=True)

    if not zones:
        await _show(update, context, [
            "No platform zones to delete."
        ], keyboard=back_kb())
        return

    buttons = [
        [InlineKeyboardButton(
            f"🗑 {z['name']} [{z.get('geofence_type','custom')}]",
            callback_data=f"del_zone:{z['id']}"
        )]
        for z in zones
    ]
    buttons.append([InlineKeyboardButton("← Back", callback_data="back")])
    kb = InlineKeyboardMarkup(buttons)
    await _show(update, context, [
        "📍 <b>Delete Zone</b>\n\nSelect a zone to deactivate:"
    ], keyboard=kb)


async def handle_delete_zone_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle del_zone:<id> callback."""
    query = update.callback_query
    await query.answer()
    user = context.user_data.get("_db_user")
    if not user or not can(user.role, "can_geofence_all"):
        return

    zone_id = int(query.data.split(":")[1])
    tenant = await get_tenant_db(user.account_id)
    zone = await tenant.get_platform_geofence_by_id(user.account_id, zone_id)
    if not zone:
        await query.edit_message_text("Zone not found.")
        return

    await tenant.delete_platform_geofence(user.account_id, zone_id)
    await query.edit_message_text(
        f"✅ Zone <b>{zone['name']}</b> has been deactivated.",
        parse_mode="HTML",
    )


async def handle_add_zone_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Handle text input during the add_zone conversation.

    Returns True if a step was consumed (caller should not process further).
    """
    step = context.user_data.get("_add_zone_step")
    if not step:
        return False

    user = context.user_data.get("_db_user")
    if not user or not can(user.role, "can_geofence_all"):
        return False

    text = (update.message.text or "").strip()
    zone_data: dict = context.user_data.get("_add_zone", {})

    if step == "name":
        if not text:
            await update.message.reply_text("Name cannot be empty. Please enter a name:")
            return True
        zone_data["name"] = text
        context.user_data["_add_zone"] = zone_data
        context.user_data["_add_zone_step"] = "type"
        await update.message.reply_html(
            "Step 2/5 — Enter a <b>zone type</b>:\n"
            "<i>(free text — e.g. maintenance_shop, dispatch_yard, customer_site, rest_stop)</i>"
        )

    elif step == "type":
        zone_data["geofence_type"] = text or "custom"
        context.user_data["_add_zone"] = zone_data
        context.user_data["_add_zone_step"] = "center"
        await update.message.reply_html(
            "Step 3/5 — Send the <b>center coordinates</b>:\n"
            "<i>Format: lat,lng  (e.g. 37.7749,-122.4194)\n"
            "Or share a Telegram location.</i>"
        )

    elif step == "center":
        # Accept "lat,lng" text or Telegram location
        if update.message.location:
            zone_data["latitude"] = update.message.location.latitude
            zone_data["longitude"] = update.message.location.longitude
        else:
            parts = text.replace(" ", "").split(",")
            if len(parts) != 2:
                await update.message.reply_text(
                    "Invalid format. Enter lat,lng (e.g. 37.7749,-122.4194):"
                )
                return True
            try:
                zone_data["latitude"] = float(parts[0])
                zone_data["longitude"] = float(parts[1])
            except ValueError:
                await update.message.reply_text(
                    "Invalid numbers. Enter lat,lng (e.g. 37.7749,-122.4194):"
                )
                return True
        context.user_data["_add_zone"] = zone_data
        context.user_data["_add_zone_step"] = "radius"
        await update.message.reply_html(
            "Step 4/5 — Enter the <b>radius in miles</b>:\n"
            "<i>(e.g. 0.25 for a quarter mile, 1 for one mile)</i>"
        )

    elif step == "radius":
        try:
            miles = float(text)
            zone_data["radius_meters"] = round(miles * 1609.344)  # miles → meters
        except ValueError:
            await update.message.reply_text("Enter a number in miles (e.g. 0.25):")
            return True
        context.user_data["_add_zone"] = zone_data
        context.user_data["_add_zone_step"] = "roles"
        all_roles = ["owner", "admin", "fleet", "safety", "dispatcher", "driver"]
        buttons = [
            [InlineKeyboardButton("✅ All roles (default)", callback_data="zone_roles:all")],
            *[
                [InlineKeyboardButton(r.capitalize(), callback_data=f"zone_roles:{r}")]
                for r in all_roles
            ],
            [InlineKeyboardButton("✓ Done selecting roles", callback_data="zone_roles:done")],
        ]
        await update.message.reply_html(
            "Step 5/5 — Who should be <b>notified</b> when a vehicle enters/exits?\n"
            "Tap roles to toggle, then tap Done.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    return True


async def handle_add_zone_roles_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle zone_roles:<role|all|done> callback during add_zone flow."""
    query = update.callback_query
    await query.answer()
    user = context.user_data.get("_db_user")
    if not user:
        return

    action = query.data.split(":")[1]
    zone_data: dict = context.user_data.get("_add_zone", {})
    all_roles = ["owner", "admin", "fleet", "safety", "dispatcher", "driver"]

    if action == "all":
        zone_data["notify_roles"] = list(all_roles)
        context.user_data["_add_zone"] = zone_data
        await _finish_add_zone(query, context, user, zone_data)

    elif action == "done":
        selected = zone_data.get("notify_roles") or list(all_roles)
        zone_data["notify_roles"] = selected
        context.user_data["_add_zone"] = zone_data
        await _finish_add_zone(query, context, user, zone_data)

    else:
        # Toggle individual role
        selected = zone_data.get("notify_roles") or []
        if action in selected:
            selected.remove(action)
        else:
            selected.append(action)
        zone_data["notify_roles"] = selected
        context.user_data["_add_zone"] = zone_data
        # Update button labels
        buttons = [
            [InlineKeyboardButton("✅ All roles", callback_data="zone_roles:all")],
            *[
                [InlineKeyboardButton(
                    f"{'✅' if r in selected else '◻️'} {r.capitalize()}",
                    callback_data=f"zone_roles:{r}"
                )]
                for r in all_roles
            ],
            [InlineKeyboardButton("✓ Done selecting roles", callback_data="zone_roles:done")],
        ]
        await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))


async def _finish_add_zone(query, context: ContextTypes.DEFAULT_TYPE, user, zone_data: dict):
    """Persist the zone and confirm to the user."""
    tenant = await get_tenant_db(user.account_id)
    try:
        zone_id = await tenant.add_platform_geofence(
            account_id=user.account_id,
            name=zone_data["name"],
            shape_type="circle",
            geofence_type=zone_data.get("geofence_type", "custom"),
            latitude=zone_data.get("latitude"),
            longitude=zone_data.get("longitude"),
            radius_meters=zone_data.get("radius_meters"),
            notify_roles=zone_data.get("notify_roles"),
            created_by=user.id,
        )
        radius_m = zone_data.get('radius_meters') or 0
        radius_mi = radius_m / 1609.344
        roles = ", ".join(zone_data.get("notify_roles") or [])
        await query.edit_message_text(
            f"✅ <b>Zone created</b> (id {zone_id})\n\n"
            f"  Name: <b>{zone_data['name']}</b>\n"
            f"  Type: {zone_data.get('geofence_type','custom')}\n"
            f"  Center: {zone_data.get('latitude'):.5f}, {zone_data.get('longitude'):.5f}\n"
            f"  Radius: {radius_mi:.2f} mi ({radius_m:,} m)\n"
            f"  Notify: {roles}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to create zone: %s", e)
        await query.edit_message_text(f"❌ Failed to create zone: {e}")
    finally:
        context.user_data.pop("_add_zone", None)
        context.user_data.pop("_add_zone_step", None)


# ── Scheduled poller ──────────────────────────────────────────────────────────

# Distance from zone boundary that counts as "approaching" (1 mile)
_NEARBY_THRESHOLD_M: float = 1_609.344

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
            await run_account_job(
                _check_geofences_account(bot_app, account),
                account_id=account.id,
                job_name="geofence_check",
            )

    except Exception as e:
        logger.error(f"Geofence check error: {e}")


async def _check_geofences_account(bot_app: Application, account):
    """Process platform geofence events for a single account.

    - Zone definitions: platform DB (not Samsara)
    - Vehicle GPS: Samsara get_fleet_overview() — lat/lng only
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
        from capabilities.geofencing.service import get_fleet_for_geofence_check
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
        # Driver isolation: can_geofence_own — only own vehicle
        if role == "driver":
            truck = getattr(sub, "truck_num", None)
            if truck and truck != vehicle_name:
                continue
        result.append(sub)
    return result

