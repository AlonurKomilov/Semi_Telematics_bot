"""Alert API endpoints."""

from fastapi import APIRouter, Depends, Query, HTTPException

from api.deps import require_permission
from bot.state import db

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/pending")
async def pending_alerts(
    user: dict = Depends(require_permission("can_alerts_all")),
):
    """Get all pending (unacknowledged) alerts for this account."""
    alerts = await db.get_pending_alerts(user["account_id"])
    return {"alerts": alerts, "count": len(alerts)}


@router.get("/history")
async def alert_history(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission("can_alerts_all")),
):
    """Get alert history for this account."""
    alerts = await db.get_alert_history(user["account_id"], limit=days * 50)
    return {"alerts": alerts, "count": len(alerts)}


@router.post("/{ack_id}/acknowledge")
async def acknowledge_alert(
    ack_id: int,
    user: dict = Depends(require_permission("can_alerts_all")),
):
    """Acknowledge an alert from the web UI."""
    telegram_id = int(user["sub"])
    success = await db.acknowledge_alert(ack_id, telegram_id)
    if not success:
        raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")
    return {"status": "acknowledged", "ack_id": ack_id}
