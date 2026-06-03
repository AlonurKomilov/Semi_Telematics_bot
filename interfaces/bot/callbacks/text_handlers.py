"""Text-input handler for pending interactive prompts."""


from adapters.storage import Role
from capabilities.iam.permissions import role_display
from capabilities.formatting import format_invite_created

from interfaces.bot.state import get_platform_db
from interfaces.bot.keyboards import back_kb, invite_kb
from interfaces.bot.helpers import _show, _safe_error, make_invite_link
from capabilities.localization.i18n import t
from interfaces.bot.auth import _get_user, _group_chat_guard
from interfaces.bot.registration import cmd_start, cmd_register, cmd_join
from interfaces.bot.management import cmd_groups
from interfaces.bot.fleet import cmd_vehicle
from interfaces.bot.fuel_costs import handle_fuelcost_text
# The maintenance CRUD wizard moved to the dashboard, so the
# bot-side ``handle_maintenance_text`` (date/miles/hours/desc text
# input router) is no longer reachable.  Import removed; the
# ``maint_*`` ``_pending`` dispatch branch below was deleted in
# the same commit.
from interfaces.bot.ai import cmd_ai_answer
# The add-zone wizard (handle_add_zone_text) was retired when
# geofence CRUD moved to the dashboard.  Any stale ``_add_zone_step``
# context state from a pre-deploy session is silently ignored — the
# user just types into the AI fallback like any other message.


async def handle_text(update, context):
    """Handle text replies for pending interactive prompts."""
    # Defense in depth: this handler is registered with
    # ``filters.ChatType.PRIVATE`` but if any future code path
    # invokes it directly with a group update, the AI fallback at
    # the bottom would burn tokens replying to alerts in forum
    # topics.  Bail early on any non-private chat.
    chat = update.effective_chat
    if chat and chat.type != "private":
        return
    # Silently ignore unauthorized group chats (kept for the legacy
    # entry points; with the chat-type guard above this is a no-op
    # in normal flow).
    if not await _group_chat_guard(update):
        return

    # Handle cancel from the group picker reply keyboard
    text_raw = (update.message.text or "").strip()
    if text_raw == "❌ Cancel" and context.user_data.pop("_awaiting_chat_pick", None):
        from telegram import ReplyKeyboardRemove
        await update.message.reply_text(
            "Cancelled.", reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.pop("_pending_group", None)
        await cmd_groups(update, context)
        return

    pending = context.user_data.pop("_pending", None)

    # Any stale ``_add_zone_step`` from before geofence CRUD moved
    # to the dashboard is silently cleared so a half-finished
    # wizard doesn't trap the user.
    context.user_data.pop("_add_zone_step", None)
    context.user_data.pop("_add_zone", None)

    if not pending:
        # No pending prompt — route to AI if user is registered & AI configured
        text_msg = (update.message.text or "").strip()
        if text_msg:
            import capabilities.ai as ai
            user = context.user_data.get("_db_user")
            if not user:
                user, _, _ = await _get_user(update)
            if user and ai.is_configured():
                context.user_data["_db_user"] = user
                await cmd_ai_answer(update, context, question=text_msg)
                return
        await cmd_start(update, context)
        return

    text = update.message.text.strip()
    if not text:
        await cmd_start(update, context)
        return

    # ── Register company ────────────────────────────────────────
    if pending == "register":
        context.args = text.split()
        await cmd_register(update, context)

    # ── Join with invite code ───────────────────────────────────
    elif pending == "join":
        context.args = [text.replace(" ", "")]
        await cmd_join(update, context)

    # ── Truck lookup ────────────────────────────────────────────
    elif pending == "truck":
        context.args = text.split()
        await cmd_vehicle(update, context)

    # NOTE: per-user and per-company text-wizard branches (addcompany_*,
    # change_dept, confirm_remove_company, change_api_key,
    # rename_company) were removed when the corresponding bot grids
    # moved to the dashboard.  Stale ``_pending`` values from cached
    # menus fall through to the AI fallback, which is benign.
    #
    # ``change_api_key`` is intentionally not restored: pasting an
    # API key into a Telegram chat leaves it in chat history +
    # Telegram server logs.  The dashboard sends the key over TLS
    # straight to the API and stores it encrypted.

    # ── Invite driver (vehicle number — required) ───────────────
    elif pending == "invite_driver":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        context.user_data["_db_user"] = user

        # Drivers MUST have a vehicle — without one the alert pipeline
        # has nothing to filter against, and the new
        # ``driver_vehicle_assignments`` row can't be seeded.  Reject
        # ``/skip`` and any blank entry, re-prompting the admin.
        vehicle_num = text.strip()
        if not vehicle_num or vehicle_num.lower() in ("skip", "/skip"):
            # Stay in ``invite_driver`` state so the next text retries.
            await _show(update, context, [
                t('invite.driver_vehicle_required')
            ], keyboard=back_kb())
            return

        try:
            invite = await get_platform_db().create_invite(
                account_id=user.account_id,
                created_by=user.id,
                role=Role.DRIVER,
                department="operations",
                truck_num=vehicle_num,
            )
            link = make_invite_link(invite.code, context)
            invite_text = format_invite_created(
                invite.code, role_display(Role.DRIVER),
                "operations",
                invite_link=link,
            )
            invite_text += "\n  " + t('invite.vehicle_assigned', vehicle=vehicle_num)

            # Samsara cross-reference suggestion — best-effort, fire-and-
            # forget.  Looking up who currently drives this truck in
            # Samsara gives the admin a head-start on linking
            # ``samsara_driver_id`` from the dashboard.  Soft-fail when
            # the fleet API is unreachable or the truck isn't assigned.
            hint = await _samsara_driver_hint(user.account_id, vehicle_num)
            if hint:
                invite_text += "\n\n" + hint

            kb = invite_kb(link)
            # Clear the pending state — invite created successfully.
            context.user_data.pop("_pending", None)
            await _show(update, context, [invite_text], keyboard=kb)
        except Exception as e:
            await _show(update, context, [_safe_error(e)], keyboard=back_kb())

    # ── Fuel cost wizard ────────────────────────────────────────
    elif pending.startswith("fuelcost_"):
        context.user_data["_pending"] = pending  # restore for handler
        await handle_fuelcost_text(update, context)

    # NOTE: ``maint_*`` (maintenance task wizard), ``whours_*`` (working
    # hours wizard) and ``route_vehicle`` (route replay truck picker)
    # text-input branches were removed when those flows moved to the
    # dashboard.  Stale ``_pending`` values from cached chats fall
    # through to the AI fallback below.

    # ── AI question input ───────────────────────────────────────
    elif pending == "ai_question":
        await cmd_ai_answer(update, context, question=text)

    # ── Knowledge base search ───────────────────────────────────
    elif pending == "kb_search":
        context.user_data["_pending"] = pending  # restore for handler
        from interfaces.bot.knowledge import handle_kb_search_input
        await handle_kb_search_input(update, context)

    else:
        await cmd_start(update, context)


async def _samsara_driver_hint(account_id: int, vehicle_num: str) -> str | None:
    """If Samsara has a static driver assigned to ``vehicle_num``,
    return a one-liner hint the admin can use to confirm the link
    target.  Returns ``None`` on any failure (unreachable Samsara,
    no assignment, malformed response) — this is purely additive UX
    and must not block the invite-created message.
    """
    try:
        from infra.services import get_client as _get_samsara
        samsara = await _get_samsara(account_id)
    except Exception:
        return None

    needle = vehicle_num.strip().lower()
    # Try each company's static-assignment map; first hit wins.
    try:
        for code, single in samsara.clients.items():
            try:
                mapping = await single.get_static_driver_assignments()
            except Exception:
                continue
            driver_name = mapping.get(needle)
            if driver_name:
                return (
                    f"💡 <b>Samsara link suggestion</b>\n"
                    f"  Truck <b>#{vehicle_num}</b> is currently driven by "
                    f"<b>{driver_name}</b> ({code}) in Samsara.\n"
                    f"  Open the Drivers page after the invite is accepted "
                    f"to link the Samsara ID."
                )
    except Exception:
        return None
    return None
