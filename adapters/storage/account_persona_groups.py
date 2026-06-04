"""Per-persona Telegram group CRUD.

Backing layer for the Pattern B alerts routing when an account opts
into ``accounts.alert_routing_mode = 'per_persona_groups'``.  One row
per (account, persona) pair; lookup is the hot-path that
``capabilities.alerting.routing_resolver`` consults on every alert
dispatch when the account is in the new mode.

Independent of the legacy ``forum_groups`` / ``alert_routing`` pair —
the new mode bypasses both.  An account can have either model active
at a time (chosen by ``alert_routing_mode``); both can coexist as
configuration but the resolver only reads from the active one.
"""

from __future__ import annotations

from typing import Optional

from .models import PersonaGroup


class AccountPersonaGroupsMixin:

    async def upsert_persona_group(
        self,
        *,
        account_id: int,
        persona: str,
        chat_id: int,
        chat_title: str = "",
    ) -> PersonaGroup:
        """Bind a persona to a Telegram group chat.  Re-running with the
        same (account_id, persona) overwrites the chat_id — used when an
        admin moves a persona to a different group in the admin UI.

        Always returns the row with ``is_active=True`` — re-upserting a
        deactivated row reactivates it (same shape as the legacy
        ``upsert_alert_route`` contract).
        """
        now = self._now()
        await self._db.execute(
            """INSERT INTO account_persona_groups
                 (account_id, persona, chat_id, chat_title,
                  is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(account_id, persona) DO UPDATE SET
                 chat_id    = excluded.chat_id,
                 chat_title = excluded.chat_title,
                 is_active  = 1,
                 updated_at = excluded.updated_at""",
            (account_id, persona, chat_id, chat_title, now, now),
        )
        await self._db.commit()
        row = await self.get_persona_group(account_id, persona)
        assert row is not None
        return row

    async def get_persona_group(
        self, account_id: int, persona: str
    ) -> Optional[PersonaGroup]:
        """Return the (active) persona group, or None if missing/disabled.

        ``is_active = 0`` rows are treated as absent — the resolver's
        fallback chain takes over.
        """
        cur = await self._db.execute(
            """SELECT * FROM account_persona_groups
               WHERE account_id = ? AND persona = ? AND is_active = 1
               LIMIT 1""",
            (account_id, persona),
        )
        row = await cur.fetchone()
        return self._row_to_persona_group(row) if row else None

    async def list_persona_groups(
        self, account_id: int
    ) -> list[PersonaGroup]:
        """Every persona group (active and inactive) for the admin UI."""
        cur = await self._db.execute(
            """SELECT * FROM account_persona_groups
               WHERE account_id = ?
               ORDER BY persona ASC""",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_persona_group(r) for r in rows]

    async def set_persona_group_active(
        self, account_id: int, persona: str, *, active: bool
    ) -> None:
        """Soft-disable / re-enable a persona group without losing the
        chat_id — convenient when an admin temporarily pauses routing
        to one persona during onboarding.
        """
        now = self._now()
        await self._db.execute(
            """UPDATE account_persona_groups
               SET is_active = ?, updated_at = ?
               WHERE account_id = ? AND persona = ?""",
            (1 if active else 0, now, account_id, persona),
        )
        await self._db.commit()

    async def delete_persona_group(
        self, account_id: int, persona: str
    ) -> None:
        """Permanently remove a persona binding.  Use ``set_persona_group_active``
        for reversible disable; this is the destructive path."""
        await self._db.execute(
            "DELETE FROM account_persona_groups WHERE account_id = ? AND persona = ?",
            (account_id, persona),
        )
        await self._db.commit()

    def _row_to_persona_group(self, row) -> PersonaGroup:
        return PersonaGroup(
            id=row["id"],
            account_id=row["account_id"],
            persona=row["persona"],
            chat_id=row["chat_id"],
            chat_title=row["chat_title"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
