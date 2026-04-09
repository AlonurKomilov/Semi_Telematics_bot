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

    async def resolve_parking_event(
        self, account_id: int, vehicle_id: str,
    ) -> bool:
        """Mark a parking event as resolved (vehicle moved)."""
        await self._db.execute(
            "UPDATE parking_events SET resolved = 1, last_checked = ? "
            "WHERE account_id = ? AND vehicle_id = ? AND resolved = 0",
            (self._now(), account_id, vehicle_id),
        )
        await self._db.commit()
        return True

    async def update_parking_alert_level(
        self, event_id: int, alert_level: str, ai_analysis: str = "",
        account_id: int = 0,
    ) -> bool:
        """Update the alert level and AI analysis for a parking event.

        If account_id is provided, the row must belong to that account.
        """
        now = self._now()
        acct_filter = " AND account_id = ?" if account_id else ""
        if ai_analysis:
            params: list = [alert_level, ai_analysis, now, event_id]
            if account_id:
                params.append(account_id)
            cur = await self._db.execute(
                "UPDATE parking_events SET alert_level = ?, ai_analysis = ?, "
                f"last_checked = ? WHERE id = ?{acct_filter}",
                params,
            )
        else:
            params = [alert_level, now, event_id]
            if account_id:
                params.append(account_id)
            cur = await self._db.execute(
                "UPDATE parking_events SET alert_level = ?, "
                f"last_checked = ? WHERE id = ?{acct_filter}",
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
