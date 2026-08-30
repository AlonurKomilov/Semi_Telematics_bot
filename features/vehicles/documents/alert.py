"""Daily — tell somebody before a truck's paper lapses.

The expiry date was captured at upload and then said nothing: an
expired insurance certificate rendered in the same grey as a current
one, and the first anyone learned of it was a roadside inspection.

Rides the EXISTING ``documents`` alert type — the one driver documents
already use.  Same category (a document is expiring), different
entity, so it inherits the topic, the subscription row and the
delivery routing rather than minting a parallel set of all three.

Archived trucks are excluded in the QUERY, not here: archiving a truck
stops every alert about it, and a document alert is an alert.  Declared
as ``filters`` in the alert-source stance ratchet.
"""

from __future__ import annotations

import logging

from telegram.ext import Application

from capabilities.alerting.pipeline import AlertSeverity, send_alert
from capabilities.alerting.registry import register_alert_source
from capabilities.localization.tz import is_local_hour
from features.vehicles.documents.expiration import classify, describe
from infra.bot_registry import get_app_for_account
from infra.services import get_tenant_db
from interfaces.bot.state import get_platform_db

logger = logging.getLogger(__name__)

# One delivery per account per day, at that account's own local 09:00 —
# paperwork is a morning-office task, not a 3am page.
_TARGET_HOUR_LOCAL = 9


def _severity(days_left: int) -> AlertSeverity:
    """Past due is CRITICAL because the truck is running on lapsed
    paper RIGHT NOW; the last week is a WARNING; the earlier notices
    are INFO, which respects do-not-disturb.  A T-30 registration
    notice has no business bypassing anyone's quiet hours."""
    if days_left < 0:
        return AlertSeverity.CRITICAL
    if days_left <= 7:
        return AlertSeverity.WARNING
    return AlertSeverity.INFO


@register_alert_source("vehicle_doc_expiry_check", trigger="cron", minute=4)
async def check_vehicle_document_expirations(
    _app: Application | None = None,
) -> None:
    """Fire per-truck document-expiry alerts, once per bucket.

    Buckets (T-30/14/7/1/0) and a claim-before-send ledger keep this
    from becoming a daily nag: a registration expiring in a month
    speaks five times over that month, not thirty.
    """
    try:
        accounts = await get_platform_db().list_accounts(active_only=True)
    except Exception:
        logger.error("vehicle doc expiry — cannot list accounts",
                     exc_info=True)
        return

    for acct in accounts:
        try:
            if not await is_local_hour(acct.id, _TARGET_HOUR_LOCAL):
                continue
            bot_app = get_app_for_account(acct.id)
            if bot_app is None:
                continue
            tenant = await get_tenant_db(acct.id)
            if tenant is None:
                continue

            rows = await tenant.get_expiring_vehicle_documents(acct.id)
            if not rows:
                continue
            due = classify(rows)
            if not due:
                continue

            subs = await tenant.get_typed_alert_subscribers(acct.id, "documents")
            if not subs:
                continue

            # Claim first, then group: a document whose bucket was
            # already claimed must not drag its truck into a second
            # message for the same reason.
            claimed = []
            for e in due:
                try:
                    if await tenant.record_vehicle_doc_notification(
                            e.doc_id, e.bucket):
                        claimed.append(e)
                except Exception:
                    logger.debug("could not claim doc=%s bucket=%s",
                                 e.doc_id, e.bucket, exc_info=True)
            if not claimed:
                continue

            # One message per TRUCK: three papers lapsing on unit 110 is
            # one thing to go and fix, not three notifications.
            by_vehicle: dict[int, list] = {}
            for e in claimed:
                by_vehicle.setdefault(e.vehicle_id, []).append(e)

            for vehicle_id, items in by_vehicle.items():
                items.sort(key=lambda x: x.days_left)
                worst = items[0]
                lines = "\n".join(f"• {describe(i)}" for i in items)
                text = (
                    f"📄 Documents expiring — unit {worst.unit_number}\n"
                    f"{lines}"
                )
                await send_alert(
                    bot_app,
                    account_id=acct.id,
                    alert_type="documents",
                    severity=_severity(worst.days_left),
                    vehicle={"id": vehicle_id, "name": worst.unit_number},
                    alert_text=text,
                    subscribers=subs,
                    co=worst.company_code,
                    alert_key_detail=f"docs:{worst.bucket}",
                    bot_app=bot_app,
                )
        except Exception:
            logger.error("vehicle doc expiry failed for account %s",
                         getattr(acct, "id", "?"), exc_info=True)
