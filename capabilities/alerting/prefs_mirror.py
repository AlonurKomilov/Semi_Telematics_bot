"""Mirror legacy alert-pref writes into the notification matrix.

Since the DM fanout moved to the notifications spine
(capabilities/alerting/docs/alert-dm-migration.md), the matrix
(``notification_pref`` + ``notification_channel``) is the DELIVERY SSOT
for personal Telegram alerts.  The legacy ``users.alert_*`` /
``alerts_on`` columns remain as a write-mirrored cache — the bot's
settings keyboard and a few reports still read them — but every write
must land in BOTH stores or a user's toggle stops affecting delivery.

One helper, three writers (dashboard PUT /user/me/alerts, the bot's
per-type toggle, the bot's master on/off) — so the mirror can't drift
per call site.  Best-effort by design: a mirror failure is logged, the
legacy write already succeeded, and the next successful mirror
converges the stores (same key set, last write wins).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CHANNEL = "telegram_dm"


async def mirror_alert_prefs_to_matrix(
    db,
    user,
    *,
    toggles: dict[str, bool] | None = None,
    alerts_on: bool | None = None,
) -> None:
    """Mirror one legacy pref write into the matrix.

    ``toggles`` maps BARE alert types ("faults", "events") to enabled;
    ``alerts_on`` maps to the channel's master switch (re-enable also
    refreshes address + verified — a linked Telegram id is implicitly
    verified, same rule the original backfill used).
    """
    account_id = getattr(user, "account_id", None)
    user_id = getattr(user, "id", None)
    if account_id is None or user_id is None:
        return
    for atype, enabled in (toggles or {}).items():
        try:
            await db.set_notification_pref(
                account_id, "user", str(user_id), _CHANNEL,
                f"alert.{atype}", enabled=bool(enabled),
                cadence="immediate",
            )
        except Exception as e:
            logger.error("pref mirror failed (%s/%s %s): %s",
                         account_id, user_id, atype, e)
    if alerts_on is None:
        return
    try:
        if alerts_on:
            telegram_id = getattr(user, "telegram_id", None)
            await db.upsert_notification_channel(
                account_id, "user", str(user_id), _CHANNEL,
                address=str(telegram_id or ""), verified=True,
                enabled_master=True,
            )
        else:
            await db.disable_notification_channel(
                account_id, "user", str(user_id), _CHANNEL)
    except Exception as e:
        logger.error("channel mirror failed (%s/%s master=%s): %s",
                     account_id, user_id, alerts_on, e)
