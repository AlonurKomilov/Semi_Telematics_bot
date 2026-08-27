"""Storage for ``alert_triggers`` — one person's watch on one metric.

Scoped by ``account_id`` on every read and write, like every tenant
table.  ``owner_user_id`` narrows further: a personal trigger belongs to
one person, and nobody else's list may show it or delete it, which is why
the delete and update helpers take the owner and match on it rather than
trusting the caller to have checked.
"""

from __future__ import annotations

from typing import Any


class AlertTriggersMixin:

    async def list_alert_triggers(
        self, account_id: int, *, owner_user_id: int | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        """One account's triggers, newest first.

        ``owner_user_id`` set → just that person's (the list they edit).
        Omitted → the whole account's, which is what the evaluator sweeps.
        """
        q = ("SELECT id, account_id, owner_user_id, metric, threshold, scope, "
             "       origin, enabled, severity, channels, vehicles, "
             "       created_at, updated_at "
             "  FROM alert_triggers WHERE account_id = ?")
        params: list = [account_id]
        if owner_user_id is not None:
            q += " AND owner_user_id = ?"
            params.append(owner_user_id)
        if enabled_only:
            q += " AND enabled = 1"
        q += " ORDER BY id DESC"
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def list_enabled_alert_triggers(self) -> list[dict[str, Any]]:
        """Every enabled trigger on the platform, for the sweep to group
        by account.  Mirrors how the built-in checkers collect their
        subscribers in one pass rather than per account."""
        cur = await self._db.execute(
            "SELECT id, account_id, owner_user_id, metric, threshold, scope, "
            "       origin, enabled, severity, channels, vehicles "
            "  FROM alert_triggers WHERE enabled = 1 ORDER BY account_id, id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_alert_triggers(self, account_id: int, owner_user_id: int) -> int:
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM alert_triggers "
            " WHERE account_id = ? AND owner_user_id = ?",
            (account_id, owner_user_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def create_alert_trigger(
        self, account_id: int, owner_user_id: int, *, metric: str,
        threshold: float, severity: str = "warning",
        scope: str = "personal", origin: str = "user",
        # Mirrors DEFAULT_CHANNELS_CSV in capabilities/alerting/triggers/
        # models.py, which is the SSOT — repeated as a literal because
        # adapters may not import from capabilities.  Only ever reached
        # by a caller that passes nothing; the router always passes.
        channels: str = "telegram_dm,email",
        # csv of vehicles.id.  '' = every vehicle in the owner's scope,
        # which is what a trigger meant before targeting existed — so the
        # default preserves the meaning of every row already written.
        vehicles: str = "",
    ) -> dict[str, Any]:
        """Insert one trigger and return it.

        No cap check here on purpose.  The per-person limit is policy, it
        lives with the catalog it belongs to, and the storage layer may
        not import from capabilities — the router counts first and
        refuses with a message a person can act on.
        """
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO alert_triggers
                 (account_id, owner_user_id, metric, threshold, scope,
                  origin, enabled, severity, channels, vehicles,
                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
               RETURNING id""",
            (account_id, owner_user_id, metric, float(threshold), scope,
             origin, severity, channels, vehicles, now, now),
        )
        row = await cur.fetchone()
        await self._db.commit()
        return {
            "id": int(row[0]) if row else 0, "account_id": account_id,
            "owner_user_id": owner_user_id, "metric": metric,
            "threshold": float(threshold), "scope": scope, "origin": origin,
            "enabled": True, "severity": severity, "channels": channels,
            "vehicles": vehicles,
            "created_at": now, "updated_at": now,
        }

    async def update_alert_trigger(
        self, account_id: int, owner_user_id: int, trigger_id: int, *,
        metric: str | None = None,
        threshold: float | None = None, enabled: bool | None = None,
        channels: str | None = None,
        vehicles: str | None = None,
    ) -> bool:
        """Edit one's own trigger.  Scoped to the owner in the WHERE, so a
        foreign id silently matches nothing rather than editing it."""
        sets, params = [], []
        if metric is not None:
            sets.append("metric = ?")
            params.append(metric)
        if threshold is not None:
            sets.append("threshold = ?")
            params.append(float(threshold))
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if channels is not None:
            sets.append("channels = ?")
            params.append(channels)
        # '' is a MEANING here ("all my vehicles"), not an absent value —
        # so the guard is `is not None`, and clearing a selection saves.
        if vehicles is not None:
            sets.append("vehicles = ?")
            params.append(vehicles)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(self._now())
        params += [account_id, owner_user_id, trigger_id]
        cur = await self._db.execute(
            f"UPDATE alert_triggers SET {', '.join(sets)} "
            "  WHERE account_id = ? AND owner_user_id = ? AND id = ?",
            params,
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def delete_alert_trigger(
        self, account_id: int, owner_user_id: int, trigger_id: int,
    ) -> bool:
        cur = await self._db.execute(
            "DELETE FROM alert_triggers "
            " WHERE account_id = ? AND owner_user_id = ? AND id = ?",
            (account_id, owner_user_id, trigger_id),
        )
        await self._db.commit()
        return cur.rowcount > 0
