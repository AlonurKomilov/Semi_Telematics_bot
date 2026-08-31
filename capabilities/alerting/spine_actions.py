"""Alerting's spine action handlers — the MEANING side of a button press.

Part of the alert-DM migration (capabilities/alerting/docs/alert-dm-migration.md):
the notifications spine renders the buttons and routes the press here;
THIS module decides what "acknowledge" means — the same storage cascade
the dashboard's bulk-ack runs — then asks the spine to update every
delivered copy of the message.

Correlation-key contract (what spine-delivered alert DMs send with):
one logical alert occurrence = one ``alert_history`` row, so

    correlation_key = f"alert:{history_id}"

Importing this module (done from ``capabilities.alerting`` at boot, like
``notification_categories``) performs the registration.  Nothing fires
until spine-delivered alert DMs ship — registering early just
means the wire is live the moment the first such DM ships.
"""

from __future__ import annotations

import logging

from capabilities.notifications import NotificationContent, update_delivery
from capabilities.notifications.actions import (
    ActionContext,
    register_action_handler,
)

logger = logging.getLogger(__name__)

CORRELATION_PREFIX = "alert"

# The button rows spine-delivered alert DMs carry (owner decision
# 2026-08-30, the Seen / Working-on / Resolved-by trio):
#
#   🔧 Work on it — the CLAIM.  The pager's job is finding an owner and
#      a claim is an owner, so this silences the re-page and stamps the
#      presser's name on every delivered copy, while the alert stays
#      open and the Done button stays live.
#   ✅ Done — the RESOLUTION.  The old acknowledge, wearing its honest
#      name: same storage cascade, same pager stop, and every historical
#      press already reads correctly as a resolution.
#
# ``ACK_ACTION`` keeps its variable name — the id "ack" is a wire value
# that delivered messages already carry, and renaming it would orphan
# every button already sitting in a chat.
WORK_ACTION = {"id": "work", "label": "🔧 Work on it"}
ACK_ACTION = {"id": "ack", "label": "✅ Done"}


def correlation_key_for_history(history_id: int) -> str:
    """The spine-routing address of one logical alert occurrence."""
    return f"{CORRELATION_PREFIX}:{int(history_id)}"


async def _handle_ack(ctx: ActionContext) -> str:
    """One press acknowledges the OCCURRENCE (alert_history row) — same
    semantics as the dashboard's bulk-ack — then edits every recorded
    delivery so all recipients see who took it."""
    try:
        history_id = int(ctx.correlation_key.split(":", 1)[1])
    except (IndexError, ValueError):
        return "Invalid alert"

    from infra.platform import get_tenant_db
    tenant = await get_tenant_db(ctx.account_id)
    try:
        # History-level ack first (cascades per-delivery rows); fall back
        # to a per-delivery ack id — the exact _ack_one order the
        # dashboard endpoint uses.
        cleared = await tenant.acknowledge_alert_history(
            history_id, ctx.presser_telegram_id, account_id=ctx.account_id)
        if not cleared:
            cleared = await tenant.acknowledge_alert(
                history_id, ctx.presser_telegram_id, account_id=ctx.account_id)
    except Exception:
        logger.exception("alert.ack failed for %s", ctx.correlation_key)
        return "Ack failed — try again"
    if not cleared:
        return "Already resolved"

    # Update every delivered copy (this DM and everyone else's) in place;
    # final edit for the occurrence → clear the ledger rows.
    who = ctx.presser_name or str(ctx.presser_telegram_id)
    body = f"{ctx.message_text}\n\n✅ Resolved by {who}" \
        if ctx.message_text else f"✅ Resolved by {who}"
    try:
        await update_delivery(
            tenant, ctx.account_id, ctx.correlation_key,
            NotificationContent(title="", body=body),
            clear=True,
        )
    except Exception:
        # The ack itself stands; a failed cosmetic edit must not undo it.
        logger.exception("alert.ack: delivery edit failed for %s",
                         ctx.correlation_key)
    return "Resolved ✅"


async def _handle_work(ctx: ActionContext) -> str:
    """One press claims the occurrence — "I'm working on this."

    Voluntary and additive (a second presser joins the first), stops the
    re-page because the pager's job was finding an owner, and touches
    none of the resolution columns — the invariant the board's claim
    tests pin.  The claim keys on users.id rather than the telegram id,
    same as every claim: it is about the person, not the address that
    delivered to them.
    """
    try:
        history_id = int(ctx.correlation_key.split(":", 1)[1])
    except (IndexError, ValueError):
        return "Invalid alert"

    from infra.platform import get_platform_db, get_tenant_db
    db_user = await get_platform_db().get_user_by_telegram_id(
        int(ctx.presser_telegram_id))
    if db_user is None or int(getattr(db_user, "account_id", 0)) != int(ctx.account_id):
        return "Couldn't identify you on this account"

    tenant = await get_tenant_db(ctx.account_id)
    try:
        claimed = await tenant.claim_alert(
            ctx.account_id, db_user.id, history_id)
    except Exception:
        logger.exception("alert.work failed for %s", ctx.correlation_key)
        return "Couldn't claim it — try again"
    if not claimed:
        return "Already on it"

    # Annotate every delivered copy so the second reader stands down —
    # WITHOUT clearing the ledger: the alert is owned, not over, and the
    # Done button must stay pressable.
    who = ctx.presser_name or str(ctx.presser_telegram_id)
    body = f"{ctx.message_text}\n\n🔧 {who} is working on this" \
        if ctx.message_text else f"🔧 {who} is working on this"
    try:
        await update_delivery(
            tenant, ctx.account_id, ctx.correlation_key,
            NotificationContent(title="", body=body),
            clear=False,
        )
    except Exception:
        logger.exception("alert.work: delivery edit failed for %s",
                         ctx.correlation_key)
    return "You're on it 🔧"


def register_alert_actions() -> None:
    register_action_handler(f"{CORRELATION_PREFIX}.ack", _handle_ack)
    register_action_handler(f"{CORRELATION_PREFIX}.work", _handle_work)


register_alert_actions()
