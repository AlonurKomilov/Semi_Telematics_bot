"""Geofencing AI tool — list geofence zones.

Geofences are account-level zones (not per-vehicle), so there's no
Vehicle-Access scope to apply here.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_geofences",
    "description": (
        "Get all geofence zones defined in Samsara: name, address, "
        "type (circle/polygon), and coordinates."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_geofences(tool_args: dict, samsara_client,
                        account_id: int | None = None, db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    from features.geofencing.service import get_geofences as _svc_geofences
    fences = await _svc_geofences(account_id)
    return {
        "count": len(fences),
        "geofences": [
            {
                "name": f.get("name"),
                "address": (f.get("formattedAddress")
                            or f.get("address", {}).get("formattedAddress", "")),
            }
            for f in fences[:30]
        ],
    }
