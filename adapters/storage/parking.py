"""Parking events CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class ParkingMixin:

    async def upsert_parking_event(
        self, account_id: int, vehicle_id: str, vehicle_name: str,
        company_code: str, latitude: float, longitude: float,
        address: str, first_stopped: str, duration_hours: float,
        location_class: str,
    ) -> dict:
        """Create or update a parking event for a stopped vehicle.

        If an active (unresolved) record exists for this vehicle, update it.
        Otherwise create a new record.
        Returns the record dict.
        """
        now = self._now()
        existing = await self.get_active_parking_event(account_id, vehicle_id)
        if existing:
            await self._db.execute(
                "UPDATE parking_events SET "
                "latitude = ?, longitude = ?, address = ?, "
                "duration_hours = ?, location_class = ?, "
                "last_checked = ? "
                "WHERE id = ?",
                (latitude, longitude, address,
                 duration_hours, location_class, now, existing["id"]),
            )
            await self._db.commit()
            existing.update(
                latitude=latitude, longitude=longitude, address=address,
                duration_hours=duration_hours, location_class=location_class,
                last_checked=now,
            )
            return existing
        else:
            cur = await self._db.execute(
                "INSERT INTO parking_events "
                "(account_id, vehicle_id, vehicle_name, company_code, "
                "latitude, longitude, address, first_stopped, "
                "duration_hours, location_class, alert_level, "
                "ai_analysis, resolved, last_checked, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', '', 0, ?, ?)",
                (account_id, vehicle_id, vehicle_name, company_code,
                 latitude, longitude, address, first_stopped,
                 duration_hours, location_class, now, now),
            )
            await self._db.commit()
            return {
                "id": cur.lastrowid,
                "account_id": account_id,
                "vehicle_id": vehicle_id,
                "vehicle_name": vehicle_name,
                "company_code": company_code,
                "latitude": latitude,
                "longitude": longitude,
                "address": address,
                "first_stopped": first_stopped,
                "duration_hours": duration_hours,
                "location_class": location_class,
                "alert_level": "none",
                "ai_analysis": "",
                "resolved": 0,
                "last_checked": now,
                "created_at": now,
            }

    async def get_active_parking_event(
        self, account_id: int, vehicle_id: str,
    ) -> dict | None:
        """Get the active (unresolved) parking event for a vehicle."""
        cur = await self._db.execute(
            "SELECT * FROM parking_events "
            "WHERE account_id = ? AND vehicle_id = ? AND resolved = 0 "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, vehicle_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_active_parking_events_for_vehicles(
        self, account_id: int, vehicle_ids: list[str],
    ) -> dict[str, dict]:
        """Bulk variant of ``get_active_parking_event``.

        Returns ``{vehicle_id: event_dict}`` for every vehicle in
        ``vehicle_ids`` that has an active (unresolved) parking event.
        Replaces V × per-vehicle queries in the parking-check job with
        one chunked query.
        """
        out: dict[str, dict] = {}
        if not vehicle_ids:
            return out
        # Chunk to stay below SQLite's 999-parameter limit (cap = 998
        # since one slot is reserved for account_id).
        for i in range(0, len(vehicle_ids), 500):
            chunk = vehicle_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"SELECT * FROM parking_events "
                f"WHERE account_id = ? AND resolved = 0 "
                f"  AND vehicle_id IN ({placeholders})",
                (account_id, *chunk),
            )
            for row in await cur.fetchall():
                d = dict(row)
                vid = d.get("vehicle_id")
                # If multiple unresolved rows somehow exist, keep the
                # most-recently-created one to mirror the singleton method.
                if vid:
                    prev = out.get(vid)
                    if prev is None or (d.get("created_at") or "") > (prev.get("created_at") or ""):
                        out[vid] = d
        return out

    async def get_active_parking_events(
        self, account_id: int, attention_only: bool = True,
    ) -> list[dict]:
        """Get all active parking events for an account.

        attention_only=True: only unsafe/unknown (needs attention).
        attention_only=False: all active events including safe.
        Sorted by duration_hours DESC.
        """
        if attention_only:
            cur = await self._db.execute(
                "SELECT * FROM parking_events "
                "WHERE account_id = ? AND resolved = 0 "
                "AND location_class NOT IN ('safe', 'geofence') "
                "ORDER BY duration_hours DESC",
                (account_id,),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM parking_events "
                "WHERE account_id = ? AND resolved = 0 "
                "ORDER BY duration_hours DESC",
                (account_id,),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def resolve_parking_event_by_id(
        self, account_id: int, event_id: int,
    ) -> bool:
        """Resolve exactly ONE parking event, by its own id.

        The vehicle-scoped variant below closes every unresolved row for
        a vehicle, which is wrong for a per-event button: the UI attaches
        Resolve to an Event, so it must act on that Event.  Normally a
        vehicle has one active row, but ``get_active_parking_events_for_
        vehicles`` explicitly handles "multiple unresolved rows somehow
        exist" — and in that case the vehicle-scoped call silently closed
        events the operator never saw.

        Alert cleanup still has to run per VEHICLE: ``alert_history`` and
        ``alert_acknowledgments`` are keyed by (account, type, vehicle),
        not by parking-event id, so there is nothing narrower to clear.
        It only runs once no unresolved rows remain for the vehicle,
        otherwise closing one of two events would drop the alert that the
        other one is still raising.
        """
        cur = await self._db.execute(
            "SELECT vehicle_id FROM parking_events "
            "WHERE id = ? AND account_id = ? AND resolved = 0",
            (event_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return False
        vehicle_id = dict(row).get("vehicle_id")

        await self._db.execute(
            "UPDATE parking_events SET resolved = 1, last_checked = ? "
            "WHERE id = ? AND account_id = ? AND resolved = 0",
            (self._now(), event_id, account_id),
        )
        await self._db.commit()

        cur = await self._db.execute(
            "SELECT COUNT(*) AS n FROM parking_events "
            "WHERE account_id = ? AND vehicle_id = ? AND resolved = 0",
            (account_id, vehicle_id),
        )
        remaining = dict(await cur.fetchone() or {}).get("n", 0) or 0
        if not remaining:
            await self.clear_alert_history(account_id, "parking", vehicle_id)
            await self.auto_resolve_alerts_by_vehicle(account_id, "parking", vehicle_id)
        return True

    async def resolve_parking_event(
        self, account_id: int, vehicle_id: str,
    ) -> bool:
        """Mark a parking event as resolved (vehicle moved or condition cleared).

        Also clears alert_history and alert_acknowledgments for this vehicle so
        the parking alert no longer shows as pending anywhere in the system.
        The notification to subscribers is handled separately by the caller
        via _send_parking_resolved when appropriate.
        """
        await self._db.execute(
            "UPDATE parking_events SET resolved = 1, last_checked = ? "
            "WHERE account_id = ? AND vehicle_id = ? AND resolved = 0",
            (self._now(), account_id, vehicle_id),
        )
        await self._db.commit()
        # Clean up the shared alert history record and all subscriber ack rows
        # so the parking alert is removed from pending everywhere.
        await self.clear_alert_history(account_id, "parking", vehicle_id)
        await self.auto_resolve_alerts_by_vehicle(account_id, "parking", vehicle_id)
        return True

    async def update_parking_alert_level(
        self, event_id: int, alert_level: str, ai_analysis: str = "",
        account_id: int = 0, map_image_path: str = "",
    ) -> bool:
        """Update the alert level and AI analysis for a parking event.

        If account_id is provided, the row must belong to that account.
        """
        now = self._now()
        acct_filter = " AND account_id = ?" if account_id else ""

        # Build SET clause dynamically
        set_parts = ["alert_level = ?"]
        params: list = [alert_level]
        if ai_analysis:
            set_parts.append("ai_analysis = ?")
            params.append(ai_analysis)
        if map_image_path:
            set_parts.append("map_image_path = ?")
            params.append(map_image_path)
        set_parts.append("last_checked = ?")
        params.append(now)
        params.append(event_id)
        if account_id:
            params.append(account_id)

        cur = await self._db.execute(
            f"UPDATE parking_events SET {', '.join(set_parts)} "
            f"WHERE id = ?{acct_filter}",
            params,
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def get_parking_event_by_id(self, event_id: int, account_id: int = 0) -> dict | None:
        """Get a single parking event by its row ID.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM parking_events WHERE id = ? AND account_id = ?",
                (event_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM parking_events WHERE id = ?",
                (event_id,),
            )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_parking_history(
        self, account_id: int, days: int = 0, limit: int = 50,
    ) -> list[dict]:
        """Get parking event history (resolved events), newest first.

        If days > 0, only return events resolved within the last N days.
        """
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            cur = await self._db.execute(
                "SELECT * FROM parking_events "
                "WHERE account_id = ? AND resolved = 1 "
                "AND last_checked >= ? "
                "ORDER BY last_checked DESC LIMIT ?",
                (account_id, cutoff, limit),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM parking_events "
                "WHERE account_id = ? AND resolved = 1 "
                "ORDER BY last_checked DESC LIMIT ?",
                (account_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def prune_parking_events(
        self, account_id: int, keep_days: int = 1095,
    ) -> int:
        """Delete parking events older than *keep_days*.  Returns the count.

        RESOLVED ROWS ONLY.  An unresolved event is the vehicle's current
        state — the Parking page reads exactly those — and a truck that has
        genuinely sat in one place for longer than the window would
        otherwise have its live row deleted out from under the operator.

        The window is measured from ``last_checked`` — which ``resolve_
        parking_event`` stamps at resolution — NOT from ``first_stopped``.
        Ageing off the START of the stop would delete a long-running case
        the same night it closed: an event that began 1095 days ago but was
        only resolved yesterday is one day old as EVIDENCE, and evidence is
        what the 1095-day window exists to protect.  ``COALESCE`` falls back
        to ``first_stopped`` for any legacy row with no stamp, and a row
        with neither compares NULL and is kept — deletion fails safe.

        Known gap: the companion ``map_image_path`` PNG is NOT removed.
        Every existing prune in this codebase deletes rows only
        (``prune_score_events`` and friends), and the object store is
        per-account and resolved through a different path, so wiring file
        cleanup in here would be the first of its kind rather than a
        pattern being followed.  At the current window nothing is eligible
        for years, so this is recorded rather than rushed — but the images
        WILL outlive their rows once the window is reached.
        """
        from datetime import datetime, timedelta, timezone
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=keep_days)
        ).isoformat()
        cur = await self._db.execute(
            "DELETE FROM parking_events "
            "WHERE account_id = ? AND resolved = 1 "
            "AND COALESCE(last_checked, first_stopped) < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return cur.rowcount

    async def map_image_overwritten_by_newer(
        self, account_id: int, event_id: int,
    ) -> bool:
        """True when this event's map file has been overwritten since.

        Legacy rows only.  The object-store key used to be
        ``{vehicle_id}.png`` and ``put`` overwrites, so every stop by a
        truck clobbered the previous stop's image while each row kept its
        own ``map_image_path`` pointing at the shared file.  A row is
        therefore stale exactly when a NEWER event carries the same path:
        the bytes on disk depict that newer stop, not this one.

        Events written after the per-event key landed have a unique path
        and can never match, so this returns False for them without any
        migration or backfill.
        """
        cur = await self._db.execute(
            "SELECT 1 FROM parking_events AS newer "
            "WHERE newer.account_id = ? "
            "AND newer.id > ? "
            "AND newer.map_image_path <> '' "
            "AND newer.map_image_path = ("
            "  SELECT map_image_path FROM parking_events "
            "  WHERE id = ? AND account_id = ?"
            ") LIMIT 1",
            (account_id, event_id, event_id, account_id),
        )
        return await cur.fetchone() is not None

    async def count_flagged_parking_events_by_vehicle(
        self, account_id: int, vehicle_ids: list[str],
    ) -> dict[str, int]:
        """``{vehicle_id: flagged events}`` for the given vehicles.

        FLAGGED = anything that is not ``safe`` and not ``geofence`` —
        i.e. unsafe stops plus unverified ones the classifier could not
        place.  Deliberately not a total: a truck that stopped 120 times
        in truck stops is doing its job, and a count that treats those
        the same as 120 shoulder stops sorts the wrong vehicles to the
        top.  The predicate is the SAME one behind the "Needs attention"
        segment and ``service.needs_attention``, so the column and the
        tab cannot disagree about what counts as bad.

        Counted in SQL, not by fetching rows and measuring the list: the
        grid needs one number per vehicle, and the alternative is pulling
        every historical event for the whole page just to call len() on
        the groups.

        Chunked at 500 to stay under SQLite's 999-parameter limit (one
        slot reserved for account_id), matching
        ``get_active_parking_events_for_vehicles`` above.
        """
        out: dict[str, int] = {}
        if not vehicle_ids:
            return out
        for i in range(0, len(vehicle_ids), 500):
            chunk = vehicle_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"SELECT vehicle_id, COUNT(*) AS n FROM parking_events "
                f"WHERE account_id = ? AND vehicle_id IN ({placeholders}) "
                f"AND location_class NOT IN ('safe', 'geofence') "
                f"GROUP BY vehicle_id",
                (account_id, *chunk),
            )
            for row in await cur.fetchall():
                d = dict(row)
                if d.get("vehicle_id"):
                    out[d["vehicle_id"]] = int(d.get("n") or 0)
        return out

    async def get_parking_events_for_vehicle(
        self, account_id: int, vehicle_id: str, limit: int = 50,
    ) -> list[dict]:
        """Every parking event for ONE vehicle, newest first.

        Backs the detail drawer's history panel.  Queried per-vehicle
        rather than filtering a whole-account fetch in Python: the answer
        is a handful of rows and the account may hold tens of thousands,
        so the filter belongs in the WHERE clause.

        Both resolved and unresolved, because the question the panel
        answers — "does this truck park badly a lot?" — is about the
        pattern, not the current state.
        """
        cur = await self._db.execute(
            "SELECT * FROM parking_events "
            "WHERE account_id = ? AND vehicle_id = ? "
            "ORDER BY COALESCE(first_stopped, created_at) DESC LIMIT ?",
            (account_id, vehicle_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_parking_points_for_heatmap(
        self, account_id: int, days: int = 30,
    ) -> list[dict]:
        """Latitude/longitude/duration triples of every parking event
        (active OR resolved) in the last ``days`` for the Owner /
        Admin utilisation heatmap on Live Map.

        Returns the minimum projection — ``{lat, lon, duration_hours}``
        per row — so the response stays small (a 100-truck fleet over
        30 days produces a few thousand points at most, comfortably
        under 100 KB).

        Skipped: zero-coordinate events (Samsara occasionally backfills
        these on cold-start vehicles) and rows where ``duration_hours``
        is missing/non-positive (would weight as 0 in the heatmap and
        just inflate the payload).
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 1))).isoformat()
        # ``company_code`` is projected even though the heatmap never
        # renders it: the caller needs it to apply the per-user company
        # restriction.  Company scope is a per-USER assignment (Team
        # Management), independent of role — only ``owner`` is
        # auto-unrestricted — so a company-restricted holder of
        # ``can_vehicle_all`` reaches this query and must not receive
        # another company's parking density.
        cur = await self._db.execute(
            "SELECT latitude, longitude, duration_hours, company_code "
            "FROM parking_events "
            "WHERE account_id = ? "
            "AND created_at >= ? "
            "AND latitude != 0 AND longitude != 0 "
            "AND duration_hours > 0",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
