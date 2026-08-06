"""Vehicle lifecycle machinery: the departure sweep and the ingest ledger.

``vehicle_state_live`` means "the fleet as it reports NOW", but nothing ever
removed a row once its gateway went silent: a truck that left Samsara in
May was still served as a current vehicle in August, its frozen odometer
indistinguishable from a fresh one.  Gateway swaps made it worse — the
same physical truck kept one live row per badge it ever wore, which is
how unit 6729 came to exist three times.

The sweep retires rows whose provider signal is older than the
threshold: the registry entry linked to that badge is flagged
``is_active = 0`` (a flag the registry already has, with readers that
already honour it) and the stale live-state row is deleted.  History is
untouched — telemetry, safety events and work orders keep their
identity links and stay reachable from the truck's pages.  The
operator's ``status`` column is never written: that vocabulary
(active / available / shop / yard …) belongs to people, not to this
sweep.  Revival is the ingest's existing behaviour — a badge that
reports again is re-upserted and its registry row reactivated.

One deliberate refusal: if EVERY row in the account is stale, the sweep
does nothing and says so.  Whole-fleet silence is an outage or a paused
integration, not a fleet-wide departure — retiring a customer's entire
fleet on the strength of our own ingest being broken would repeat the
"silence mistaken for fact" failure this whole arc exists to end.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Older than this with no provider signal = departed.  Matches the
# Samsara client's own active-vehicle window (active_days = 30), so the
# ingest filter and the retirement rule agree on what "gone" means.
DEPARTED_AFTER_DAYS = 30


class VehicleDepartureMixin:
    """Registered on the single ``Database`` beside the other mixins."""

    async def sweep_departed_vehicles(
        self,
        account_id: int,
        *,
        silent_days: int = DEPARTED_AFTER_DAYS,
    ) -> dict[str, Any]:
        """Retire live-state rows silent beyond ``silent_days``.

        Returns ``{"departed": [...], "registry_deactivated": n,
        "skipped_all_stale": bool}`` — the departed list carries
        (vehicle_id, name, company) for the caller's log line.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=silent_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

        cols = ("vehicle_id", "vehicle_name", "company_code", "captured_at")
        cur = await self._db.execute(
            f"SELECT {', '.join(cols)} FROM vehicle_state_live "
            f"WHERE account_id = ?",
            (account_id,),
        )
        rows = [dict(zip(cols, r)) for r in await cur.fetchall()]
        if not rows:
            return {"departed": [], "registry_deactivated": 0,
                    "skipped_all_stale": False}

        # A blank captured_at is a truck that never transmitted a
        # location — freshly added badges look like that for a tick or
        # two.  They are not "departed"; they simply haven't arrived.
        stale = [
            r for r in rows
            if r["captured_at"] and str(r["captured_at"])[:19] < cutoff
        ]
        if not stale:
            return {"departed": [], "registry_deactivated": 0,
                    "skipped_all_stale": False}
        if len(stale) == len(rows):
            logger.warning(
                "departure sweep acct=%d: ALL %d vehicles stale — treating "
                "as an ingest outage, not a fleet-wide departure; nothing "
                "retired", account_id, len(rows),
            )
            return {"departed": [], "registry_deactivated": 0,
                    "skipped_all_stale": True}

        stale_ids = [str(r["vehicle_id"]) for r in stale]
        placeholders = ", ".join("?" for _ in stale_ids)

        # Registry first: the badge's registry entry goes inactive.  The
        # operator status column is deliberately absent from this UPDATE.
        cur = await self._db.execute(
            f"UPDATE vehicles SET is_active = 0, updated_at = ? "
            f"WHERE account_id = ? AND telematics_ref IN ({placeholders}) "
            f"AND is_active = 1",
            (self._now(), account_id, *stale_ids),
        )
        deactivated = getattr(cur, "rowcount", 0) or 0

        await self._db.execute(
            f"DELETE FROM vehicle_state_live "
            f"WHERE account_id = ? AND vehicle_id IN ({placeholders})",
            (account_id, *stale_ids),
        )
        await self._db.commit()

        departed = [
            (r["vehicle_id"], r["vehicle_name"], r["company_code"])
            for r in stale
        ]
        logger.info(
            "departure sweep acct=%d retired %d badge(s), deactivated %d "
            "registry row(s): %s",
            account_id, len(departed), deactivated,
            ", ".join(f"{n or '?'} ({c or '-'})" for _, n, c in departed),
        )
        return {"departed": departed, "registry_deactivated": deactivated,
                "skipped_all_stale": False}


    async def record_ingest_run(
        self, account_id: int, dataset_key: str, rows_written: int,
    ) -> None:
        """Fold one ingest run into the day-grain ledger.

        The ledger is how "this dataset wrote nothing for three days"
        becomes one query instead of a forensic project — five datasets
        died silently for want of exactly this record.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        await self._db.execute(
            """
            INSERT INTO ingest_runs (
                account_id, dataset_key, day,
                runs, rows_sum, last_rows, last_ran_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT (account_id, dataset_key, day) DO UPDATE SET
                runs = ingest_runs.runs + 1,
                rows_sum = ingest_runs.rows_sum + excluded.rows_sum,
                last_rows = excluded.last_rows,
                last_ran_at = excluded.last_ran_at
            """,
            (account_id, dataset_key, now.strftime("%Y-%m-%d"),
             int(rows_written), int(rows_written),
             now.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        await self._db.commit()
