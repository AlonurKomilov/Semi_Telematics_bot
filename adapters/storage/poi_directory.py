"""Built-in POI points — PLATFORM-owned map furniture, imported from OSM.

The seven built-in layers (fuel, DEF, truck parking, showers, weigh
stations, rest areas, repair shops) describe things that DO NOT MOVE.
A truck stop opened last year is in the same place today, which is why
asking a live query service for them on every map pan was the wrong
shape: the panel's layers went blank whenever a volunteer Overpass
mirror was busy, and on 2026-09-13 all three reachable mirrors refused a
one-node query within the same hour.

So they are imported on a schedule and served from here.  What changes:

    every map pan   →  a bbox query against this table (milliseconds)
    a mirror outage →  nothing; the data is already ours
    freshness       →  our import date, shown on the map, on our schedule

NOT tenant data, and deliberately no ``account_id``: a fuel stop is the
same fuel stop for every account, exactly like ``vendor_directory``
beside it.  An account's OWN layers stay in ``custom_poi`` — that module
scopes every query by account, and this one must never be read as if it
did.

IDENTITY IS OSM'S.  A point is keyed by (layer, osm_type, osm_id), so a
re-import UPDATES what moved or was renamed rather than growing a second
copy.  A point that vanished from OSM is removed only when the layer
imported CLEANLY — a half-finished run must never empty a layer, which
is the same rule the serving path learned the hard way.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class PoiDirectoryMixin:
    """Read and write the platform's imported POI points."""

    # ── read (the map's hot path) ─────────────────────────────────

    async def poi_points_in_bbox(
        self, layer: str, south: float, west: float, north: float, east: float,
        limit: int = 6000,
    ) -> list[dict]:
        """One layer's points inside a viewport.

        The cap mirrors the Overpass path's ``_MAX_POI_RESULTS`` so a
        wide view answers with the same shape it always did.
        """
        cur = await self._db.execute(
            "SELECT osm_type, osm_id, lat, lng, name, props "
            "FROM poi_points "
            "WHERE layer = ? "
            "  AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? "
            f"LIMIT {int(limit)}",
            (layer, south, north, west, east),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            raw = r.pop("props", None)
            try:
                r["props"] = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                # A row we cannot parse is a row with no extra tags, not
                # a broken map.
                r["props"] = {}
        return rows

    async def poi_layer_imports(self) -> dict[str, dict]:
        """layer → its last import, for the freshness line and the
        operator's view of whether the job is running."""
        cur = await self._db.execute(
            "SELECT layer, imported_at, points, ok, note FROM poi_imports")
        return {r["layer"]: dict(r) for r in [dict(x) for x in await cur.fetchall()]}

    async def poi_layer_imported_at(self, layer: str) -> Optional[str]:
        """When this layer's points were last replaced SUCCESSFULLY, or
        None if that has never happened.

        NOT filtered on ``ok``, and a test had to prove why: ``ok`` is
        the LAST RUN's outcome and ``imported_at`` is the last GOOD one,
        and they are different questions.  Filtering the date by the flag
        meant one failed run hid the date the map could still vouch for —
        so a layer full of last week's truck stops would have reported
        itself as never imported, and the panel would have said "not
        loaded yet" over data it was drawing.

        ``imported_at`` is only ever written on success, so an empty
        string is a layer that has never finished a run.
        """
        cur = await self._db.execute(
            "SELECT imported_at FROM poi_imports WHERE layer = ?", (layer,))
        row = await cur.fetchone()
        at = dict(row)["imported_at"] if row else ""
        return at or None

    # ── write (the import job) ────────────────────────────────────

    async def upsert_poi_points(
        self, layer: str, points: list[dict], stamp: str,
    ) -> int:
        """Add or refresh points of one layer.  Keyed by OSM identity, so
        a re-import moves and renames rather than duplicating.

        ``stamp`` IS THE RUN'S OWN, and the same string the run later
        hands ``finish_poi_import`` — not ``now()``.  The sweep that
        removes what left OSM compares this column against that value as
        TEXT, so the two have to be written from one source in one
        format; a database ``now()::text`` and a Python isoformat() sort
        differently and the sweep would have deleted the wrong side.

        Does NOT remove anything — see ``finish_poi_import``.
        """
        n = 0
        for p in points:
            await self._db.execute(
                """INSERT INTO poi_points
                       (layer, osm_type, osm_id, lat, lng, name, props, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (layer, osm_type, osm_id) DO UPDATE SET
                     lat = excluded.lat, lng = excluded.lng,
                     name = excluded.name, props = excluded.props,
                     updated_at = excluded.updated_at""",
                (layer, str(p.get("osm_type") or "node"), int(p.get("osm_id") or 0),
                 float(p["lat"]), float(p["lng"]), str(p.get("name") or ""),
                 json.dumps(p.get("props") or {}, separators=(",", ":")), stamp),
            )
            n += 1
        return n

    async def finish_poi_import(
        self, layer: str, stamp: str, points: int, ok: bool, note: str = "",
    ) -> None:
        """Close one layer's import run.

        ON SUCCESS ONLY, points not touched by this run are deleted —
        they left OSM.  On failure nothing is removed and the previous
        ``imported_at`` stands, so a run that died halfway leaves the map
        showing last week's data instead of an empty layer.  That is the
        same rule the /pois endpoint learned: a source that did not
        answer is not an area with nothing in it.
        """
        if ok:
            await self._db.execute(
                "DELETE FROM poi_points WHERE layer = ? AND updated_at < ?",
                (layer, stamp))
        # Only a SUCCESSFUL run may write the date.  On the first-ever
        # run this matters at the INSERT, not just the update: a layer
        # whose very first import failed must not end up carrying that
        # run's timestamp as though it had data from then.
        await self._db.execute(
            """INSERT INTO poi_imports (layer, imported_at, points, ok, note)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (layer) DO UPDATE SET
                 imported_at = CASE WHEN excluded.ok = 1
                                    THEN excluded.imported_at
                                    ELSE poi_imports.imported_at END,
                 points = CASE WHEN excluded.ok = 1
                               THEN excluded.points ELSE poi_imports.points END,
                 ok = excluded.ok,
                 note = excluded.note""",
            (layer, stamp if ok else "", int(points), 1 if ok else 0, note[:400]),
        )

    async def count_poi_points(self, layer: Optional[str] = None) -> dict[str, int]:
        """layer → row count.  The operator's answer to "did it import?"."""
        sql = "SELECT layer, COUNT(*) AS n FROM poi_points"
        params: tuple[Any, ...] = ()
        if layer is not None:
            sql += " WHERE layer = ?"
            params = (layer,)
        sql += " GROUP BY layer"
        cur = await self._db.execute(sql, params)
        return {r["layer"]: int(r["n"]) for r in [dict(x) for x in await cur.fetchall()]}
