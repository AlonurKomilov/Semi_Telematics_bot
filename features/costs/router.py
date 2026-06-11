"""Costs API endpoints — fuel cost tracking + cost-per-mile."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import require_permission, get_tenant_db, get_user_company_codes, validate_company_access, filter_by_assigned_trucks, resolve_user_id
from capabilities.costs.service import compute_fleet_cpm, summarize_fuel_entries

router = APIRouter(prefix="/costs", tags=["costs"])


# ── Fuel Cost Tracking ────────────────────────────────────────

class FuelEntryCreate(BaseModel):
    vehicle_name: str = Field(..., min_length=1)
    company_code: str = ""
    gallons: float = Field(..., gt=0)
    price_per_gallon: float = Field(..., gt=0)
    odometer_miles: float = Field(..., ge=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("/fuel")
async def fuel_entries(
    vehicle: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(require_permission("can_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Get fuel fill-up entries, optionally filtered by vehicle."""
    entries = await tenant_db.get_fuel_entries(
        user["account_id"],
        vehicle_name=vehicle,
        limit=limit,
    )
    entries = await filter_by_assigned_trucks(entries, user, name_key="vehicle_name")
    return {"entries": entries, "count": len(entries)}


@router.post("/fuel")
async def add_fuel_entry(
    body: FuelEntryCreate,
    user: dict = Depends(require_permission("can_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Log a new fuel fill-up entry."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, body.company_code or None)
    row_id = await tenant_db.add_fuel_entry(
        account_id=user["account_id"],
        company_code=body.company_code,
        vehicle_name=body.vehicle_name,
        gallons=body.gallons,
        price_per_gallon=body.price_per_gallon,
        odometer_miles=body.odometer_miles,
        date=body.date,
        created_by=await resolve_user_id(user),
    )
    return {"id": row_id, "status": "created"}


@router.get("/fuel/summary")
async def fuel_summary(
    user: dict = Depends(require_permission("can_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle fuel cost summary — total gallons, cost, avg price, miles, MPG."""
    rows = await tenant_db.get_fuel_summary(user["account_id"])
    rows = await filter_by_assigned_trucks(rows, user, name_key="vehicle_name")
    return summarize_fuel_entries(rows)


# ── Cost Per Mile ─────────────────────────────────────────────

@router.get("/cpm")
async def cost_per_mile(
    user: dict = Depends(require_permission("can_cost_per_mile")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle cost-per-mile: fuel cost ÷ miles driven."""
    rows = await tenant_db.get_fuel_summary(user["account_id"])
    rows = await filter_by_assigned_trucks(rows, user, name_key="vehicle_name")
    results, fleet = compute_fleet_cpm(rows)

    items = []
    for r in results:
        items.append({
            "vehicle_name": r["truck"],
            "company": r["company"],
            "miles": r["miles"],
            "total_cost": r["cost"],
            "gallons": r["gallons"],
            "cpm": r["cpm"],
            "mpg": r["mpg"],
        })

    return {
        "vehicles": items,
        "count": len(items),
        "fleet_avg_cpm": fleet["fleet_cpm"],
        "fleet_avg_mpg": fleet["fleet_mpg"],
        "fleet_total_miles": fleet["fleet_miles"],
        "fleet_total_cost": fleet["fleet_cost"],
    }
