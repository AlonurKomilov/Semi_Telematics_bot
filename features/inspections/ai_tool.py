"""PTI / DVIR AI tool — recent driver vehicle inspections.

Vehicle-specific (optional vehicle_name), so driver/scope isolation is
enforced by the gate (a scoped caller must name an allowed vehicle).
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


from features.inspections.templates import (
    VALID_INSPECTION_STATUSES, VALID_REVIEW_STATUSES,
)


@register_tool({
    "name": "get_recent_inspections",
    "description": (
        "List recent driver vehicle inspections (DVIR — pre-trip / "
        "post-trip).  Returns vehicle, inspector, inspection type, "
        "lifecycle status, how many defects were found, review status "
        "and timestamp.  Use for 'any inspections that found something "
        "this week' (with_defects=true), 'show defects on truck 231', "
        "'who hasn't done a pre-trip today', 'inspections nobody has "
        "reviewed' (review_status=... — see that parameter).  There is "
        "no pass/fail column: an inspection that found nothing has "
        "defect_count 0."
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
                "enum": sorted(VALID_INSPECTION_STATUSES),
                "description": (
                    "Optional LIFECYCLE filter — where the inspection "
                    "has got to, not whether it passed.  Nothing here "
                    "means 'failed'; use with_defects for that."
                ),
            },
            "with_defects": {
                "type": "boolean",
                "description": (
                    "Optional: true for inspections that FOUND "
                    "something (one or more defects), false for the "
                    "clean ones.  This is the 'failed inspections' "
                    "filter."
                ),
            },
            "review_status": {
                "type": "string",
                "enum": sorted(VALID_REVIEW_STATUSES),
                "description": (
                    "Optional: what a manager decided after reading it. "
                    "An inspection nobody has reviewed has no value "
                    "here — filter on status='submitted' for those."
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

    from capabilities.ai.tools import tool_error

    vehicle = (tool_args.get("vehicle_name") or "").strip() or None
    status = (tool_args.get("status") or "").strip().lower() or None
    review = (tool_args.get("review_status") or "").strip().lower() or None
    days = int(tool_args.get("days") or 7)
    raw_defects = tool_args.get("with_defects")
    with_defects = None if raw_defects is None else bool(raw_defects)

    # A value outside the vocabulary used to filter to nothing, and an
    # empty list reads to the model as "there are none" — a clean bill
    # of health on exactly the question somebody asked because they were
    # worried.  Say what went wrong instead of answering zero.
    if status and status not in VALID_INSPECTION_STATUSES:
        return tool_error(
            f"status must be one of {', '.join(sorted(VALID_INSPECTION_STATUSES))}. "
            "There is no pass/fail status — use with_defects=true for "
            "inspections that found something."
        )
    if review and review not in VALID_REVIEW_STATUSES:
        return tool_error(
            f"review_status must be one of {', '.join(sorted(VALID_REVIEW_STATUSES))}. "
            "An inspection nobody has reviewed yet has none of these — "
            "filter on status='submitted' instead."
        )

    page = await db.list_inspections_for_account(
        account_id,
        status=status, review_status=review,
        vehicle_name=vehicle, with_defects=with_defects, days=days,
        page=1, page_size=30,
    )
    items = page.get("items", []) if isinstance(page, dict) else []
    total = page.get("total", len(items)) if isinstance(page, dict) else len(items)

    return {
        "count": len(items),
        "total_matching": total,
        "filters": {
            "vehicle_name": vehicle, "status": status,
            "review_status": review, "with_defects": with_defects,
            "days": days,
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
