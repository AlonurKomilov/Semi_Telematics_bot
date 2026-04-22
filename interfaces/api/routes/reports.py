"""Reports API endpoints — faults, fuel, health, efficiency + PDF/CSV export."""

import asyncio
import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from interfaces.api.deps import require_permission, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks
from core.services import get_client

from capabilities.reporting import (
    generate_fault_report_pdf,
    generate_fuel_report_pdf,
    generate_vehicle_health_pdf,
    generate_fleet_efficiency_pdf,
)
from capabilities.reporting.csv_generators import (
    generate_fault_csv,
    generate_fuel_csv,
    generate_health_csv,
    generate_efficiency_csv,
)
from capabilities.reporting.transformers import (
    simplify_fault as _simplify_fault,
    simplify_fuel as _simplify_fuel,
    simplify_health as _simplify_health,
    simplify_efficiency as _simplify_efficiency,
)

router = APIRouter(prefix="/reports", tags=["reports"])


# ── Report Data Endpoints ────────────────────────────────────

@router.get("/faults")
async def report_faults(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fault report — all vehicles with active fault codes."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_fault_codes(company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    faulted = [v for v in vehicles if v.get("fault_codes")]
    return {
        "vehicles": [_simplify_fault(v) for v in faulted],
        "total_vehicles": len(vehicles),
        "faulted_count": len(faulted),
    }


@router.get("/fuel")
async def report_fuel(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_fuel")),
):
    """Fuel & DEF levels for all vehicles."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_fuel_levels(company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    items = [_simplify_fuel(v) for v in vehicles]
    # Summary
    with_fuel = [i for i in items if i["fuel_pct"] is not None]
    avg_fuel = (
        round(sum(i["fuel_pct"] for i in with_fuel) / len(with_fuel), 1)
        if with_fuel else None
    )
    critical = len([i for i in with_fuel if (i["fuel_pct"] or 0) <= 15])
    low = len([i for i in with_fuel if 15 < (i["fuel_pct"] or 0) <= 30])
    return {
        "vehicles": items,
        "count": len(items),
        "summary": {
            "avg_fuel_pct": avg_fuel,
            "critical": critical,
            "low": low,
            "good": len(with_fuel) - critical - low,
        },
    }


@router.get("/health")
async def report_health(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_health")),
):
    """Vehicle health — battery, oil, coolant, DEF, engine data."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_vehicle_health(company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    items = [_simplify_health(v) for v in vehicles]
    alert_count = sum(len(i["alerts"]) for i in items)
    return {
        "vehicles": items,
        "count": len(items),
        "alert_count": alert_count,
    }


@router.get("/efficiency")
async def report_efficiency(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fleet efficiency — miles, fuel, idle/drive time per vehicle."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])
    vehicles = await client.get_fleet_efficiency(days=days, company=company)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    return {
        "vehicles": [_simplify_efficiency(v) for v in vehicles],
        "count": len(vehicles),
        "days": days,
    }


# ── Export Endpoints ──────────────────────────────────────────

EXPORT_TYPES = {
    "faults": {
        "pdf": generate_fault_report_pdf,
        "csv": generate_fault_csv,
        "data_method": "get_fault_codes",
        "perm": "can_faults",
    },
    "fuel": {
        "pdf": generate_fuel_report_pdf,
        "csv": generate_fuel_csv,
        "data_method": "get_fuel_levels",
        "perm": "can_fuel",
    },
    "health": {
        "pdf": generate_vehicle_health_pdf,
        "csv": generate_health_csv,
        "data_method": "get_vehicle_health",
        "perm": "can_health",
    },
    "efficiency": {
        "pdf": generate_fleet_efficiency_pdf,
        "csv": generate_efficiency_csv,
        "data_method": "get_fleet_efficiency",
        "perm": "can_faults",
    },
}


@router.get("/export")
async def export_report(
    report_type: str = Query(..., description="faults, fuel, health, efficiency"),
    fmt: str = Query("pdf", description="pdf or csv"),
    company: str | None = Query(None),
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission("can_faults")),
):
    """Download a report as PDF or CSV file."""
    if report_type not in EXPORT_TYPES:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown report type: {report_type}")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    cfg = EXPORT_TYPES[report_type]
    client = await get_client(user["account_id"])

    # Fetch data
    method_name: str = cfg["data_method"]  # type: ignore[assignment]
    method = getattr(client, method_name)
    if report_type == "efficiency":
        vehicles = await method(days=days, company=company)
    elif company:
        vehicles = await method(company=company)
    else:
        vehicles = await method()

    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)

    # Generate file
    gen: Any = cfg["pdf"] if fmt == "pdf" else cfg["csv"]
    if report_type == "efficiency":
        buf: io.BytesIO = await asyncio.to_thread(gen, vehicles, days, company)
    else:
        buf = await asyncio.to_thread(gen, vehicles, company)

    content_type = (
        "application/pdf" if fmt == "pdf"
        else "text/csv; charset=utf-8"
    )
    ext = "pdf" if fmt == "pdf" else "csv"
    filename = f"{report_type}_report.{ext}"

    return StreamingResponse(
        buf,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
