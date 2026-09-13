"""PTI / DVIR AI tool — recent driver vehicle inspections.

Vehicle-specific (optional vehicle_name), so driver/scope isolation is
enforced by the gate (a scoped caller must name an allowed vehicle).
"""

from __future__ import annotations

import logging

from capabilities.ai.tools.registry import register_tool
from features.vehicles.resolve import company_of, resolve_for_tool


from features.inspections.templates import (
    VALID_INSPECTION_STATUSES, VALID_REVIEW_STATUSES,
)


logger = logging.getLogger("bot.ai.tools")


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

    # Which "103"? Unit numbers are reused across companies, and this
    # tool asked the store for every inspection whose vehicle_name
    # matched — so a caller was shown BOTH companies' trucks' pre-trips
    # interleaved, with no company marker anywhere in the payload, and
    # the defect counts it narrated were a merge of two trucks.
    #
    # The resolver answers the two questions the tool could not: it
    # asks WHICH company when the number is ambiguous, and it refuses a
    # truck outside the caller's vehicle access.
    resolved = None
    if vehicle:
        resolved, err = await resolve_for_tool(db, account_id, tool_args)
        if err:
            return err

    # Split the twins in the QUERY, through the company wall the adapter
    # already exposes and the dashboard route already uses.
    #
    # Not filter_to_scope: driver_inspections rows carry no provider
    # vehicle id, so the ladder could only reach its name rung here —
    # which is the comparison that merged the twins in the first place.
    # The wall goes into the SQL rather than a post-filter so the page
    # and total_matching stay honest, and an empty list already means
    # deny-all there.
    only_user_ids = None
    co = company_of(resolved)
    if co:
        try:
            from infra.platform import get_platform_db
            by_user = await get_platform_db().get_all_user_company_codes(account_id)
            only_user_ids = [
                uid for uid, codes in (by_user or {}).items()
                if any((c or "").upper() == co for c in (codes or []))
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("inspections: company wall unavailable: %s", e)
            only_user_ids = None

    page = await db.list_inspections_for_account(
        account_id,
        status=status, review_status=review,
        vehicle_name=vehicle, with_defects=with_defects, days=days,
        only_user_ids=only_user_ids,
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
            # Say WHICH truck was answered about — the payload carried no
            # company marker, so a merged answer looked like one truck's.
            "company": co or "",
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
