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
) -> str:
    """Render and save a parking map image to the configured object
    store for dashboard display.

    Returns the URL/relative path on success, or "" on failure.
    """
    import asyncio
    map_bytes = await asyncio.to_thread(_render_parking_map, lat, lng)
    if not map_bytes:
        return ""
    try:
        from adapters.storage.object_store import get_object_store
        safe_vid = vehicle_id.replace("/", "_").replace("\\", "_")
        key = f"{account_id}_{safe_vid}.png"
        return get_object_store().put("parking_maps", key, map_bytes)
    except Exception as e:
        logger.debug("Failed to save parking map: %s", e)
        return ""
