"""Costs API endpoints — fuel cost tracking + cost-per-mile."""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import require_permission, get_tenant_db, get_user_company_codes, validate_company_access, filter_by_assigned_trucks, filter_by_allowed_companies, resolve_user_id
from features.costs.service import compute_fleet_cpm, summarize_fuel_entries

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
    user: dict = Depends(require_permission("can_view_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Get fuel fill-up entries, optionally filtered by vehicle.

    ``count`` is the number of entries RETURNED.  ``total`` is how many
    the account actually holds — present only when this caller sees the
    account unrestricted, because the two permission filters below run in
    Python AFTER the SQL limit, so an account-wide count would overstate
    what a scoped caller can reach.  Omitted rather than approximated: a
    consumer treats a missing total as "no claim", and a wrong one as
    truth.

    The distinction is not cosmetic.  With ``limit`` defaulting to 50, a
    30-truck fleet's fuel page was pivoting roughly two days of fill-ups
    and presenting it as the account's fuel spend — a cross-tab shows no
    rows, so there was nothing on screen to notice the shortfall by.
    """
    allowed = await get_user_company_codes(user)
    entries = await tenant_db.get_fuel_entries(
        user["account_id"],
        vehicle_name=vehicle,
        limit=limit,
    )
    entries = await filter_by_assigned_trucks(entries, user, name_key="vehicle_name")
    entries = filter_by_allowed_companies(entries, allowed, key="company_code")
    out: dict = {"entries": entries, "count": len(entries)}
    # A driver is scoped to assigned trucks; company codes scope everyone
    # else.  Either one makes the account-wide aggregate the wrong number
    # for this caller.  ``vehicle`` narrows too, and by the same rule the
    # account total would not describe it.
    unrestricted = not allowed and str(user.get("role")) != "driver" and not vehicle
    if unrestricted:
        stats = await tenant_db.get_fuel_entry_stats(user["account_id"])
        out["total"] = int(stats.get("count") or 0)
    return out


@router.post("/fuel")
async def add_fuel_entry(
    body: FuelEntryCreate,
    user: dict = Depends(require_permission("can_view_fuel_cost")),
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
    user: dict = Depends(require_permission("can_view_fuel_cost")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle fuel cost summary — total gallons, cost, avg price, miles, MPG."""
    rows = await tenant_db.get_fuel_summary(user["account_id"])
    rows = await filter_by_assigned_trucks(rows, user, name_key="vehicle_name")
    rows = filter_by_allowed_companies(rows, await get_user_company_codes(user), key="company_code")
    return summarize_fuel_entries(rows)


# ── Cost Per Mile ─────────────────────────────────────────────

@router.get("/cpm")
async def cost_per_mile(
    user: dict = Depends(require_permission("can_view_cost_per_mile")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle cost-per-mile: fuel cost ÷ miles driven."""
    rows = await tenant_db.get_fuel_summary(user["account_id"])
    rows = await filter_by_assigned_trucks(rows, user, name_key="vehicle_name")
    rows = filter_by_allowed_companies(rows, await get_user_company_codes(user), key="company_code")
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
        "aggregate_avg_cpm": fleet["aggregate_cpm"],
        "aggregate_avg_mpg": fleet["aggregate_mpg"],
        "aggregate_total_miles": fleet["aggregate_miles"],
        "aggregate_total_cost": fleet["aggregate_cost"],
    }
