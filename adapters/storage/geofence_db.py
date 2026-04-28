"""Platform geofence zones CRUD mixin."""

from __future__ import annotations

import json
from typing import Optional


class GeofenceMixin:

    # ── Internal helpers ──────────────────────────────────────────

    def _zone_row_to_dict(self, row) -> dict:
        """Convert a raw DB row to a normalised zone dict."""
        d = dict(row)
        # Parse JSON fields; default to safe values if corrupt
        try:
            d["vertices"] = json.loads(d.get("vertices") or "[]")
        except (ValueError, TypeError):
            d["vertices"] = []
        try:
            d["notify_roles"] = json.loads(d.get("notify_roles") or "[]")
        except (ValueError, TypeError):
            d["notify_roles"] = []
        return d

    # ── Create ────────────────────────────────────────────────────

    async def add_platform_geofence(
        self,
        account_id: int,
        name: str,
        shape_type: str,
        *,
        description: str = "",
        geofence_type: str = "custom",
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_meters: Optional[float] = None,
        vertices: Optional[list] = None,
        notify_roles: Optional[list] = None,
        zone_role: str = "all",
        created_by: int = 0,
    ) -> int:
        """Create a platform-owned geofence zone. Returns the new row id."""
        if notify_roles is None:
            notify_roles = ["owner", "admin", "fleet", "safety", "dispatcher", "driver"]
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO platform_geofences
               (account_id, name, description, geofence_type, shape_type,
                latitude, longitude, radius_meters, vertices,
                notify_roles, zone_role, is_active, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                account_id, name, description, geofence_type, shape_type,
                latitude, longitude, radius_meters,
                json.dumps(vertices or []),
                json.dumps(notify_roles),
                zone_role,
                created_by, now, now,
            ),
        )
        await self._db.commit()
        return cur.lastrowid

    # ── Read ──────────────────────────────────────────────────────

    async def get_platform_geofences(
        self,
        account_id: int,
        is_active: bool = True,
    ) -> list[dict]:
        """Fetch all platform zones for an account, ordered by name."""
        cur = await self._db.execute(
            "SELECT * FROM platform_geofences "
            "WHERE account_id = ? AND is_active = ? "
            "ORDER BY name",
            (account_id, 1 if is_active else 0),
        )
        rows = await cur.fetchall()
        return [self._zone_row_to_dict(r) for r in rows]

    async def get_platform_geofence_by_id(
        self,
        account_id: int,
        zone_id: int,
    ) -> dict | None:
        """Fetch a single platform zone by id (scoped to account)."""
        cur = await self._db.execute(
            "SELECT * FROM platform_geofences WHERE id = ? AND account_id = ?",
            (zone_id, account_id),
        )
        row = await cur.fetchone()
        return self._zone_row_to_dict(row) if row else None

    # ── Update ────────────────────────────────────────────────────

    async def update_platform_geofence(
        self,
        account_id: int,
        zone_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        geofence_type: Optional[str] = None,
        notify_roles: Optional[list] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_meters: Optional[float] = None,
        vertices: Optional[list] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        """Update mutable fields on a platform zone. Returns True if a row was changed."""
        updates: list[str] = []
        params: list = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if geofence_type is not None:
            updates.append("geofence_type = ?")
            params.append(geofence_type)
        if notify_roles is not None:
            updates.append("notify_roles = ?")
            params.append(json.dumps(notify_roles))
        if latitude is not None:
            updates.append("latitude = ?")
            params.append(latitude)
        if longitude is not None:
            updates.append("longitude = ?")
            params.append(longitude)
        if radius_meters is not None:
            updates.append("radius_meters = ?")
            params.append(radius_meters)
        if vertices is not None:
            updates.append("vertices = ?")
            params.append(json.dumps(vertices))
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(self._now())
        params.extend([zone_id, account_id])

        cur = await self._db.execute(
            f"UPDATE platform_geofences SET {', '.join(updates)} "
            "WHERE id = ? AND account_id = ?",
            params,
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Delete (soft) ─────────────────────────────────────────────

    async def delete_platform_geofence(
        self,
        account_id: int,
        zone_id: int,
    ) -> bool:
        """Soft-delete a platform zone (sets is_active=0). Returns True if found."""
        return await self.update_platform_geofence(
            account_id, zone_id, is_active=False
        )
