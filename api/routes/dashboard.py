"""Dashboard overview stats endpoint."""

from fastapi import APIRouter, Depends, Query

from api.deps import get_current_user, get_tenant_db
from bot.state import get_client

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
async def dashboard_stats(
    company: str | None = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Aggregated fleet stats for the overview page."""
    account_id = user["account_id"]
    client = await get_client(account_id)

    overview = await client.get_fleet_overview(company=company)

    total = len(overview)
    moving = sum(1 for v in overview if v.get("speed_mph", 0) and v["speed_mph"] > 0)
    idle = sum(
        1 for v in overview
        if v.get("engineState") == "On" and not v.get("speed_mph")
    )
    stopped = total - moving - idle

    faulted = sum(1 for v in overview if v.get("faults"))
    low_fuel = sum(
        1 for v in overview
        if v.get("fuelPercent") is not None and v["fuelPercent"] < 20
    )

    pending_alerts = await tenant_db.get_pending_alerts(account_id)
    alert_count = len(pending_alerts) if pending_alerts else 0

    return {
        "fleet": {
            "total": total,
            "moving": moving,
            "idle": idle,
            "stopped": stopped,
        },
        "faults": faulted,
        "low_fuel": low_fuel,
        "pending_alerts": alert_count,
    }
