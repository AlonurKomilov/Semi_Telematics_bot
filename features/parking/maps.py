"""Map rendering and persistence for parking events."""

from __future__ import annotations

import logging

from constants import OSM_TILE_URL

logger = logging.getLogger("bot")


def _render_parking_map(lat: float, lng: float) -> bytes | None:
    """Render satellite-hybrid + road map side-by-side for AI vision analysis.

    Left panel:  Satellite imagery with labels overlay (ESRI Hybrid, zoom 17)
    Right panel:  Labeled road map (OpenStreetMap, zoom 15, ~500 m)
    Red marker shows vehicle position on both panels.

    Uses ESRI World_Imagery as base + Reference_Labels overlay for POI names
    (truck stops, weigh stations, parking areas, etc.) that raw satellite
    imagery alone would not show.

    Returns PNG bytes or None on failure.
    """
    try:
        from staticmap import StaticMap, CircleMarker
        from PIL import Image as PILImage
        import io

        pw, ph = 512, 512

        # Satellite base layer — close-up terrain / parking lot detail
        sat = StaticMap(
            pw, ph,
            url_template=(
                "https://server.arcgisonline.com/ArcGIS/rest/services/"
                "World_Imagery/MapServer/tile/{z}/{y}/{x}"
            ),
        )
        sat.add_marker(CircleMarker((lng, lat), "#ff0000", 12))
        sat_img = sat.render(zoom=18, center=[lng, lat])

        # Labels overlay on satellite — shows road names, POIs, facility names
        try:
            import numpy as np
            labels = StaticMap(
                pw, ph,
                url_template=(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/"
                    "Reference/World_Reference_Overlay/MapServer/tile/{z}/{y}/{x}"
                ),
            )
            labels_img = labels.render(zoom=18, center=[lng, lat])
            # staticmap renders to RGB, losing the tile's alpha channel.
            # The label tiles have a transparent background, but after RGB
            # conversion those pixels become white (255,255,255).  Restore
            # transparency by treating near-white pixels as transparent.
            labels_rgba = labels_img.convert("RGBA")
            arr = np.array(labels_rgba)
            white_mask = (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
            arr[white_mask, 3] = 0  # make white pixels transparent
            labels_rgba = PILImage.fromarray(arr, "RGBA")
            sat_img = sat_img.convert("RGBA")
            sat_img = PILImage.alpha_composite(sat_img, labels_rgba).convert("RGB")
        except Exception as e:
            logger.debug("Labels overlay failed, using raw satellite: %s", e)

        # Road map panel — wider area with road names & POI labels
        road = StaticMap(
            pw, ph,
            url_template=OSM_TILE_URL,
        )
        road.add_marker(CircleMarker((lng, lat), "#ff0000", 12))
        road_img = road.render(zoom=15, center=[lng, lat])

        # Combine side-by-side
        combined = PILImage.new("RGB", (pw * 2, ph))
        combined.paste(sat_img, (0, 0))
        combined.paste(road_img, (pw, 0))

        buf = io.BytesIO()
        combined.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.debug("Failed to render parking map: %s", e)
        return None


async def _save_parking_map(
    account_id: int, vehicle_id: str, lat: float, lng: float,
    tenant_db,
    company_code: str = "",
    event_id: int = 0,
    vehicle_name: str = "",
) -> str:
    """Render and save a parking-event map image to the account's
    configured object store.

    KEYED BY EVENT, not by vehicle.  The key was ``{vehicle_id}.png``,
    and ``put`` overwrites unconditionally, so every stop a truck made
    wrote over the previous stop's map: on live data 116 events for one
    vehicle shared 4 files.  Each row still carried its own
    ``map_image_path``, so the detail drawer happily showed a
    three-month-old event beside a picture of where that truck parked
    last night — correct address and AI reason, wrong map.  Harmless
    while only the current stop was viewable; the per-vehicle history
    panel made every stale pairing reachable.

    ``event_id`` is required for the per-event key.  It defaults to 0
    rather than being positional so existing callers fail loudly in
    review instead of silently reverting to the shared key — a 0 falls
    back to the legacy vehicle key, which is correct ONLY for a caller
    that genuinely has no event yet.

    Routes through ``get_object_storage_for_account`` so when an account
    has connected Google Drive, parking-map screenshots land in their
    Drive at the same hierarchy used by work orders and dashcam
    captures.  ``tenant_db`` is passed in by the caller (parking
    check loop) which already has a resolved tenant handle — avoids a
    redundant ``get_tenant_db`` call per event.

    ``company_code`` routes the image into the per-company folder.
    When the caller doesn't have it, ``vehicle_name`` lets us ask the
    vehicle registry — the SSOT for which company owns a unit — so a
    telematics payload that arrived without an org tag still files its
    map under the right company instead of the holding pen.

    Returns the URL/object-storage path on success, or "" on failure.
    """
    import asyncio
    map_bytes = await asyncio.to_thread(_render_parking_map, lat, lng)
    if not map_bytes:
        return ""
    try:
        from adapters.storage.object_storage import get_object_storage_for_account
        from capabilities.object_storage.tracking import track_for_sync_if_hybrid
        from capabilities.object_storage.paths import resolve_company_folder
        safe_vid = vehicle_id.replace("/", "_").replace("\\", "_")
        # Mirrors the work-orders + camera-images layout so a user
        # browsing their Drive sees consistent ``{COMPANY}/...``
        # folders across modules.
        if not company_code and vehicle_name:
            try:
                company_code = await tenant_db.company_code_for_unit(
                    account_id, vehicle_name,
                )
            except Exception:
                logger.warning(
                    "registry company lookup failed for unit %r acct=%s",
                    vehicle_name, account_id, exc_info=True,
                )
        company_folder = await resolve_company_folder(
            tenant_db, account_id, company_code,
        )
        bucket = f"{company_folder}/parking-maps"
        key = f"{safe_vid}-{event_id}.png" if event_id else f"{safe_vid}.png"
        store = await get_object_storage_for_account(account_id, tenant_db)
        saved = store.put(bucket, key, map_bytes)
        # Hand the file to the cloud-sync queue on a hybrid account.  A
        # no-op on disk/gdrive.  Only possible per-event: the repointer
        # rewrites parking_events.map_image_path for THIS event id, which
        # is the same reason the key stopped being per-vehicle.
        if saved and event_id:
            await track_for_sync_if_hybrid(
                store, bucket, key, saved,
                entity_type="parking_map", entity_id=int(event_id),
                file_size=len(map_bytes),
            )
        return saved
    except Exception as e:
        logger.debug("Failed to save parking map: %s", e)
        return ""
