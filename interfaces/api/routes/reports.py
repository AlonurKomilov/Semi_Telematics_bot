"""Reports API endpoints — faults, fuel, health, efficiency + PDF/CSV export."""

import asyncio
import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from interfaces.api.deps import require_permission, require_permission_any, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks
from capabilities.iam.permissions import can as _can
from infra.services import get_client
from capabilities.telemetry.service import (
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_efficiency as _svc_fleet_efficiency,
)

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


@router.get("/fuel-levels")
async def report_fuel_levels(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_fuel")),
):
    """Live fuel & DEF tank levels from telematics — not the same as
    /costs/fuel (which tracks logged fill-up entries)."""
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


@router.get("/fuel", include_in_schema=False)
async def _legacy_report_fuel(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_fuel")),
):
    """Deprecated alias — use /reports/fuel-levels instead."""
    return await report_fuel_levels(company=company, user=user)


@router.get("/health")
async def report_health(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_health")),
):
    """Vehicle health — battery, oil, coolant, DEF, engine data."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    vehicles = await _svc_vehicle_health(user["account_id"], company=company)
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
    user: dict = Depends(require_permission_any("can_efficiency", "can_vehicle_all")),
):
    """Fleet efficiency — miles, fuel, idle/drive time per vehicle."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    vehicles = await _svc_fleet_efficiency(user["account_id"], days=days, company=company)
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
        "perm": "can_efficiency",
    },
}


@router.get("/export")
async def export_report(
    report_type: str = Query(..., description="faults, fuel, health, efficiency"),
    fmt: str = Query("pdf", description="pdf or csv"),
    company: str | None = Query(None),
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_faults", "can_fuel", "can_health", "can_efficiency", "can_vehicle_all")),
):
    """Download a report as PDF or CSV file."""
    from fastapi import HTTPException
    if report_type not in EXPORT_TYPES:
        raise HTTPException(400, f"Unknown report type: {report_type}")

    cfg = EXPORT_TYPES[report_type]
    # Enforce per-type permission so callers can only export what they can read.
    type_perm: str = cfg["perm"]  # type: ignore[assignment]
    if not _can(user["role"], type_perm):
        raise HTTPException(403, f"Role '{user['role']}' cannot export {report_type} reports")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

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


# ── Stakeholder Risk Summary ─────────────────────────────────────

@router.get("/risk-summary")
async def report_risk_summary(
    subject_type: str = Query(..., pattern="^(driver|vehicle)$"),
    subject_id: str = Query(..., min_length=1, max_length=200),
    audience: str = Query("owner"),
    fmt: str = Query("pdf", pattern="^(pdf|csv)$"),
    days: int = Query(30, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any(
        "can_risk_report_all", "can_risk_report_own",
    )),
):
    """Universal Stakeholder Risk Summary — per-subject risk profile.

    Audience is one of insurance, owner, broker, auditor, payroll;
    unknown values fall back to ``owner``.
    """
    from fastapi import HTTPException
    from infra.platform import get_tenant_db as _get_tenant_db
    from capabilities.reporting import (
        build_risk_profile, generate_risk_summary_pdf,
        generate_risk_summary_csv, is_valid_audience,
    )
    from interfaces.api.deps import get_user_vehicle_nums

    if not is_valid_audience(audience):
        # Fall back to owner silently rather than 4xx — keeps deep-links robust.
        audience = "owner"

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    # Defense-in-depth for own-only callers (driver / restricted roles):
    # they may only target a *vehicle* subject in their assigned truck list.
    own_perm = user.get("_matched_perm") == "can_risk_report_own"
    if own_perm:
        if subject_type != "vehicle":
            raise HTTPException(
                403,
                "Own-scope callers may only request vehicle risk summaries.",
            )
        own_trucks = {t.strip().lower() for t in await get_user_vehicle_nums(user) if t}
        if subject_id.strip().lower() not in own_trucks:
            raise HTTPException(403, "Subject is not in your assigned trucks.")

    # ── Build profile ───────────────────────────────────────────
    profile = await build_risk_profile(
        user["account_id"],
        subject_type=subject_type,
        subject_id=subject_id,
        days=days,
        company=company,
    )

    # ── Render ──────────────────────────────────────────────────
    account_label = company or ""
    if fmt == "pdf":
        buf = await asyncio.to_thread(
            generate_risk_summary_pdf, profile,
            audience=audience, account_label=account_label,
        )
        media = "application/pdf"
        ext = "pdf"
    else:
        buf = await asyncio.to_thread(
            generate_risk_summary_csv, profile, audience=audience,
        )
        media = "text/csv; charset=utf-8"
        ext = "csv"

    # ── Audit log ───────────────────────────────────────────────
    try:
        tenant = await _get_tenant_db(user["account_id"])
        await tenant.add_audit_log(
            user["account_id"], int(user["sub"]),
            "risk_summary_export",
            target_type=subject_type, target_id=str(subject_id),
            details=f"audience={audience} fmt={fmt} days={days}",
        )
    except Exception:
        # Audit failure must not break export delivery.
        pass

    safe_subject = "".join(c if c.isalnum() or c in "-_." else "_"
                           for c in subject_id)[:64]
    filename = f"risk_summary_{audience}_{subject_type}_{safe_subject}.{ext}"
    return StreamingResponse(
        buf,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/risk-summary/me")
async def report_risk_summary_me(
    audience: str = Query("payroll"),
    fmt: str = Query("pdf", pattern="^(pdf|csv)$"),
    days: int = Query(30, ge=1, le=90),
    user: dict = Depends(require_permission_any(
        "can_risk_report_all", "can_risk_report_own",
    )),
):
    """Self-service Risk Summary for the calling user (miniapp shortcut).

    Resolves the caller's primary assigned vehicle and emits a risk
    summary for that subject. Defaults to the ``payroll`` audience —
    the most appropriate audience for driver-facing self-service.
    """
    from fastapi import HTTPException
    from interfaces.api.deps import get_user_vehicle_nums
    from infra.platform import get_tenant_db as _get_tenant_db
    from capabilities.reporting import (
        build_risk_profile, generate_risk_summary_pdf,
        generate_risk_summary_csv, is_valid_audience,
    )

    if not is_valid_audience(audience):
        audience = "payroll"

    trucks = await get_user_vehicle_nums(user)
    if not trucks:
        raise HTTPException(404, "No vehicle assignment for caller.")

    subject_id = trucks[0]
    profile = await build_risk_profile(
        user["account_id"],
        subject_type="vehicle",
        subject_id=subject_id,
        days=days,
    )

    if fmt == "pdf":
        buf = await asyncio.to_thread(
            generate_risk_summary_pdf, profile,
            audience=audience, account_label="",
        )
        media = "application/pdf"
        ext = "pdf"
    else:
        buf = await asyncio.to_thread(
            generate_risk_summary_csv, profile, audience=audience,
        )
        media = "text/csv; charset=utf-8"
        ext = "csv"

    try:
        tenant = await _get_tenant_db(user["account_id"])
        await tenant.add_audit_log(
            user["account_id"], int(user["sub"]),
            "risk_summary_export",
            target_type="vehicle", target_id=str(subject_id),
            details=f"audience={audience} via=miniapp_self",
        )
    except Exception:
        pass

    safe_subject = "".join(c if c.isalnum() or c in "-_." else "_"
                           for c in subject_id)[:64]
    filename = f"risk_summary_{audience}_vehicle_{safe_subject}.{ext}"
    return StreamingResponse(
        buf,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
