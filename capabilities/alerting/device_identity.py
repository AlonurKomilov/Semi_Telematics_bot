"""Device-identity notices — the identity watch's tenant-side delivery.

The Samsara ingest watches the three identity anchors behind each
provider vehicle id (VIN, gateway serial, odometer scale) and records
changes in ``warehouse.device_event_log``.  A changed anchor is the
ACCOUNT's fleet event — their shop swapped a camera, their truck's
odometer source moved — so delivery targets the account's admins
through the notifications capability like every other alert, never a
platform-operator channel (tenant data must not cross that wall).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KIND_WORDS = {
    "vin_change": "VIN changed — is a different truck behind this unit?",
    "gateway_swap": "telematics gateway swapped",
    "odo_rebase": "odometer changed scale — mileage history rebased",
}

# One tick's worth of hardware work can touch several trucks; cap the
# notice burst so a fleet-wide provider hiccup can't flood anyone.
_MAX_NOTICES_PER_TICK = 5


async def notify_device_identity_events(
    account_id: int, events: list[dict]
) -> None:
    """Tell the account's admins about identity-anchor changes.

    Best-effort: a delivery failure is logged, never raised — the
    ingest that detected the events must not be poisoned by it.
    """
    if not events:
        return
    from capabilities.notifications import NotificationContent, notify_user
    from infra.platform import get_platform_db

    db = get_platform_db()
    admins = await db.get_account_admins(account_id)
    if not admins:
        return

    for e in events[:_MAX_NOTICES_PER_TICK]:
        body = (
            f"🔧 {e.get('vehicle_name') or e.get('vehicle_id', '?')}: "
            f"{_KIND_WORDS.get(e.get('kind', ''), e.get('kind', 'device change'))}\n"
            f"{e.get('old_value', '')} → {e.get('new_value', '')}"
        )
        for admin in admins:
            try:
                await notify_user(
                    db, account_id, admin.id,
                    NotificationContent(
                        title="",
                        body=body,
                        category="alert.device_identity",
                        severity="warning",
                    ),
                )
            except Exception as err:
                logger.error(
                    "device-identity notice to user %s failed: %s",
                    admin.id, err,
                )
    dropped = len(events) - _MAX_NOTICES_PER_TICK
    if dropped > 0:
        logger.warning(
            "device-identity notices capped acct=%d — %d more event(s) "
            "recorded in device_event_log only", account_id, dropped,
        )
