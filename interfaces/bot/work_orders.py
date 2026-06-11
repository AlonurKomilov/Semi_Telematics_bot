"""Bot /invoice flow — driver photographs a shop invoice in the field.

Workflow:
  1. Driver types ``/invoice``.  Bot identifies the truck (auto for
     drivers with a single assigned truck, picker for managers).
  2. Bot prompts "Send the invoice photo or PDF".  State stored in
     ``user_data["_invoice"]``.
  3. The next photo or document message hits ``handle_invoice_upload``
     — bytes downloaded from Telegram, saved to the object store via
     the same path helper the dashboard uses, draft work order created.
  4. Bot confirms to the driver and notifies account admins to fill in
     cost details from the dashboard.

The bot writes directly to the tenant DB + object store — no HTTP
roundtrip to the API — because the driver's identity is already
established via their telegram_id at this point.  Drivers are scoped
to their own truck via the same ``can_maintenance_vehicle`` permission used
elsewhere.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from adapters.storage import Role
from adapters.storage.object_store import get_object_store_for_account
from capabilities.iam.permissions import can
from features.maintenance.service import has_maintenance_access
from features.work_orders.storage import (
    resolve_company_folder, safe_attachment_name, work_order_folder,
)
from infra.bot_registry import get_app_for_account
from interfaces.bot.auth import _require_registered
from interfaces.bot.state import get_platform_db, get_tenant_db
from interfaces.bot.helpers import _show

logger = logging.getLogger(__name__)


# Telegram caps photo files at 20 MB and document files at the bot
# limit; we cap at the same 10 MB the dashboard route enforces so the
# two channels accept the same range of inputs.
_MAX_BYTES = 10 * 1024 * 1024
_ALLOWED_MIME = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif",
}


# ── Command entry point ──────────────────────────────────────────────────────


@_require_registered
async def cmd_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the invoice-photo flow.

    Drivers with one assigned truck skip straight to the upload prompt.
    Managers and multi-truck drivers see a truck-name input (kept simple
    on purpose — re-uses free-text rather than the full vehicle picker
    so the field-driver path stays fast).
    """
    user = context.user_data["_db_user"]
    if not has_maintenance_access(user.role):
        if update.callback_query:
            await update.callback_query.answer("⛔ No access", show_alert=True)
        return

    # Clear any unrelated wizard state — invoice flow is one-shot.
    context.user_data.pop("_maint", None)
    context.user_data.pop("_pending", None)

    if user.role == Role.DRIVER and user.truck_num:
        # Single-truck driver — skip picker entirely.
        context.user_data["_invoice"] = {"vehicle_name": user.truck_num}
        await _show(update, context, [
            f"📷 <b>Upload Invoice</b>\n\n"
            f"  🚛 Truck: <b>#{user.truck_num}</b>\n\n"
            f"Send a photo of the invoice — or attach the PDF.\n"
            f"A manager will fill in cost details from the dashboard."
        ])
        return

    # Manager / multi-truck — ask for the truck name as free text.
    # Keeps the bot side cheap; the full company/truck picker lives in
    # /maint_add for users who want it.
    context.user_data["_invoice"] = {"awaiting_truck": True}
    await _show(update, context, [
        "📷 <b>Upload Invoice</b>\n\n"
        "Type the truck number (e.g. <code>221</code>) — "
        "or use the dashboard's Work Orders page for fleet-wide entry."
    ])


# ── Message handler ──────────────────────────────────────────────────────────


@_require_registered
async def handle_invoice_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Catch-all handler invoked while ``_invoice`` wizard state is active.

    Branches on what the user sent:
      * plain text while ``awaiting_truck`` → store as truck name,
        prompt for the file
      * photo / document → download, validate, save, create draft

    Registered with a low priority in handler_setup.py so it only fires
    when other (more specific) handlers don't match.  Cleared from
    user_data after success/error so the next message goes through
    normal routing.
    """
    state = context.user_data.get("_invoice")
    if not state:
        return  # no active wizard, defer to other handlers

    msg = update.message
    if not msg:
        return

    # Text turn — collecting the truck number.
    if state.get("awaiting_truck") and msg.text:
        state["vehicle_name"] = msg.text.strip()
        state.pop("awaiting_truck", None)
        await _show(update, context, [
            f"🚛 Truck: <b>#{state['vehicle_name']}</b>\n\n"
            f"Send a photo of the invoice — or attach the PDF."
        ])
        return

    # File turn — photo or document.
    user = context.user_data["_db_user"]
    file_bytes, file_name, mime = await _extract_file(update, context)
    if file_bytes is None:
        # Not a file we recognise.  Stay in state — the user might be
        # just typing chatter; one more upload attempt will work.
        return

    vehicle = state.get("vehicle_name")
    if not vehicle:
        await _show(update, context, [
            "⚠️ Truck not set yet — type the truck number first."
        ])
        return

    if mime and mime not in _ALLOWED_MIME:
        await _show(update, context, [
            f"⚠️ Unsupported file type: <code>{mime}</code>\n"
            "Send a PDF, JPG, or PNG."
        ])
        return
    if len(file_bytes) > _MAX_BYTES:
        await _show(update, context, [
            f"⚠️ File too large ({len(file_bytes) // (1024 * 1024)} MB).  "
            "Max 10 MB.  Try a lower-res photo."
        ])
        return

    # Restrict drivers to their own truck so a misclick on a wrong
    # truck number doesn't pollute another vehicle's records.
    if not can(user.role, "can_maintenance_all"):
        allowed = [user.truck_num] if user.truck_num else []
        if not allowed or vehicle.lower() not in [t.lower() for t in allowed]:
            await _show(update, context, [
                f"⛔ You can only upload invoices for your assigned truck "
                f"(<code>{user.truck_num}</code>).",
            ])
            context.user_data.pop("_invoice", None)
            return

    tenant = await get_tenant_db(user.account_id)

    # Create the draft work order first so we have an id for the
    # folder path.  Cost fields stay zero — manager fills them in.
    work_order_id = await tenant.add_work_order(
        account_id=user.account_id,
        company_code="",
        vehicle_name=vehicle,
        vendor_name="",
        status="draft",
        payment_status="unpaid",
        notes=f"Invoice photo uploaded by {user.display_name or user.telegram_id} via bot.",
        created_by=int(user.telegram_id),
    )

    # Save the bytes through the object store using the same structured
    # path helper the dashboard uses.  When BYO Drive ships, this code
    # is unchanged — the factory swaps backends.
    # Bot uploads are drafts where company_code is not yet known —
    # falls through to the "unnamed-company" bucket; managers re-assign
    # via the dashboard when they fill in the work order details.
    company_folder = await resolve_company_folder(
        tenant, user.account_id, "",
    )
    folder = work_order_folder(
        company_folder=company_folder,
        work_order_id=work_order_id,
        vehicle_name=vehicle,
        service_date=None,
        vendor_name="",
    )
    safe_name = safe_attachment_name(file_name or "invoice.bin")
    store = await get_object_store_for_account(user.account_id, tenant)
    stored_path = store.put(folder, safe_name, file_bytes)

    await tenant.add_work_order_attachment(
        work_order_id,
        file_path=stored_path,
        file_name=safe_name,
        file_size=len(file_bytes),
        content_type=mime or "application/octet-stream",
        kind="invoice",
        uploaded_by=int(user.telegram_id),
    )
    await tenant.add_audit_log(
        user.account_id, int(user.telegram_id),
        "work_order_bot_upload",
        target_type="work_order", target_id=str(work_order_id),
        details=f"{vehicle}: {safe_name} ({len(file_bytes)} bytes)",
    )

    # Clear state so the next message routes normally.
    context.user_data.pop("_invoice", None)

    await _show(update, context, [
        f"✅ <b>Invoice received</b>\n\n"
        f"  🚛 Truck: <b>#{vehicle}</b>\n"
        f"  📄 Work Order: <b>#{work_order_id}</b>\n"
        f"  💾 File: {safe_name}\n\n"
        f"A manager will fill in vendor, parts, and total from the dashboard."
    ])

    # Notify admins so they know there's a draft waiting.
    await _notify_admins(
        user.account_id,
        text=(
            f"📷 <b>New invoice photo</b>\n\n"
            f"  🚛 Truck: <b>#{vehicle}</b>\n"
            f"  👤 Uploaded by: {user.display_name or user.telegram_id}\n"
            f"  📄 Work Order: <b>#{work_order_id}</b>\n\n"
            f"Open the Work Orders page to add cost details."
        ),
        exclude=int(user.telegram_id),
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _extract_file(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Pull file bytes from either a photo or document message.

    Returns (bytes, filename, mime) — all None when the message isn't a
    file (caller stays in wizard state and waits for another message).
    Uses ``getlargest`` for photos so the saved invoice is the
    highest-resolution version Telegram has.
    """
    msg = update.message
    if not msg:
        return (None, None, None)

    if msg.photo:
        # Telegram sends photos as a list of sizes (thumbnails + larger
        # versions).  The last entry is the largest, which is what we
        # want for a legible invoice scan.
        photo = msg.photo[-1]
        f = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await f.download_to_memory(buf)
        # Telegram doesn't expose the original filename for photos —
        # synthesise one with the file_id as a stable handle.
        return (buf.getvalue(), f"invoice_{photo.file_unique_id}.jpg", "image/jpeg")

    if msg.document:
        d = msg.document
        f = await context.bot.get_file(d.file_id)
        buf = io.BytesIO()
        await f.download_to_memory(buf)
        return (buf.getvalue(), d.file_name or "document.bin", (d.mime_type or "").lower())

    return (None, None, None)


async def _notify_admins(account_id: int, *, text: str, exclude: int = 0) -> None:
    """Send a notification to account admins/owners (mirrors the
    maintenance module's helper of the same name).  Best-effort — a
    failure to deliver to one admin doesn't block the others."""
    bot_app = get_app_for_account(account_id)
    if not bot_app:
        return
    try:
        users = await get_platform_db().list_account_users(account_id)
    except Exception as e:
        logger.warning("_notify_admins failed to list users for %d: %s", account_id, e)
        return
    for u in users:
        if u.telegram_id == exclude:
            continue
        if u.role in (Role.OWNER, Role.ADMIN):
            try:
                await bot_app.bot.send_message(
                    chat_id=u.telegram_id, text=text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.debug("Could not notify admin %d: %s", u.telegram_id, e)
