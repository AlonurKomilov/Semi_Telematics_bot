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
        from adapters.samsara.client import SamsaraClient
        from capabilities.ai.vision import analyze_camera_image
        # Get snapshots for the specific truck
        from core.platform import get_tenant_db as _get_tdb
        _tdb = await _get_tdb(account_id) if account_id else None
        companies = await _tdb.get_account_companies(account_id) if _tdb else []
        snap = None
        for co in companies:
            client = SamsaraClient(
                api_key=co.samsara_api_key,
                active_days=co.active_days,
            )
            try:
                snaps = await client.get_dashcam_snapshots(days=3)
                match = [
                    s for s in snaps
                    if s["vehicle_name"].lower() == vehicle.lower()
                ]
                if match:
                    snap = match[0]
                    break
            finally:
                await client.close()
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
