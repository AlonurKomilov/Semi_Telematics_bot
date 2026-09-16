"""Which provider driver IS which member — for any provider.

The link used to live in a vendor-named column, and there were already
TWO: ``users.samsara_driver_id`` and ``users.datatruck_driver_id``,
each with its own write method, its own picker and its own uniqueness
check. A third ELD would have made three, which is the rename this
project has done twice and the note
``features/eld/ingest::_driver_links`` was carrying as a confession.

One table, keyed on what the PROVIDER gives us —
``(account_id, provider_id, provider_driver_id)`` — with ``user_id`` as
the thing we learned. The same shape as ``driver_hos_live``, for the
same reason: a provider's id is the only stable handle on a person
before anybody has matched them.

THE LEGACY COLUMNS STILL COUNT
------------------------------
They hold live links and four features read them. Every read here
MERGES the new table over them, new rows winning, so this can land
without a data migration and without a flag day. They retire on their
own change, when their four readers move — not in the commit that
introduces their replacement.

WHY LINKS ARE NOT GUESSED HERE
------------------------------
Nothing in this module infers a link. The matcher that Datatruck's
import uses (``_driver_match_state``) goes ref → CDL → email and
deliberately refuses NAME, because a wrong match writes one driver's
licence number onto another driver's record. This module stores the
answer; it does not invent one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger("bot.storage")


if TYPE_CHECKING:
    class _MixinBase:
        _db: Any

        def _now(self) -> str: ...

        def transaction(self): ...
else:
    _MixinBase = object


#: provider_id → the legacy column that still holds its links.
#:
#: Read-only here. A new link is written to the table; these are how we
#: keep seeing the ones that already exist.
_LEGACY_COLUMN = {
    "samsara": "samsara_driver_id",
    "datatruck": "datatruck_driver_id",
}


class DriverLinksMixin(_MixinBase):
    """Reads and writes for ``driver_provider_links``."""

    async def driver_links_for(
        self, account_id: int, provider_id: str,
    ) -> dict[str, int]:
        """``{provider_driver_id: user_id}`` for one provider.

        Merges the legacy vendor column UNDER the table, so a provider
        that predates it keeps every link it has while new ones are
        written to the new home. A row in the table wins: it is the
        newer statement, and it is the one an admin made most recently.
        """
        out: dict[str, int] = {}

        column = _LEGACY_COLUMN.get(provider_id)
        if column:
            try:
                cur = await self._db.execute(
                    f"SELECT {column}, id FROM users "
                    f"WHERE account_id = ? AND {column} IS NOT NULL "
                    f"AND {column} <> ''",
                    (account_id,),
                )
                for ref, uid in await cur.fetchall():
                    key = str(ref or "").strip()
                    if key and uid is not None:
                        out[key] = int(uid)
            except Exception:
                # A legacy column that has been dropped is not an error
                # — it is the end state this module exists to reach.
                logger.exception(
                    "driver_links: legacy read failed acct=%d provider=%s",
                    account_id, provider_id,
                )

        try:
            cur = await self._db.execute(
                "SELECT provider_driver_id, user_id FROM driver_provider_links "
                "WHERE account_id = ? AND provider_id = ?",
                (account_id, provider_id),
            )
            for ref, uid in await cur.fetchall():
                key = str(ref or "").strip()
                if key and uid is not None:
                    out[key] = int(uid)
        except Exception:
            # Before the migration has run, the legacy answer above is
            # the whole answer — which is exactly today's behaviour.
            logger.exception(
                "driver_links: table read failed acct=%d provider=%s",
                account_id, provider_id,
            )
        return out

    async def link_provider_driver(
        self,
        account_id: int,
        provider_id: str,
        user_id: int,
        provider_driver_id: str,
        *,
        method: str = "manual",
        linked_by: Optional[int] = None,
    ) -> None:
        """Bind one provider driver to one member, or unbind with ``''``.

        Two rules, and both refuse rather than overwrite:

          a provider driver already bound to ANOTHER member — binding it
          again would silently move somebody's hours of service onto a
          different person;

          a member who already holds a DIFFERENT id for this provider —
          one person cannot be two drivers on one device, and letting
          them be makes every reader pick whichever row sorts first.

        Both are checked here AND held by the schema, because a race
        between two admins on the same drawer would slip past a check
        alone.
        """
        ref = (provider_driver_id or "").strip()

        if not ref:
            async with self.transaction():
                await self._db.execute(
                    "DELETE FROM driver_provider_links "
                    "WHERE account_id = ? AND provider_id = ? AND user_id = ?",
                    (account_id, provider_id, user_id),
                )
            return

        cur = await self._db.execute(
            "SELECT user_id FROM driver_provider_links "
            "WHERE account_id = ? AND provider_id = ? "
            "AND provider_driver_id = ?",
            (account_id, provider_id, ref),
        )
        row = await cur.fetchone()
        if row and int(row[0]) != int(user_id):
            raise ValueError(
                "that driver is already linked to another member",
            )

        now = self._now()
        async with self.transaction():
            # One identity per member per provider: clear whatever they
            # held before, so a re-link REPLACES rather than accumulates.
            await self._db.execute(
                "DELETE FROM driver_provider_links "
                "WHERE account_id = ? AND provider_id = ? AND user_id = ?",
                (account_id, provider_id, user_id),
            )
            await self._db.execute(
                "INSERT INTO driver_provider_links "
                "(account_id, provider_id, provider_driver_id, user_id, "
                " link_method, linked_by, linked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, provider_id, provider_driver_id) "
                "DO UPDATE SET user_id = EXCLUDED.user_id, "
                "              link_method = EXCLUDED.link_method, "
                "              linked_by = EXCLUDED.linked_by, "
                "              linked_at = EXCLUDED.linked_at",
                (account_id, provider_id, ref, user_id,
                 str(method or "manual"), linked_by, now),
            )

    async def provider_driver_link_rows(
        self, account_id: int,
    ) -> list[dict]:
        """Every link on the account, for the admin surface.

        Includes the legacy ones so the page shows the truth rather
        than only what this table happens to hold yet.
        """
        out: list[dict] = []
        for provider_id in sorted(
            set(_LEGACY_COLUMN) | await self._link_table_providers(account_id)
        ):
            for ref, uid in (
                await self.driver_links_for(account_id, provider_id)
            ).items():
                out.append({
                    "provider_id": provider_id,
                    "provider_driver_id": ref,
                    "user_id": uid,
                })
        return out

    async def _link_table_providers(self, account_id: int) -> set[str]:
        try:
            cur = await self._db.execute(
                "SELECT DISTINCT provider_id FROM driver_provider_links "
                "WHERE account_id = ?",
                (account_id,),
            )
            return {str(r[0]) for r in await cur.fetchall() if r[0]}
        except Exception:
            return set()
