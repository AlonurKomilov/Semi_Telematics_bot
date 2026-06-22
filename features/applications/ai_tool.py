"""Driver Applications AI tool — the applicant-pipeline contribution to
the AI hub.

Account-wide + permission-gated (``can_manage_applications``), so the
assistant answers a recruiter/owner/HR's pipeline questions ("how many
applications are pending", "any new applicants this week").  READ-ONLY and
PII-SAFE: it reads the no-PII list (``list_driver_applications``) and never
surfaces SSN / DOB / licence number — those are decrypted only in the
detail endpoint and must never enter the model's context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capabilities.ai.tools.registry import register_tool


def _within_days(submitted_at: str, days: int) -> bool:
    """True when ``submitted_at`` (ISO-ish) is within the last ``days``."""
    if not submitted_at:
        return False
    try:
        sd = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
        if sd.tzinfo is None:
            sd = sd.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    return sd >= cutoff


@register_tool({
    "name": "get_driver_applications",
    "description": (
        "Look up the driver-application pipeline: a count of applicants in "
        "each stage plus a list of recent ones.  Returns reference id, "
        "applicant name, stage (submitted / screening / interview / "
        "approved / rejected / withdrawn / hired), city & state, CDL "
        "class, and when they applied — NOT sensitive details (no SSN, "
        "date of birth, or licence number).  Use for questions like 'how "
        "many applications are pending', 'any new driver applications this "
        "week', 'who is in screening', 'show approved applicants ready to "
        "hire', 'how many people applied in the last 30 days'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": (
                    "Optional stage filter for the listed applicants: "
                    "'submitted', 'screening', 'interview', 'approved', "
                    "'rejected', 'withdrawn', 'hired'.  Omit for all stages."
                ),
            },
            "days": {
                "type": "number",
                "description": (
                    "Restrict the LISTED applicants to those who applied "
                    "within the last N days (omit for all time; 7 = this "
                    "week, 30 = this month).  The per-stage counts always "
                    "cover every application regardless of this."
                ),
            },
        },
        "required": [],
    },
})
async def get_driver_applications(tool_args: dict, samsara_client,
                                  account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Application data not available in this context"}

    status = (tool_args.get("status") or "").strip().lower() or None
    days_raw = tool_args.get("days")
    try:
        days = int(days_raw) if days_raw not in (None, "") else None
    except (TypeError, ValueError):
        days = None

    # No-PII list (storage excludes the encrypted SSN/DOB/licence blobs).
    rows = await db.list_driver_applications(account_id, limit=500)

    # Per-stage counts ALWAYS cover every application.
    by_stage: dict[str, int] = {}
    for r in rows:
        st = r.get("status") or "submitted"
        by_stage[st] = by_stage.get(st, 0) + 1

    # The listed applicants honour the optional stage + recency filters.
    listed = rows
    if status:
        listed = [r for r in listed if (r.get("status") or "") == status]
    if days is not None:
        listed = [r for r in listed if _within_days(r.get("submitted_at") or "", days)]

    return {
        "total": len(rows),
        "by_stage": by_stage,
        "filters": {"status": status, "days": days},
        "applications": [
            {
                "reference": r.get("reference") or "",
                "name": f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(),
                "stage": r.get("status") or "",
                "location": ", ".join(
                    x for x in (r.get("city"), r.get("state")) if x
                ),
                "cdl_class": r.get("cdl_class") or "",
                "applied": r.get("submitted_at") or "",
            }
            for r in listed[:25]
        ],
    }
