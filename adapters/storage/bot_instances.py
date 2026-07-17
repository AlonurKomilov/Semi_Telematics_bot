"""Per-role sender bots ("Sub bot") — CRUD mixin.

One optional Telegram bot per (account, persona): a role manager
attaches their own BotFather token so their role's alert group
receives posts from THEIR bot.  Sender-only by contract — identity
(registration, login, commands, user binding) never leaves the
account's primary bot, and the alert pipeline falls back to the
primary whenever an instance is missing or down.

Personas match ``account_persona_groups.persona``:
owner_admin, dispatcher, safety, fleet, hr.
"""

from __future__ import annotations

from typing import Optional

from .models import BotInstance


class BotInstancesMixin:
    # ``self._db`` / ``self._now`` come from the concrete Database —
    # NEVER stub them here: a method stub in the MRO would shadow the
    # real ``_now`` and null every ``created_at`` in the application.

    async def upsert_bot_instance(
        self,
        *,
        account_id: int,
        persona: str,
        token_encrypted: str,
        bot_username: str = "",
        webhook_secret: str = "",
    ) -> BotInstance:
        """Attach (or replace) the persona's sender bot.  Re-running for
        the same (account_id, persona) swaps the token/username and
        reactivates a previously detached row."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO bot_instances
               (account_id, persona, bot_username, token_encrypted,
                webhook_secret, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(account_id, persona) DO UPDATE SET
                   bot_username    = excluded.bot_username,
                   token_encrypted = excluded.token_encrypted,
                   webhook_secret  = excluded.webhook_secret,
                   is_active       = 1,
                   updated_at      = excluded.updated_at""",
            (account_id, persona, bot_username, token_encrypted,
             webhook_secret, now, now),
        )
        await self._db.commit()
        row = await self.get_bot_instance(account_id, persona)
        assert row is not None  # just written
        return row

    async def get_bot_instance(
        self, account_id: int, persona: str
    ) -> Optional[BotInstance]:
        """The persona's ACTIVE sender bot, or None (→ primary sends)."""
        cur = await self._db.execute(
            "SELECT * FROM bot_instances "
            "WHERE account_id = ? AND persona = ? AND is_active = 1 LIMIT 1",
            (account_id, persona),
        )
        row = await cur.fetchone()
        return self._row_to_bot_instance(row) if row else None

    async def list_bot_instances(self, account_id: int) -> list[BotInstance]:
        """Every sender bot (active and inactive) for the settings UI."""
        cur = await self._db.execute(
            "SELECT * FROM bot_instances WHERE account_id = ? ORDER BY persona ASC",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_bot_instance(r) for r in rows]

    async def list_all_active_bot_instances(self) -> list[BotInstance]:
        """Every active sender bot across accounts — bot-service boot."""
        cur = await self._db.execute(
            "SELECT * FROM bot_instances WHERE is_active = 1 ORDER BY account_id ASC",
        )
        rows = await cur.fetchall()
        return [self._row_to_bot_instance(r) for r in rows]

    async def delete_bot_instance(self, account_id: int, persona: str) -> None:
        """Detach the persona's sender bot — delivery falls back to the
        primary bot on the next alert (never dropped)."""
        await self._db.execute(
            "DELETE FROM bot_instances WHERE account_id = ? AND persona = ?",
            (account_id, persona),
        )
        await self._db.commit()

    def _row_to_bot_instance(self, row) -> BotInstance:
        d = dict(row)
        return BotInstance(
            id=d["id"],
            account_id=d["account_id"],
            persona=d["persona"],
            bot_username=d["bot_username"] or "",
            token_encrypted=d["token_encrypted"],
            webhook_secret=d["webhook_secret"] or "",
            is_active=bool(d["is_active"]),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )
