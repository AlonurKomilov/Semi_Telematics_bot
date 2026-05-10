"""Alert API endpoints."""

import asyncio
import re

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from interfaces.api.deps import require_permission_any, get_tenant_db, get_user_vehicle_nums, paginate
from capabilities.alerting.service import filter_alerts_by_access

router = APIRouter(prefix="/alerts", tags=["alerts"])

# Strip FMI sub-codes from a fault alert_key so historical variants of
# the *same* SPN (e.g. "524133-31:..." and "524133-9:...") collapse onto
# a single canonical key.  Without this, the dashboard's pending list
# carries every FMI variation as its own row even though they're the
# same logical chronic fault — production data showed 8 variants for
# SPN 524133 alone on one truck.
_FAULT_FMI_RE = re.compile(r"(\d+)-\d+(?=\b|:)")


async def _filter_own(user: dict, alerts: list[dict]) -> list[dict]:
    """If user has only _own permission, filter alerts to their assigned vehicles."""
    if user.get("_matched_perm") != "can_alerts_own":
        return alerts
    trucks = await get_user_vehicle_nums(user)
    return filter_alerts_by_access(alerts, trucks)


def _canonical_key(alert: dict) -> str:
    """Return a dedup key that's stable across FMI variants of the same SPN.

    For fault alerts the raw alert_key embeds every (SPN, FMI) pair the
    truck reported on the cycle the row was created — and Samsara
    flips between FMI 31 / 9 / 19 / combinations for one chronic
    issue.  This canonicaliser strips the FMI suffix and dedups
    repeated SPN segments so all variants share one dedup bucket.
    """
    raw = alert.get("alert_key") or f"{alert.get('vehicle_id')}:{alert.get('alert_type')}"
    if alert.get("alert_type") != "fault":
        return raw
    stripped = _FAULT_FMI_RE.sub(r"\1", raw)
    # Two SPN-31 + SPN-9 segments collapse to "SPN|SPN" of the same SPN —
    # split the key into prefix + segments and dedup the segments.
    head, _, tail = stripped.partition(":")
    if not tail:
        return stripped
    # Walk forward to the first segment that contains an SPN number.
    # alert_key shape is e.g. "OSY:VID:524133:Desc|524133:Desc" — so the
    # vehicle-id segment is the first ":VID:" piece.  Split on "|" to
    # dedup the SPN segments while preserving the prefix.
    prefix_parts = stripped.split(":", 2)
    if len(prefix_parts) < 3:
        return stripped
    prefix = ":".join(prefix_parts[:2])
    body = prefix_parts[2]
    seen: list[str] = []
    for segment in body.split("|"):
        if segment and segment not in seen:
            seen.append(segment)
    return f"{prefix}:{'|'.join(seen)}"


def _dedup_by_alert_key(alerts: list[dict]) -> list[dict]:
    """Deduplicate alerts that share the same canonical alert key.

    The table has one row per subscriber (chat_id) per alert, plus one
    row per FMI variant for fault alerts.  For the dashboard we want
    one row per logical alert, keeping the row with the highest-priority
    status (active > acknowledged > expired) and the latest created_at.
    """
    STATUS_RANK = {"active": 0, "acknowledged": 1, "expired": 2, "superseded": 3, "info": 4}
    seen: dict[str, dict] = {}
    for a in alerts:
        key = _canonical_key(a)
        if key not in seen:
            seen[key] = a
        else:
            existing = seen[key]
            # Prefer higher-priority status
            if STATUS_RANK.get(a.get("status"), 9) < STATUS_RANK.get(existing.get("status"), 9):
                seen[key] = a
            elif a.get("created_at", "") > existing.get("created_at", ""):
                seen[key] = a
    return list(seen.values())


def _shape_history_for_pending_api(row: dict) -> dict:
    """Translate an `alert_history` row into the shape the dashboard +
    mini-app already expect for the pending list.

    Carries through:
        id              → AlertID surfaced in UI ("#1234")
        alert_type, vehicle_id, vehicle_name
        occurrence_count → "× 5 occurrences"
        first_seen, last_seen, last_detail
        status (always 'active' here)
        message          ← derived from last_detail for backward compat
        created_at       ← maps to first_seen so existing UI sorting works
        alert_key        ← stable canonical id (history.id-based)
    """
    return {
        "id": row.get("id"),
        "alert_type": row.get("alert_type"),
        "vehicle_id": row.get("vehicle_id"),
        "vehicle_name": row.get("vehicle_name"),
        # Severity + location are now SSOT on alert_history (writes by
        # pipeline.send_alert).  Frontends should read these instead of
        # re-deriving from alert_type.
        "severity": (row.get("severity") or "warning"),
        "location": row.get("location") or "",
        "occurrence_count": row.get("occurrence_count") or 1,
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "created_at": row.get("first_seen"),
        "last_detail": row.get("last_detail") or "",
        "message": row.get("last_detail") or "",
        "status": row.get("status") or "active",
        # Stable canonical key for clients that still cache by alert_key —
        # using the history.id makes it stable across re-fires.
        "alert_key": f"hist:{row.get('id')}",
    }


@router.get("/pending")
async def pending_alerts(
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    page: int = Query(1, ge=1, description="Page number"),
    # Cap raised 200 → 500 so a fleet with 300+ active logical alerts
    # can render in one round-trip on the dashboard / mini-app.  The
    # default stays 50 for backwards compat with any client that
    # doesn't pass page_size.
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Get all currently-active *logical* alerts for this account.

    Reads from `alert_history` (one row per (account, alert_type, vehicle))
    instead of `alert_acknowledgments` (one row per delivery × subscriber).
    Production data shows alert_acknowledgments has ~33× more rows than
    alert_history because every re-fire to every subscriber adds a row;
    `alert_history` is the SSOT and gives one entry per logical issue.
    """
    rows = await tenant_db.get_active_alert_history_for_account(user["account_id"])
    alerts = [_shape_history_for_pending_api(r) for r in rows]
    alerts = await _filter_own(user, alerts)

    if alert_type:
        alerts = [a for a in alerts if a.get("alert_type") == alert_type]
    if vehicle:
        q = vehicle.lower()
        alerts = [a for a in alerts if q in (a.get("vehicle_name") or "").lower()]

    paged = paginate(alerts, page, page_size)
    return {"alerts": paged["items"], "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


@router.get("/pending/count")
async def pending_alerts_count(
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Lightweight count of pending alerts respecting driver isolation.

    Used by the miniapp tab badge poll — avoids serialising the full
    paginated list when only the count is needed.  Reads
    `alert_history` (logical alerts) for consistency with /pending.
    """
    rows = await tenant_db.get_active_alert_history_for_account(user["account_id"])
    alerts = [_shape_history_for_pending_api(r) for r in rows]
    alerts = await _filter_own(user, alerts)
    return {"count": len(alerts)}


@router.get("/history")
async def alert_history(
    days: int = Query(7, ge=1, le=90),
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    status: str | None = Query(None, description="Filter: acknowledged, expired, active"),
    page: int = Query(1, ge=1, description="Page number"),
    # Cap raised 200 → 500 so a fleet with 300+ active logical alerts
    # can render in one round-trip on the dashboard / mini-app.  The
    # default stays 50 for backwards compat with any client that
    # doesn't pass page_size.
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Get alert history for this account."""
    alerts = await tenant_db.get_alert_history(user["account_id"], limit=days * 50)
    alerts = await _filter_own(user, alerts)
    alerts = _dedup_by_alert_key(alerts)

    if alert_type:
        alerts = [a for a in alerts if a.get("alert_type") == alert_type]
    if vehicle:
        q = vehicle.lower()
        alerts = [a for a in alerts if q in (a.get("vehicle_name") or "").lower()]
    if status:
        alerts = [a for a in alerts if a.get("status") == status]

    paged = paginate(alerts, page, page_size)
    return {"alerts": paged["items"], "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


@router.post("/{ack_id}/acknowledge")
async def acknowledge_alert(
    ack_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Acknowledge an alert from the web UI.

    `ack_id` is now an `alert_history.id` — the canonical AlertID that
    the /pending list returns.  The ack clears the history row and
    cascades to every delivery receipt (alert_acknowledgments) that
    belongs to the same (account, alert_type, vehicle).  A single click
    silences the whole logical alert across every recipient instead of
    requiring per-recipient acks.

    Falls back to the legacy per-delivery ack path when the supplied id
    matches an `alert_acknowledgments` row instead — preserves backward
    compatibility for any old client that still sends ack_ids.
    """
    telegram_id = int(user["sub"])
    cleared = await tenant_db.acknowledge_alert_history(
        ack_id, telegram_id, account_id=user["account_id"],
    )
    if cleared:
        return {"status": "acknowledged", "ack_id": ack_id, "scope": "history"}
    # Legacy fallback — old clients sending alert_acknowledgments.id.
    success = await tenant_db.acknowledge_alert(ack_id, telegram_id, account_id=user["account_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")
    return {"status": "acknowledged", "ack_id": ack_id, "scope": "legacy"}


class BulkAckRequest(BaseModel):
    ids: list[int]


async def _ack_one(tenant_db, alert_id: int, telegram_id: int, account_id: int) -> bool:
    """Try the new history-ack first; fall back to per-delivery ack."""
    cleared = await tenant_db.acknowledge_alert_history(alert_id, telegram_id, account_id=account_id)
    if cleared:
        return True
    return await tenant_db.acknowledge_alert(alert_id, telegram_id, account_id=account_id)


@router.post("/bulk-ack")
async def bulk_acknowledge(
    body: BulkAckRequest,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Acknowledge multiple alerts at once."""
    telegram_id = int(user["sub"])
    if not body.ids:
        return {"acked": 0, "failed": 0, "total": 0}
    # Run all acknowledges in parallel — N sequential awaits become 1 round trip
    # per row served concurrently by the asyncpg pool.
    results = await asyncio.gather(
        *(
            _ack_one(tenant_db, ack_id, telegram_id, user["account_id"])
            for ack_id in body.ids
        ),
        return_exceptions=True,
    )
    acked = sum(1 for r in results if r is True)
    failed = len(results) - acked
    return {"acked": acked, "failed": failed, "total": len(body.ids)}


# ── D2: per-alert mute ─────────────────────────────────────────────

class MuteRequest(BaseModel):
    hours: int = 24 * 7   # default 7-day mute
    reason: str = ""


@router.post("/{history_id}/mute")
async def mute_alert(
    history_id: int,
    body: MuteRequest,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Mute Telegram delivery for a specific alert (alert_history.id).

    The dashboard / mini-app still show the alert as active — only the
    Telegram pings stop until ``muted_until`` passes.  CRITICAL alerts
    ignore mutes (handled in pipeline.send_alert) so a real fire still
    pages someone.
    """
    if body.hours <= 0 or body.hours > 24 * 30:
        raise HTTPException(status_code=400, detail="hours must be in 1..720")
    telegram_id = int(user["sub"])
    mute = await tenant_db.mute_alert_history(
        history_id, account_id=user["account_id"],
        muted_by=telegram_id, hours=body.hours, reason=body.reason,
    )
    if not mute:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "muted", **mute}


@router.delete("/{history_id}/mute")
async def unmute_alert(
    history_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Drop every active mute on this alert — Telegram delivery resumes
    on the next pipeline cycle."""
    removed = await tenant_db.unmute_alert_history(
        history_id, account_id=user["account_id"],
    )
    return {"status": "unmuted", "removed": removed}


@router.get("/mutes")
async def list_active_mutes(
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """List every active (unexpired) mute for this account so the
    dashboard can render a 🔇 badge on muted alerts."""
    mutes = await tenant_db.get_active_mutes_for_account(user["account_id"])
    return {"mutes": mutes, "count": len(mutes)}


# ── D3: per-vehicle bulk-ack ───────────────────────────────────────

@router.post("/vehicle/{vehicle_id}/ack")
async def acknowledge_vehicle_alerts(
    vehicle_id: str,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Acknowledge every active alert for one vehicle in one click.

    Replaces the destructive "Ack all 359 alerts globally" UX with the
    safer "Ack the 5 alerts for Truck 200" — same 1-click ergonomics,
    much smaller blast radius.
    """
    telegram_id = int(user["sub"])
    rows = await tenant_db.get_active_alert_history_for_account(user["account_id"])
    targets = [r for r in rows if str(r.get("vehicle_id")) == str(vehicle_id)]
    if not targets:
        return {"acked": 0, "vehicle_id": vehicle_id}

    # Driver isolation: only let drivers ack alerts for their own truck(s).
    if user.get("_matched_perm") == "can_alerts_own":
        from interfaces.api.deps import get_user_vehicle_nums
        allowed = await get_user_vehicle_nums(user)
        if allowed is not None:
            allowed_lower = {(t or "").lower() for t in allowed}
            vname_match = (targets[0].get("vehicle_name") or "").lower()
            if vname_match and vname_match not in allowed_lower:
                raise HTTPException(status_code=403, detail="Vehicle not in your assignments")

    results = await asyncio.gather(
        *(
            tenant_db.acknowledge_alert_history(r["id"], telegram_id, account_id=user["account_id"])
            for r in targets
        ),
        return_exceptions=True,
    )
    acked = sum(1 for r in results if r and not isinstance(r, BaseException))
    return {"acked": acked, "total": len(targets), "vehicle_id": vehicle_id}
