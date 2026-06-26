"""PTI / DVIR AI tool — recent driver vehicle inspections.

Vehicle-specific (optional vehicle_name), so driver/scope isolation is
enforced by the gate (a scoped caller must name an allowed vehicle).
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_recent_inspections",
    "description": (
        "List recent driver vehicle inspections (DVIR — pre-trip / "
        "post-trip).  Returns vehicle, inspector, inspection type, "
        "pass/fail status, defect list, review status, and timestamp.  "
        "Use for questions like 'any failed inspections this week', "
        "'show defects on truck 231', 'who hasn't done a pre-trip "
        "today', 'unreviewed inspections'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "Optional vehicle filter (exact match).",
            },
            "status": {
                "type": "string",
                "description": (
                    "Optional: 'pass', 'fail', 'defect'.  Defect = "
                    "passed but with non-blocking issues noted."
                ),
            },
            "review_status": {
                "type": "string",
                "description": (
                    "Optional: 'pending', 'reviewed', 'resolved'.  "
                    "Pending = not yet looked at by a manager."
                ),
            },
            "days": {
                "type": "number",
                "description": (
                    "Window in days (default 7).  Use 1 for 'today', "
                    "30 for 'this month'."
                ),
            },
        },
        "required": [],
    },
})
async def get_recent_inspections(tool_args: dict, samsara_client,
                                 account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Inspection data not available in this context"}

    vehicle = (tool_args.get("vehicle_name") or "").strip() or None
    status = (tool_args.get("status") or "").strip() or None
    review = (tool_args.get("review_status") or "").strip() or None
    days = int(tool_args.get("days") or 7)

    page = await db.list_inspections_for_account(
        account_id,
        status=status, review_status=review,
        vehicle_name=vehicle, days=days,
        page=1, page_size=30,
    )
    items = page.get("items", []) if isinstance(page, dict) else []
    total = page.get("total", len(items)) if isinstance(page, dict) else len(items)

    return {
        "count": len(items),
        "total_matching": total,
        "filters": {
            "vehicle_name": vehicle, "status": status,
            "review_status": review, "days": days,
        },
        "inspections": [
            {
                "id": r.get("id"),
                "vehicle": r.get("vehicle_name") or "",
                "inspector": r.get("inspector_name") or r.get("user_name") or "",
                "type": r.get("inspection_type") or "",
                "status": r.get("status") or "",
                "review_status": r.get("review_status") or "pending",
                "defect_count": int(r.get("defects_count") or 0),  # column is defects_count (plural)
                "inspected_at": r.get("inspected_at") or r.get("created_at") or "",
                "summary": (r.get("notes") or "")[:200],
            }
            for r in items
        ],
    }
