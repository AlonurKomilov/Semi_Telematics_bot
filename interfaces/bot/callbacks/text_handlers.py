"""Text-input handler for pending interactive prompts."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from adapters.storage import Role
from capabilities.iam.permissions import role_display
from adapters.samsara.client import populate_company_display
from capabilities.formatting import format_invite_created

from interfaces.bot.config import logger
from interfaces.bot.state import get_platform_db, get_tenant_db, invalidate_client
from interfaces.bot.keyboards import back_kb, invite_kb
from interfaces.bot.helpers import _show, _safe_error, make_invite_link
from capabilities.localization.i18n import t
from interfaces.bot.auth import _get_user, _group_chat_guard
from interfaces.bot.registration import cmd_start, cmd_register, cmd_join
from interfaces.bot.management import cmd_addcompany, cmd_groups
from interfaces.bot.fleet import cmd_vehicle
from interfaces.bot.fuel_costs import handle_fuelcost_text
from interfaces.bot.maintenance import handle_maintenance_text
from interfaces.bot.routes import handle_route_text
from interfaces.bot.ai import cmd_ai_answer
from interfaces.bot.geofences import handle_add_zone_text


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

    # ── Add-zone multi-step conversation ────────────────────────
    if context.user_data.get("_add_zone_step"):
        await handle_add_zone_text(update, context)
        return

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

    # ── Add Company wizard step 1: company code ─────────────────────────
    elif pending == "addcompany_code":
        code = text.strip().upper()
        if not code or len(code) > 10 or " " in code:
            await _show(update, context, [
                t('company.add_code_invalid') + "\n"
                + t('company.add_code_example')
            ], keyboard=back_kb())
            return
        context.user_data["_addcompany"] = {"code": code}
        context.user_data["_pending"] = "addcompany_key"
        await _show(update, context, [
            t('company.add_wizard_title', code=code) + "\n\n"
            + t('company.add_step2') + "\n\n"
            + t('company.add_step2_prompt') + "\n"
            + t('company.add_step2_hint')
        ], keyboard=back_kb())

    # ── Add Company wizard step 2: api key ──────────────────────────
    elif pending == "addcompany_key":
        api_key = text.strip()
        # Security: delete the message containing the API key from chat
        try:
            await update.message.delete()
        except Exception as e:
            logger.debug("Could not delete API key message: %s", e)
        if not api_key or len(api_key) < 10:
            await _show(update, context, [
                t('company.chkey_invalid')
            ], keyboard=back_kb())
            return
        from interfaces.bot.keyboards import skip_name_kb
        wiz = context.user_data.get("_addcompany", {})
        wiz["api_key"] = api_key
        context.user_data["_addcompany"] = wiz
        context.user_data["_pending"] = "addcompany_name"
        await _show(update, context, [
            t('company.add_wizard_title', code=wiz.get('code', '?')) + "\n\n"
            + t('company.add_step3') + "\n\n"
            + t('company.add_step3_prompt') + "\n\n"
            + t('company.add_step3_example')
        ], keyboard=skip_name_kb())

    # ── Add Company wizard step 3: display name → create ────────────
    elif pending == "addcompany_name":
        wiz = context.user_data.pop("_addcompany", {})
        code = wiz.get("code", "")
        api_key = wiz.get("api_key", "")
        display_name = code if text.lower().strip() == "skip" else text.strip()
        if not code or not api_key:
            await _show(update, context, [t('company.wizard_lost')], keyboard=back_kb())
            return
        # Delegate to cmd_addcompany with synthesized args
        context.args = [f"{code}:{api_key}"] + (display_name.split() if display_name != code else [])
        await cmd_addcompany(update, context)

    # ── Change department for a user ──────────────────────────────
    elif pending == "change_dept":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        target_tid = context.user_data.pop("_dept_tid", None)
        if not target_tid:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        new_dept = text.strip()
        if not new_dept or len(new_dept) > 50:
            await _show(update, context, [
                t('user_mgmt.dept_invalid')
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"usrmenu_{target_tid}")],
            ]))
            return

        target_user = await get_platform_db().get_user_by_telegram_id(target_tid)
        if not target_user or target_user.account_id != user.account_id:
            await _show(update, context, [t('user_mgmt.user_not_found')], keyboard=back_kb())
            return

        await get_platform_db().update_user(target_user.id, department=new_dept)
        await _show(update, context, [
            t('user_mgmt.dept_updated', user=target_user.label, dept=new_dept)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.back'), callback_data=f"usrmenu_{target_tid}")],
        ]))

    # ── Confirm remove company (type code to confirm) ───────────
    elif pending == "confirm_remove_company":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        code = context.user_data.pop("_rmco_code", None)
        if not code:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        typed = text.strip().upper()
        if typed != code:
            await _show(update, context, [
                t('company.remove_mismatch', typed=typed, expected=code)
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
            ]))
            return

        tenant = await get_tenant_db(user.account_id)
        company = await tenant.get_company_by_code(user.account_id, code)
        if company:
            await tenant.remove_company(company.id, account_id=user.account_id)
            await invalidate_client(user.account_id)
            companies = await tenant.get_account_companies(user.account_id)
            populate_company_display(companies)
            # Audit log
            await tenant.add_audit_log(
                account_id=user.account_id,
                user_id=user.id,
                action="company_removed",
                target_type="company",
                target_id=str(company.id),
                details=f"{user.label} removed company {code} ({company.display_name})",
            )
            await _show(update, context, [
                t('company.remove_confirmed', code=code)
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('company.back_companies'), callback_data="cmd_account")],
            ]))
        else:
            await _show(update, context, [
                t('company.not_found_code', code=code)
            ], keyboard=back_kb())

    # ── Change API key for a company ──────────────────────────────
    elif pending == "change_api_key":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        code = context.user_data.pop("_chkey_code", None)
        if not code:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        api_key = text.strip()
        # Security: delete the message containing the API key from chat
        try:
            await update.message.delete()
        except Exception as e:
            logger.debug("Could not delete API key message: %s", e)
        if not api_key or len(api_key) < 10:
            await _show(update, context, [
                t('company.chkey_invalid')
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
            ]))
            return

        tenant = await get_tenant_db(user.account_id)
        company = await tenant.get_company_by_code(user.account_id, code)
        if not company:
            await _show(update, context, [t('company.not_found')], keyboard=back_kb())
            return

        await tenant.update_company(company.id, account_id=user.account_id, samsara_api_key=api_key)
        await invalidate_client(user.account_id)
        await tenant.add_audit_log(
            account_id=user.account_id, user_id=user.id,
            action="api_key_changed", target_type="company",
            target_id=str(company.id),
            details=f"{user.label} changed API key for {code}",
        )
        await _show(update, context, [
            t('company.chkey_updated', code=code)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
        ]))

    # ── Rename company display name ─────────────────────────────
    elif pending == "rename_company":
        user = context.user_data.get("_db_user")
        if not user:
            user, _, _ = await _get_user(update)
        if not user:
            await cmd_start(update, context)
            return
        code = context.user_data.pop("_rename_code", None)
        if not code:
            await _show(update, context, [t('common.session_expired')], keyboard=back_kb())
            return

        new_name = text.strip()
        if not new_name or len(new_name) > 100:
            await _show(update, context, [
                t('company.rename_invalid')
            ], keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
            ]))
            return

        tenant = await get_tenant_db(user.account_id)
        company = await tenant.get_company_by_code(user.account_id, code)
        if not company:
            await _show(update, context, [t('company.not_found')], keyboard=back_kb())
            return

        old_name = company.display_name
        await tenant.update_company(company.id, display_name=new_name)
        # Refresh display cache
        companies = await tenant.get_account_companies(user.account_id)
        populate_company_display(companies)
        await tenant.add_audit_log(
            account_id=user.account_id, user_id=user.id,
            action="company_renamed", target_type="company",
            target_id=str(company.id),
            details=f"{user.label} renamed {code}: {old_name} → {new_name}",
        )
        await _show(update, context, [
            t('company.renamed', code=code, name=new_name)
        ], keyboard=InlineKeyboardMarkup([
            [InlineKeyboardButton(t('common.back'), callback_data=f"comenu_{code}")],
        ]))

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

    # ── Maintenance wizard ──────────────────────────────────────
    elif pending.startswith("maint_"):
        context.user_data["_pending"] = pending  # restore for handler
        await handle_maintenance_text(update, context)

    # ── Work schedule wizard ────────────────────────────────────
    elif pending.startswith("whours_"):
        context.user_data["_pending"] = pending  # restore for handler
        from interfaces.bot.work_hours import handle_whours_text
        await handle_whours_text(update, context)

    # ── Route replay truck input ────────────────────────────────
    elif pending == "route_vehicle":
        context.user_data["_pending"] = pending  # restore for handler
        await handle_route_text(update, context)

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
