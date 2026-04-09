"""Costs API endpoints — fuel cost tracking + cost-per-mile."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.deps import require_permission, get_tenant_db

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
    return {"entries": entries, "count": len(entries)}


@router.post("/fuel")
async def add_fuel_entry(
    body: FuelEntryCreate,
    user: dict = Depends(require_permission("can_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Log a new fuel fill-up entry."""
    row_id = await tenant_db.add_fuel_entry(
        account_id=user["account_id"],
        company_code=body.company_code,
        vehicle_name=body.vehicle_name,
        gallons=body.gallons,
        price_per_gallon=body.price_per_gallon,
        odometer_miles=body.odometer_miles,
        date=body.date,
        created_by=int(user["sub"]),
    )
    return {"id": row_id, "status": "created"}


@router.get("/fuel/summary")
async def fuel_summary(
    user: dict = Depends(require_permission("can_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle fuel cost summary — total gallons, cost, avg price."""
    rows = await tenant_db.get_fuel_summary(user["account_id"])
    items = []
    fleet_cost = 0.0
    fleet_gallons = 0.0
    for r in rows:
        total_cost = r.get("total_cost") or 0
        total_gal = r.get("total_gallons") or 0
        fleet_cost += total_cost
        fleet_gallons += total_gal
        items.append({
            "vehicle_name": r.get("vehicle_name", ""),
            "company": r.get("company_code", ""),
            "entries": r.get("entries", 0),
            "total_gallons": round(total_gal, 1),
            "total_cost": round(total_cost, 2),
            "avg_price": round(r.get("avg_price") or 0, 3),
            "first_odo": r.get("first_odo"),
            "last_odo": r.get("last_odo"),
        })
    return {
        "vehicles": items,
        "count": len(items),
        "fleet_total_cost": round(fleet_cost, 2),
        "fleet_total_gallons": round(fleet_gallons, 1),
    }


# ── Cost Per Mile ─────────────────────────────────────────────

@router.get("/cpm")
async def cost_per_mile(
    user: dict = Depends(require_permission("can_cost_per_mile")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle cost-per-mile: fuel cost ÷ miles driven."""
    rows = await tenant_db.get_fuel_summary(user["account_id"])
    items = []
    fleet_cost = 0.0
    fleet_miles = 0.0
    fleet_gallons = 0.0
    for r in rows:
        total_cost = r.get("total_cost") or 0
        total_gal = r.get("total_gallons") or 0
        first_odo = r.get("first_odo") or 0
        last_odo = r.get("last_odo") or 0
        miles = last_odo - first_odo if last_odo > first_odo else 0
        cpm = total_cost / miles if miles > 0 else 0
        mpg = miles / total_gal if total_gal > 0 and miles > 0 else 0

        fleet_cost += total_cost
        fleet_miles += miles
        fleet_gallons += total_gal

        items.append({
            "vehicle_name": r.get("vehicle_name", ""),
            "company": r.get("company_code", ""),
            "miles": round(miles),
            "total_cost": round(total_cost, 2),
            "gallons": round(total_gal, 1),
            "cpm": round(cpm, 2),
            "mpg": round(mpg, 1),
        })

    fleet_cpm = fleet_cost / fleet_miles if fleet_miles > 0 else 0
    fleet_mpg = fleet_miles / fleet_gallons if fleet_gallons > 0 else 0

    return {
        "vehicles": items,
        "count": len(items),
        "fleet_avg_cpm": round(fleet_cpm, 2),
        "fleet_avg_mpg": round(fleet_mpg, 1),
        "fleet_total_miles": round(fleet_miles),
        "fleet_total_cost": round(fleet_cost, 2),
    }
