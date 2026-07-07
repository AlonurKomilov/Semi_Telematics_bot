"""Per-user UI preferences — DataTable layouts, filters, etc.

Backs the dashboard's auto-save: when an operator hides a column,
reorders headers, or pins a column, that change persists server-side
(scoped to their user_id) so the layout follows them across devices
instead of being trapped in one browser's localStorage.

Keys are opaque strings the frontend coins (e.g. ``"table.maintenance-
tasks.layout"``) — the storage layer doesn't interpret them.  Values
are TEXT (the frontend serialises JSON in/out) — same approach as
``account_settings``, just user-scoped.
"""

from __future__ import annotations

from typing import Optional


class UserPreferencesMixin:
    """Lightweight key-value store for per-user UI state.

    One row per (user_id, key).  Updates are upserts; ``get`` returns
    ``default`` when the row is absent so the frontend can treat the
    first read of a new key the same as a fresh-default state.
    """

    async def get_user_preference(
        self, user_id: int, key: str, default: str = "",
    ) -> str:
        cur = await self._db.execute(
            "SELECT value FROM user_preferences "
            "WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_user_preference(
        self, user_id: int, key: str, value: str,
    ) -> None:
        await self._db.execute(
            "INSERT INTO user_preferences (user_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = ?, updated_at = ?",
            (user_id, key, value, self._now(), value, self._now()),
        )
        await self._db.commit()

    async def delete_user_preference(
        self, user_id: int, key: str,
    ) -> None:
        await self._db.execute(
            "DELETE FROM user_preferences WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        await self._db.commit()

    async def list_user_preferences(
        self, user_id: int, prefix: Optional[str] = None,
    ) -> dict[str, str]:
        """Return every preference for a user as ``{key: value}``.

        ``prefix`` narrows to keys starting with that string, useful for
        loading "all DataTable layouts for this user" in a single
        request instead of one round-trip per table.
        """
        if prefix:
            cur = await self._db.execute(
                "SELECT key, value FROM user_preferences "
                "WHERE user_id = ? AND key LIKE ? "
                "ORDER BY key",
                (user_id, f"{prefix}%"),
            )
        else:
            cur = await self._db.execute(
                "SELECT key, value FROM user_preferences "
                "WHERE user_id = ? "
                "ORDER BY key",
                (user_id,),
            )
        rows = await cur.fetchall()
        return {r["key"]: r["value"] for r in rows}
