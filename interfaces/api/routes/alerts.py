"""Alert API endpoints."""

import asyncio

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from interfaces.api.deps import require_permission_any, get_tenant_db, get_user_vehicle_nums, paginate
from capabilities.alerting.service import filter_alerts_by_access

router = APIRouter(prefix="/alerts", tags=["alerts"])


async def _filter_own(user: dict, alerts: list[dict]) -> list[dict]:
    """If user has only _own permission, filter alerts to their assigned vehicles."""
    if user.get("_matched_perm") != "can_alerts_own":
        return alerts
    trucks = await get_user_vehicle_nums(user)
    return filter_alerts_by_access(alerts, trucks)


def _dedup_by_alert_key(alerts: list[dict]) -> list[dict]:
    """Deduplicate alerts that share the same alert_key.

    The table has one row per subscriber (chat_id) per alert, so the same
    alert appears N times when N subscribers are signed up. For the dashboard
    we want one row per logical alert, keeping the row with the highest
    precedence status (active > acknowledged > expired) and the latest
    created_at.
    """
    STATUS_RANK = {"active": 0, "acknowledged": 1, "expired": 2, "superseded": 3, "info": 4}
    seen: dict[str, dict] = {}
    for a in alerts:
        key = a.get("alert_key") or f"{a.get('vehicle_id')}:{a.get('alert_type')}"
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


@router.get("/pending")
async def pending_alerts(
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_own")),
    tenant_db=Depends(get_tenant_db),
):
    """Get all pending (unacknowledged) alerts for this account."""
    alerts = await tenant_db.get_pending_alerts(user["account_id"])
    alerts = await _filter_own(user, alerts)
    alerts = _dedup_by_alert_key(alerts)

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
    paginated list when only the count is needed.
    """
    alerts = await tenant_db.get_pending_alerts(user["account_id"])
    alerts = await _filter_own(user, alerts)
    alerts = _dedup_by_alert_key(alerts)
    return {"count": len(alerts)}


@router.get("/history")
async def alert_history(
    days: int = Query(7, ge=1, le=90),
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    status: str | None = Query(None, description="Filter: acknowledged, expired, active"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
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
    """Acknowledge an alert from the web UI."""
    telegram_id = int(user["sub"])
    success = await tenant_db.acknowledge_alert(ack_id, telegram_id, account_id=user["account_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")
    return {"status": "acknowledged", "ack_id": ack_id}


class BulkAckRequest(BaseModel):
    ids: list[int]


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
            tenant_db.acknowledge_alert(ack_id, telegram_id, account_id=user["account_id"])
            for ack_id in body.ids
        ),
        return_exceptions=True,
    )
    acked = sum(1 for r in results if r is True)
    failed = len(results) - acked
    return {"acked": acked, "failed": failed, "total": len(body.ids)}
