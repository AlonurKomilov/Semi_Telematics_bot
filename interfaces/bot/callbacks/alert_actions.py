"""Alert message callback handlers — the ✅ Acknowledge press on
LEGACY-keyboard messages (``ack_alert_<id>``) and the 🔎 back-to-alert
restore on reminder edits.

Moved from capabilities/alerting/escalation.py: these manipulate
Telegram Update/Message objects directly, which is interface-layer
work — the alerting capability no longer imports the transport
(docs/architecture/alert-dm-migration.md, the wall).  New spine posts
carry ``notif_act:`` buttons routed by capabilities/notifications/
actions.py instead; these handlers serve messages sent before that.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from capabilities.alerting.pipeline import AlertSeverity, build_alert_button_specs


def _specs_to_markup(rows: list) -> InlineKeyboardMarkup:
    """Generic button specs → PTB markup (the interface layer's job)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b["text"],
                              callback_data=b.get("callback_data"),
                              url=b.get("url"))
         for b in row] for row in rows
    ])
from infra.context import get_company_display
from infra.services import get_db, get_tenant_db

logger = logging.getLogger("bot")


async def handle_alert_ack(update, context, ack_id: int):
    """Handle the ✅ Acknowledge button press on a critical alert."""
    query = update.callback_query
    try:
        user = context.user_data.get("_db_user")
        tid = query.from_user.id
        acct_id = user.account_id if user else 0
        tenant = await get_tenant_db(acct_id) if acct_id else get_db()
        await tenant.acknowledge_alert(ack_id, tid)

        # Update the message to show it's been acknowledged.  The
        # Option A grammar appends a chip suffix to the existing 🔖 id
        # line so the alert stays a single coherent block instead of
        # gaining a trailing "Acknowledged by …" stanza on its own.
        # Falls back to appending a new line when no id chip is found
        # (legacy alerts, manually-edited messages, …).
        try:
            original_text = query.message.text_html or query.message.text or ""
            ack_name = query.from_user.full_name or str(tid)
            ack_chip = (
                f"  ·  ✅ Acked by "
                f"<a href='tg://user?id={tid}'>{ack_name}</a>"
            )
            lines = original_text.split("\n")
            patched = False
            for i, ln in enumerate(lines):
                if ln.lstrip().startswith("🔖"):
                    lines[i] = ln + ack_chip
                    patched = True
                    break
            ack_text = "\n".join(lines) if patched else (
                original_text + f"\n\n✅ <b>Acknowledged</b> by "
                f"<a href='tg://user?id={tid}'>{ack_name}</a>"
            )
            # Keep only the truck view button
            new_kb = InlineKeyboardMarkup([
                row for row in (query.message.reply_markup.inline_keyboard
                                if query.message.reply_markup else [])
                if any("ack_alert" not in (b.callback_data or "") for b in row)
            ])
            await query.edit_message_text(
                text=ack_text,
                parse_mode=ParseMode.HTML,
                reply_markup=new_kb if new_kb.inline_keyboard else None,
            )
        except Exception:
            logger.debug("Failed to edit ack message for alert %d", ack_id)
        await query.answer("✅ Alert acknowledged!", show_alert=False)

        # Trail — the ack came from a human tapping the bot button;
        # ``user.id`` is already the platform users.id.
        if user:
            from capabilities.activity_trail import record_simple
            await record_simple(
                tenant, user.account_id, user.id,
                "alert_acknowledged", "alert", ack_id,
                context={"via": "telegram"},
            )
    except Exception as e:
        logger.error("ACK alert %d failed: %s", ack_id, e, exc_info=True)
        await query.answer("Error acknowledging alert", show_alert=True)




async def handle_back_to_alert(update, context, ack_id: int):
    """Re-render the alert summary + keyboard when user presses Back from AI Diagnose, etc."""
    query = update.callback_query
    await query.answer()
    try:
        user = context.user_data.get("_db_user")
        acct_id = user.account_id if user else 0
        tenant = await get_tenant_db(acct_id) if acct_id else get_db()
        row = await tenant.get_alert_ack_by_id(ack_id)
        if not row:
            await query.edit_message_text("Alert not found.", parse_mode=ParseMode.HTML)
            return

        alert_key = row.get("alert_key", "")
        parts = alert_key.split(":", 2)
        co = parts[0] if parts else "?"
        vname = row.get("vehicle_name", "?")
        alert_type = row.get("alert_type", "fault")
        detail = parts[2] if len(parts) > 2 else ""
        co_display = get_company_display().get(co, co)

        # Determine severity
        severity = (AlertSeverity.CRITICAL if alert_type == "health"
                    else AlertSeverity.WARNING)

        # Status line
        acked = row.get("acknowledged_at")
        status = row.get("status", "active")
        if acked:
            status_line = "  ✅ <b>Acknowledged</b>"
        elif status == "expired":
            status_line = "  ⏳ <b>Expired</b>"
        else:
            status_line = "  🔴 <b>Unacknowledged</b>"

        # Build alert type header/icon
        type_icons = {
            "fault": "⚙️", "health": "🩺", "fuel": "⛽",
            "events": "🚨", "parking": "🅿️",
        }
        icon = type_icons.get(alert_type, "🔔")

        # Build detail lines from alert_key
        detail_lines = ""
        if alert_type == "fault" and detail:
            for item in detail.split("|")[:3]:
                spn_fmi, _, desc = item.partition(":")
                detail_lines += f"\n  {icon} {spn_fmi}"
                if desc:
                    detail_lines += f"\n     {desc}"
        elif detail:
            detail_lines = f"\n  {icon} {detail}"

        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  🔔  <b>{alert_type.upper()} ALERT</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛 Truck: <b>#{vname}</b>  ({co_display})"
            f"{detail_lines}\n"
            f"\n{status_line}"
        )

        kb = _specs_to_markup(build_alert_button_specs(
            severity, co, vname, ack_id=ack_id,
            alert_type=alert_type,
            vehicle_id=row.get("vehicle_id", ""),
            lang=getattr(user, "language", None) or "en",
        ))

        await query.edit_message_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"Back to alert {ack_id}: {e}")


# ── Re-escalation of unacknowledged CRITICAL alerts ──────────────

