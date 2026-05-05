"""Custom (per-tenant) POI layer CRUD mixin.

Each account can register its own POI overlay layers in addition to the
hardcoded built-ins (fuel_station, weigh_station, etc.).  Three source types:

  - ``overpass``  — admin-supplied OSM filter expression, executed via the
                    same Overpass pipeline as built-in layers (cached).
  - ``pin_brand`` — UX shortcut: same storage as ``overpass`` but the query
                    is auto-built server-side from a clicked POI's brand tag.
                    Stored using ``source_type='overpass'`` — pin-brand is a
                    UI mode, not a separate runtime path.
  - ``csv``       — static rows uploaded by admin, stored in
                    ``custom_poi_points`` and served from DB (no Overpass).

Tenant isolation is by file (each tenant has its own SQLite); ``account_id``
is kept on rows for backward-compat audit and to allow defensive WHEREs.
"""

from __future__ import annotations

import json
from typing import Optional


class CustomPoiMixin:

    # ── Internal helpers ──────────────────────────────────────────

    def _custom_poi_row_to_dict(self, row) -> dict:
        d = dict(row)
        try:
            d["brand_filters"] = json.loads(d.get("brand_filters") or "[]")
        except (ValueError, TypeError):
            d["brand_filters"] = []
        d["default_on"] = bool(d.get("default_on"))
        d["is_active"] = bool(d.get("is_active"))
        return d

    # ── Layers: Create ────────────────────────────────────────────

    async def add_custom_poi_layer(
        self,
        account_id: int,
        *,
        layer_key: str,
        label: str,
        color: str,
        icon: str,
        source_type: str,
        overpass_query: Optional[str] = None,
        brand_filters: Optional[list] = None,
        default_on: bool = False,
        created_by: int = 0,
    ) -> int:
        """Create a custom POI layer.  Returns the new row id."""
        if source_type not in ("overpass", "csv"):
            raise ValueError(f"invalid source_type: {source_type}")
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO custom_poi_layers
               (account_id, layer_key, label, color, icon, source_type,
                overpass_query, brand_filters, default_on, is_active,
                created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                account_id, layer_key, label, color, icon, source_type,
                overpass_query or "",
                json.dumps(brand_filters or []),
                1 if default_on else 0,
                created_by, now, now,
            ),
        )
        await self._db.commit()
        return cur.lastrowid

    # ── Layers: Read ──────────────────────────────────────────────

    async def get_custom_poi_layers(
        self,
        account_id: int,
        is_active: bool = True,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM custom_poi_layers "
            "WHERE account_id = ? AND is_active = ? "
            "ORDER BY label",
            (account_id, 1 if is_active else 0),
        )
        rows = await cur.fetchall()
        return [self._custom_poi_row_to_dict(r) for r in rows]

    async def get_custom_poi_layer_by_id(
        self,
        account_id: int,
        layer_id: int,
    ) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM custom_poi_layers WHERE id = ? AND account_id = ?",
            (layer_id, account_id),
        )
        row = await cur.fetchone()
        return self._custom_poi_row_to_dict(row) if row else None

    # ── Layers: Update ────────────────────────────────────────────

    async def update_custom_poi_layer(
        self,
        account_id: int,
        layer_id: int,
        *,
        label: Optional[str] = None,
        color: Optional[str] = None,
        icon: Optional[str] = None,
        overpass_query: Optional[str] = None,
        brand_filters: Optional[list] = None,
        default_on: Optional[bool] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        updates: list[str] = []
        params: list = []
        if label is not None:
            updates.append("label = ?")
            params.append(label)
        if color is not None:
            updates.append("color = ?")
            params.append(color)
        if icon is not None:
            updates.append("icon = ?")
            params.append(icon)
        if overpass_query is not None:
            updates.append("overpass_query = ?")
            params.append(overpass_query)
        if brand_filters is not None:
            updates.append("brand_filters = ?")
            params.append(json.dumps(brand_filters))
        if default_on is not None:
            updates.append("default_on = ?")
            params.append(1 if default_on else 0)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(self._now())
        params.extend([layer_id, account_id])

        cur = await self._db.execute(
            f"UPDATE custom_poi_layers SET {', '.join(updates)} "
            "WHERE id = ? AND account_id = ?",
            params,
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Layers: Delete (soft) ─────────────────────────────────────

    async def delete_custom_poi_layer(
        self,
        account_id: int,
        layer_id: int,
    ) -> bool:
        return await self.update_custom_poi_layer(
            account_id, layer_id, is_active=False,
        )

    # ── Points (CSV-source layers) ────────────────────────────────

    async def replace_custom_poi_points(
        self,
        account_id: int,
        layer_id: int,
        points: list[dict],
    ) -> int:
        """Replace all stored points for a CSV-source layer.

        Each point: {name, lat, lng, brand?, properties?}.  Returns rows inserted.
        """
        async with self.transaction():
            await self._db.execute(
                "DELETE FROM custom_poi_points WHERE layer_id = ? AND account_id = ?",
                (layer_id, account_id),
            )
            inserted = 0
            for p in points:
                lat = p.get("lat")
                lng = p.get("lng")
                if lat is None or lng is None:
                    continue
                try:
                    lat_f = float(lat)
                    lng_f = float(lng)
                except (ValueError, TypeError):
                    continue
                if not (-90 <= lat_f <= 90) or not (-180 <= lng_f <= 180):
                    continue
                extra = p.get("properties")
                await self._db.execute(
                    """INSERT INTO custom_poi_points
                       (account_id, layer_id, name, brand, lat, lng, properties)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        account_id, layer_id,
                        str(p.get("name") or ""),
                        str(p.get("brand") or ""),
                        lat_f, lng_f,
                        json.dumps(extra) if isinstance(extra, dict) else "",
                    ),
                )
                inserted += 1
        return inserted

    async def get_custom_poi_points(
        self,
        account_id: int,
        layer_id: int,
        bbox: Optional[tuple[float, float, float, float]] = None,
    ) -> list[dict]:
        """Fetch points for a CSV-source layer, optionally filtered by bbox.

        bbox is (south, west, north, east).  ``account_id`` is enforced to
        prevent any cross-tenant leak even though tenant DBs are per-file.
        """
        if bbox:
            s, w, n, e = bbox
            cur = await self._db.execute(
                "SELECT id, name, brand, lat, lng, properties FROM custom_poi_points "
                "WHERE layer_id = ? AND account_id = ? "
                "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                (layer_id, account_id, s, n, w, e),
            )
        else:
            cur = await self._db.execute(
                "SELECT id, name, brand, lat, lng, properties FROM custom_poi_points "
                "WHERE layer_id = ? AND account_id = ?",
                (layer_id, account_id),
            )
        rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["properties"] = json.loads(d.get("properties") or "{}") or {}
            except (ValueError, TypeError):
                d["properties"] = {}
            out.append(d)
        return out
