"""ELD storage — the duty clocks one provider currently reports.

A MIRROR, deliberately, and the table is named for it.  The certified
ELD is the system of record for hours of service; we hold what it
currently says so dispatch can ask "who can take this load" without
opening another product.  Nothing here is evidence and nothing
downstream may compute a violation from it.

Why not ``driver_hos_status``
-----------------------------
That table already exists, empty — a writer was designed for it and
never built, which is why the AI's HOS tool has been refusing to answer
rather than reading zero rows and calling it a clean week.  Two things
make it the wrong home now:

  * its primary key is ``user_id``, so a driver we have not linked to
    the provider yet simply has no row.  The provider knows about that
    driver and reports their clocks; losing them at the door means the
    account's HOS answer silently omits people.
  * ``samsara_driver_id`` puts one vendor's name in a shared
    identifier, which is the rename this project has done twice
    already.

So the key here is ``(account_id, provider_id, provider_driver_id)`` —
what the provider actually gives us — and ``user_id`` is a nullable
ENRICHMENT filled in when a link exists.  The old table keeps its rows
(there are none) until a later migration retires it.

The link itself is not resolved here.  Callers pass a
``provider_driver_id -> user_id`` map, so this module never learns how
any particular vendor identifies a driver.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger("bot.storage")


if TYPE_CHECKING:
    class _MixinBase:
        _db: Any

        def _now(self) -> str: ...
else:
    _MixinBase = object


# The columns an upsert writes, in one place so the INSERT, the
# conflict clause and the read below cannot drift apart.
_HOS_FIELDS = (
    "duty_status",
    "drive_seconds_today",
    "on_duty_seconds_today",
    "cycle_seconds_remaining",
    "shift_seconds_remaining",
    "last_status_change",
    "driver_name",
    "source_ts",
)


class EldMixin(_MixinBase):
    """Reads and writes for ``driver_hos_live``."""

    async def upsert_driver_hos(
        self,
        account_id: int,
        provider_id: str,
        rows: list[dict],
        *,
        links: Optional[dict[str, int]] = None,
    ) -> int:
        """Replace this provider's duty clocks for an account.

        ``rows`` are plain dicts — the ingest flattens the protocol's
        ``HosSnapshot`` on the way in, so storage never learns the
        telematics contract and the telematics layer never learns SQL.

        ``links`` maps the provider's driver id to OUR ``users.id``.
        A driver with no entry is still written, with a NULL
        ``user_id``: the provider reports their clocks whether or not
        an admin has linked them yet, and dropping them here would make
        the account's HOS answer quietly incomplete — the same shape of
        bug as answering zero.

        An upsert rather than a delete-and-insert: a driver the
        provider stopped reporting keeps their last known reading with
        its original ``source_ts``, which the reader then shows as
        stale.  Deleting the row would make a driver whose ELD went
        dark look like a driver who does not exist.
        """
        if not rows:
            return 0
        links = links or {}
        now = self._now()
        written = 0
        for r in rows:
            pdid = str(r.get("provider_driver_id") or "").strip()
            if not pdid:
                # Without the provider's own id there is nothing to key
                # on and nothing to link to later.  Count it as skipped
                # rather than inventing a key.
                continue
            values = [r.get(f) for f in _HOS_FIELDS]
            await self._db.execute(
                f"""
                INSERT INTO driver_hos_live
                    (account_id, provider_id, provider_driver_id, user_id,
                     {', '.join(_HOS_FIELDS)}, updated_at)
                VALUES ({', '.join(['?'] * (5 + len(_HOS_FIELDS)))})
                ON CONFLICT (account_id, provider_id, provider_driver_id)
                DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    {', '.join(f'{f} = EXCLUDED.{f}' for f in _HOS_FIELDS)},
                    updated_at = EXCLUDED.updated_at
                """,
                (account_id, provider_id, pdid, links.get(pdid),
                 *values, now),
            )
            written += 1
        return written

    async def get_driver_hos_live(
        self,
        account_id: int,
        user_id: Optional[int] = None,
    ) -> list[dict]:
        """Current duty clocks for an account, newest reading first.

        Joins ``users`` so one query carries our own roster name and
        the driver's truck.  ``display_name`` prefers OUR name and
        falls back to the provider's, which is only ever a diagnostic
        for an unlinked driver — the provider's spelling should not be
        what an operator reads when we know better.

        ``source_ts`` travels with every row and is NOT our write time.
        HOS goes stale in minutes; a caller that shows ``updated_at``
        would call a twenty-minute-old reading fresh.
        """
        sql = (
            "SELECT h.provider_id, h.provider_driver_id, h.user_id, "
            "       h.duty_status, h.drive_seconds_today, "
            "       h.on_duty_seconds_today, h.cycle_seconds_remaining, "
            "       h.shift_seconds_remaining, h.last_status_change, "
            "       h.driver_name, h.source_ts, h.updated_at, "
            "       u.display_name, u.truck_num "
            "FROM driver_hos_live h "
            "LEFT JOIN users u ON u.id = h.user_id "
            "WHERE h.account_id = ?"
        )
        params: list = [account_id]
        if user_id is not None:
            sql += " AND h.user_id = ?"
            params.append(user_id)
        sql += " ORDER BY h.source_ts DESC, h.provider_driver_id"

        cur = await self._db.execute(sql, params)
        out: list[dict] = []
        for r in await cur.fetchall():
            row = dict(zip((
                "provider_id", "provider_driver_id", "user_id",
                "duty_status", "drive_seconds_today",
                "on_duty_seconds_today", "cycle_seconds_remaining",
                "shift_seconds_remaining", "last_status_change",
                "driver_name", "source_ts", "updated_at",
                "display_name", "truck_num",
            ), tuple(r)))
            row["display_name"] = (
                row.get("display_name") or row.get("driver_name") or ""
            )
            row["truck_num"] = row.get("truck_num") or ""
            row["linked"] = row.get("user_id") is not None
            out.append(row)
        return out

    async def count_driver_hos_live(self, account_id: int) -> int:
        """How many duty readings the account holds.

        The question a caller asks before answering an HOS question at
        all: zero rows means nothing has ever been ingested, which is
        NOT the same statement as "every driver has hours remaining".
        """
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM driver_hos_live WHERE account_id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0
