"""Web-push device subscriptions — per-DEVICE endpoints for the push
channel (notifications phase 6).

A user's push consent is per BROWSER/DEVICE (laptop on, phone off), so
these are sub-entities of the user rather than one address on
``notification_channel``: the channel-level row says "this user uses
push"; each row here is one device that can actually receive it.

``endpoint`` is the browser-issued push URL — globally unique per
subscription, so the upsert conflicts on it: re-enabling in the same
browser refreshes the crypto keys instead of duplicating the device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class _MixinBase:
        _db: Any
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class PushSubscriptionsMixin(_MixinBase):

    async def add_push_subscription(
        self, account_id: int, user_id: int, *, endpoint: str,
        p256dh: str, auth: str, device_label: str = "",
    ) -> dict | None:
        """Store (or refresh) one device subscription.

        REASSIGN-WITH-REPAIR (advisor-settled): the endpoint is globally
        unique and the Push API reuses one subscription per browser
        profile, so on a shared cab tablet the SAME endpoint legitimately
        moves to whoever enabled push last.  The upsert therefore
        reassigns ownership — but in the same transaction it repairs the
        DISPOSSESSED owner: if this was their last device, their
        ``web_push`` channel row is un-verified so the delivery gate
        closes honestly instead of leaving them "connected" with silent
        delivery failures forever.  (Their master on/off choice is
        untouched.)

        Returns ``{"account_id", "user_id"}`` of the previous owner when
        ownership actually moved (the router logs it — warning-level on a
        cross-ACCOUNT move, the forensic trail for tenant-boundary
        questions), else ``None``.
        """
        cur = await self._db.execute(
            "SELECT account_id, user_id FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        )
        prev = await cur.fetchone()
        now = self._now()
        await self._db.execute(
            """INSERT INTO push_subscriptions
                 (account_id, user_id, endpoint, p256dh, auth,
                  device_label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (endpoint)
               DO UPDATE SET account_id = ?, user_id = ?, p256dh = ?,
                             auth = ?, device_label = ?""",
            (account_id, user_id, endpoint, p256dh, auth,
             device_label[:120], now,
             account_id, user_id, p256dh, auth, device_label[:120]),
        )

        moved = None
        if prev is not None and (prev[0], prev[1]) != (account_id, user_id):
            moved = {"account_id": prev[0], "user_id": prev[1]}
            cur = await self._db.execute(
                "SELECT COUNT(*) FROM push_subscriptions"
                " WHERE account_id = ? AND user_id = ?",
                (prev[0], prev[1]),
            )
            left = (await cur.fetchone())[0]
            if not left:
                # Direct UPDATE (not the mixin helper) so the repair is in
                # THIS transaction — no window where the previous owner is
                # verified with zero devices.
                await self._db.execute(
                    """UPDATE notification_channel
                          SET verified_at = '', updated_at = ?
                        WHERE account_id = ? AND recipient_type = 'user'
                          AND recipient_id = ? AND channel = 'web_push'""",
                    (now, prev[0], str(prev[1])),
                )
        await self._db.commit()
        return moved

    async def list_push_subscriptions(
        self, account_id: int, user_id: int,
    ) -> list[dict]:
        cur = await self._db.execute(
            """SELECT id, endpoint, p256dh, auth, device_label,
                      created_at, last_ok_at
                 FROM push_subscriptions
                WHERE account_id = ? AND user_id = ?
                ORDER BY id""",
            (account_id, user_id),
        )
        return [
            {"id": r[0], "endpoint": r[1], "p256dh": r[2], "auth": r[3],
             "device_label": r[4], "created_at": r[5], "last_ok_at": r[6]}
            for r in await cur.fetchall()
        ]

    async def remove_push_subscription(
        self, account_id: int, user_id: int, endpoint: str,
    ) -> bool:
        """Remove one device (user-initiated 'disable on this device').
        Tenant+user-scoped so a caller can only ever drop their own."""
        cur = await self._db.execute(
            """DELETE FROM push_subscriptions
                WHERE account_id = ? AND user_id = ? AND endpoint = ?""",
            (account_id, user_id, endpoint),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def prune_push_subscription(self, endpoint: str) -> None:
        """Drop a dead endpoint (the push service answered 404/410 —
        browser uninstalled / permission revoked).  Keyed on the globally
        unique endpoint because the prune happens INSIDE the send path,
        which already resolved the recipient."""
        await self._db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,),
        )
        await self._db.commit()

    async def touch_push_subscription(self, endpoint: str) -> None:
        """Record a successful delivery (freshness signal for the device
        list; also lets a future sweep drop long-dead devices)."""
        await self._db.execute(
            "UPDATE push_subscriptions SET last_ok_at = ? WHERE endpoint = ?",
            (self._now(), endpoint),
        )
        await self._db.commit()
