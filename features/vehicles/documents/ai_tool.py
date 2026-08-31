"""What the assistant can answer about a truck's paperwork.

One READ tool.  Its reason to exist is the question the Documents page
cannot answer by looking: **which trucks have nothing on file**.
Expiring and expired are visible there and in the daily alert; an
ABSENCE is only visible to something that can compare the roster
against the papers, and that is what an audit actually asks first.

Scope-aware like every vehicle tool: the answer is assembled from rows
the caller could already open, so a dispatcher restricted to one
company gets that company's trucks and nobody else's.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.documents.expiration import classify, describe


@register_tool({
    "name": "get_vehicle_documents_status",
    "description": (
        "Compliance status of vehicle paperwork — registration, cab "
        "card, insurance, annual inspection, IFTA, permits. Answers "
        "'which trucks have papers expiring', 'is 110's insurance "
        "current', and 'which trucks are missing a registration'. "
        "Returns facts and dates, never the files themselves."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "missing_type": {
                "type": "string",
                "description": (
                    "Optional: report trucks with NO document of this "
                    "type on file (e.g. 'insurance', 'cab_card'). This "
                    "is the question the documents page cannot answer "
                    "by looking."
                ),
            },
            "within_days": {
                "type": "number",
                "description": (
                    "Optional: only papers expiring within this many "
                    "days. Past-due are always included."
                ),
            },
        },
        "required": [],
    },
})
async def get_vehicle_documents_status(tool_args: dict, samsara_client,
                                       account_id: int | None = None,
                                       db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    if db is None:
        return {"error": "This tool requires database context."}

    rows = await db.list_account_vehicle_documents(account_id)
    vehicles = await db.list_vehicles(account_id)

    # Scope first, then answer — never the other way round: filtering
    # after summarising would leak a count of trucks the caller cannot
    # see, which is the whole shape of a scope leak.
    live = filter_to_scope(
        [{"name": v.unit_number, "company": v.company_code, "id": v.id}
         for v in vehicles if v.is_active],
        tool_args, key="name",
    )
    allowed_ids = {v["id"] for v in live}
    rows = [r for r in rows if r.get("vehicle_id") in allowed_ids]

    missing_type = str(tool_args.get("missing_type") or "").strip().lower()
    if missing_type:
        have = {r["vehicle_id"] for r in rows
                if str(r.get("doc_type") or "") == missing_type}
        without = [v["name"] for v in live if v["id"] not in have]
        return {
            "question": f"trucks with no {missing_type} on file",
            "missing_count": len(without),
            "total_vehicles": len(live),
            "vehicles": sorted(without)[:100],
        }

    window = tool_args.get("within_days")
    due = classify(rows)
    if window is not None:
        try:
            limit = float(window)
            due = [e for e in due if e.days_left <= limit]
        except (TypeError, ValueError):
            pass

    by_name = {v["id"]: v["name"] for v in live}
    return {
        "expiring_count": sum(1 for e in due if e.days_left >= 0),
        "expired_count": sum(1 for e in due if e.days_left < 0),
        "documents": [
            {
                "vehicle": by_name.get(e.vehicle_id, e.unit_number),
                "document": e.doc_type,
                "expires": e.expires_at,
                "days_left": e.days_left,
                "status": describe(e),
            }
            # Worst first: the answer should open with what is already
            # wrong, not with what lapses in a month.
            for e in sorted(due, key=lambda x: x.days_left)[:100]
        ],
    }
