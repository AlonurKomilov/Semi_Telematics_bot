"""Driver-related tools."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_drivers_list",
    "description": (
        "Get the list of all active drivers in the fleet: name, ID, "
        "and contact info. Useful for answering 'who are our drivers?' "
        "or finding which driver is assigned to a vehicle."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_drivers_list(tool_args: dict, samsara_client,
                           account_id: int | None = None, db=None) -> dict:
    drivers = await samsara_client.get_drivers()
    # Filter to active drivers only
    active = [d for d in drivers if not d.get("deactivatedAtMs")]
    return {
        "driver_count": len(active),
        "drivers": [
            {
                "name": d.get("name"),
                "id": d.get("id"),
                "username": d.get("username", ""),
                "phone": d.get("phone", ""),
            }
            for d in active[:50]
        ],
    }
