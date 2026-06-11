"""Camera tools: dashcam check with AI vision analysis."""

from __future__ import annotations

import logging

from capabilities.ai.tools.registry import register_tool

logger = logging.getLogger("bot.ai.tools")


@register_tool({
    "name": "check_vehicle_camera",
    "description": (
        "Check the dashcam status for a specific vehicle: captures the "
        "latest camera image and analyzes it for obstruction, alignment, "
        "and image quality. This is per-vehicle only — for a full account "
        "camera check, direct the user to the Camera Check feature "
        "in the main menu."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number",
            },
        },
        "required": ["vehicle_name"],
    },
})
async def check_vehicle_camera(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    try:
        from capabilities.ai.vision import analyze_camera_image
        if account_id is None:
            return {"vehicle": vehicle, "error": "Camera check requires account context."}
        # Route through the cached MultiCompanyClient pool so this
        # request shares the connection pool, circuit breaker, and
        # rate-limit retries with the rest of the app.  Keys come from
        # the Integration card (dual-write keeps the legacy column in
        # sync for any not-yet-migrated reader).
        # Snapshot frames come through the media service (the SSOT
        # accessor) — it merges companies and rides the same cached
        # MultiCompanyClient pool (breaker + rate-limit retries).
        from capabilities.media.service import get_dashcam_snapshots as _svc_snaps
        snaps = await _svc_snaps(account_id, days=3)
        match = [
            s for s in snaps
            if s["vehicle_name"].lower() == vehicle.lower()
        ]
        snap = match[0] if match else None
        if not snap or not snap.get("image_bytes"):
            return {"vehicle": vehicle, "result": "No recent camera image found for this vehicle."}
        analysis = await analyze_camera_image(
            snap["image_bytes"],
            vehicle_name=vehicle,
            account_id=account_id,
        )
        return {
            "vehicle": vehicle,
            "camera_type": snap.get("camera_type", "unknown"),
            "event_time": snap.get("event_time", ""),
            "analysis": analysis,
        }
    except Exception as e:
        logger.error(f"Camera check tool failed for {vehicle}: {e}")
        return {"vehicle": vehicle, "error": f"Camera check failed: {e}"}
