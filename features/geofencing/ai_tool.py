"""Geofencing AI tool — list geofence zones.

Geofences are account-level zones (not per-vehicle), so there's no
Vehicle-Access scope to apply here.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    # Zones belong to companies.  The REST route filters them by the
    # caller's company codes; this returned every zone to a
    # company-scoped caller.  Declaring this makes the orchestrator
    # inject ``_scope_companies`` (a server channel the model cannot
    # supply), and the handler filters by it.
    "company_scoped": True,
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
    allowed = tool_args.get("_scope_companies")
    if allowed:
        # Same rule as REST's filter_by_allowed_companies: an empty code
        # list means unrestricted (the orchestrator sends None then), a
        # list means only those companies' zones.
        allowed_set = {str(c).upper() for c in allowed}
        fences = [f for f in fences
                  if str(f.get("_org") or f.get("company") or "").upper() in allowed_set]
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
