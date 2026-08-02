"""Reports API endpoints — faults, fuel, health, efficiency + PDF/CSV export."""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.


import asyncio
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from interfaces.api.deps import require_permission, require_permission_any, get_user_company_codes, validate_company_access, filter_by_allowed_companies, filter_by_assigned_trucks, resolve_user_id
from capabilities.activity_trail import record_simple
from capabilities.permissions.roles import can as _can
from infra.services import get_client
from capabilities.warehouse.telemetry.service import (
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_efficiency as _svc_fleet_efficiency,
)

from capabilities.reporting.registry import (
    REPORTS_BY_KEY,
    keys_with_api_export,
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
    """Fault report — all vehicles with active fault codes.

    Reads from the warehouse (ingested every 5 min); falls back to
    live Samsara only when the warehouse is cold for this account.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    from capabilities.warehouse.telemetry import warehouse_reader as _wh
    client = await get_client(user["account_id"])

    async def _live():
        # MultiCompanyClient.get_vehicles_with_faults already returns
        # the (faulted_list, total_active, company_breakdown) 3-tuple
        # the warehouse-reader fallback expects.  The earlier shape
        # here called a non-existent ``client.get_fault_codes`` and
        # re-wrapped its result with a stub ``{}`` breakdown — both
        # wrong: it 500'd on the AttributeError, and even if the
        # method had existed the per-company breakdown would have
        # been lost.
        return await client.get_vehicles_with_faults(company=company)

    faulted, total, _bd = await _wh.get_vehicles_with_faults(
        user["account_id"], company=company, samsara_fallback=_live,
    )
    faulted = filter_by_allowed_companies(faulted or [], allowed)
    faulted = await filter_by_assigned_trucks(faulted, user)
    return {
        "vehicles": [_simplify_fault(v) for v in faulted],
        "total_vehicles": total or len(faulted),
        "faulted_count": len(faulted),
    }


@router.get("/fuel-levels")
async def report_fuel_levels(
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_fuel")),
):
    """Live fuel & DEF tank levels from telematics — not the same as
    /costs/fuel (which tracks logged fill-up entries).

    Reads from the warehouse ``vehicle_state`` snapshot (ingested every
    60 s); falls back to live Samsara only on a cold warehouse.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    from capabilities.warehouse.telemetry import warehouse_reader as _wh
    client = await get_client(user["account_id"])

    async def _live():
        # MultiCompanyClient doesn't expose the raw ``get_fuel_levels``
        # endpoint (that's a single-tenant SamsaraClient method).  The
        # warehouse fallback wants the same enriched vehicle list shape
        # that ``get_current_vehicles`` would have returned — which is
        # exactly what ``get_vehicles_overview`` produces (vehicles +
        # _fuel/_def/_faults/_location attached per vehicle).
        return await client.get_vehicles_overview(company=company)

    vehicles = await _wh.get_current_vehicles(
        user["account_id"], company=company, samsara_fallback=_live,
    )
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
#
# Report metadata (pdf/csv generators, data method, permission) comes
# from ``capabilities.reporting.registry`` — the single source of
# truth shared with the bot scheduler and dashboard.  Add a new
# exportable report there, not here.


@router.get("/export")
async def export_report(
    report_type: str = Query(..., description=f"one of: {', '.join(keys_with_api_export())}"),
    fmt: str = Query("pdf", description="pdf or csv"),
    company: str | None = Query(None),
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_faults", "can_fuel", "can_health", "can_efficiency", "can_vehicle_all")),
):
    """Download a report as PDF or CSV file."""
    from fastapi import HTTPException
    spec = REPORTS_BY_KEY.get(report_type)
    if spec is None or spec.data_method is None:
        raise HTTPException(400, f"Unknown report type: {report_type}")

    # Enforce per-type permission so callers can only export what they can read.
    if not _can(user["role"], spec.permission):
        raise HTTPException(403, f"Role '{user['role']}' cannot export {report_type} reports")

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    # ── PDF path delegates to the shared data_fetch.build_report_pdf
    #    so the dashboard download and the bot's scheduled delivery
    #    use the same upstream — eliminates the data drift previously
    #    audited.  CSV path still uses the API-direct samsara client
    #    because the bot doesn't have a CSV channel today; folding CSV
    #    in is a follow-up once we add streaming-CSV support to the
    #    data_fetch module.
    if fmt == "pdf":
        from capabilities.reporting.data_fetch import build_report_pdf
        buf, _caption, filename_stem = await build_report_pdf(
            user["account_id"], report_type,
            company=company, days=days,
        )
        if buf is None:
            from fastapi import HTTPException
            # Soft failures (e.g. camera with no snapshots) surface here
            # — the caption slot carries the explanation.
            raise HTTPException(404, _caption)
        filename = f"{filename_stem or report_type}.pdf"
        content_type = "application/pdf"
    else:
        # CSV path — keep the old direct-samsara flow.
        client = await get_client(user["account_id"])
        method = getattr(client, spec.data_method)
        if report_type == "efficiency":
            vehicles = await method(days=days, company=company)
        elif company:
            vehicles = await method(company=company)
        else:
            vehicles = await method()
        vehicles = filter_by_allowed_companies(vehicles, allowed)
        vehicles = await filter_by_assigned_trucks(vehicles, user)
        gen: Any = spec.csv_generator
        if report_type == "efficiency":
            buf = await asyncio.to_thread(gen, vehicles, days, company)
        else:
            buf = await asyncio.to_thread(gen, vehicles, company)
        filename = f"{report_type}_report.csv"
        content_type = "text/csv; charset=utf-8"

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
        await record_simple(
            tenant, user["account_id"], await resolve_user_id(user),
            "risk_summary_export", subject_type, subject_id,
            context={"audience": audience, "fmt": fmt, "days": days},
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
        await record_simple(
            tenant, user["account_id"], await resolve_user_id(user),
            "risk_summary_export", "vehicle", subject_id,
            context={"audience": audience, "via": "miniapp_self"},
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


# ── Cost Reports aliases ────────────────────────────────────────
#
# Cost-rollup endpoints physically live in ``work_orders.py`` (they
# query the work_orders + parts + tasks tables, which the Maintenance
# domain owns).  The URLs are aliased here under ``/api/reports/...``
# so the public surface matches where the Cost Reports page lives
# conceptually — a sub-page of the Reports module — and the dashboard
# only needs one prefix to remember.  The /api/work-orders/reports/*
# URLs remain for a release cycle so any pinned client (older bundle,
# integration script) doesn't break.

from features.work_orders.router import (
    report_per_vehicle as _wo_report_per_vehicle,
    report_per_task_type as _wo_report_per_task_type,
    report_per_service_task as _wo_report_per_service_task,
    report_per_part as _wo_report_per_part,
    report_per_system as _wo_report_per_system,
    report_per_assembly as _wo_report_per_assembly,
    report_per_vendor as _wo_report_per_vendor,
    report_summary as _wo_report_summary,
    report_monthly_trend as _wo_report_monthly_trend,
)

router.get("/cost-reports/per-vehicle")(_wo_report_per_vehicle)
router.get("/cost-reports/per-task-type")(_wo_report_per_task_type)
router.get("/cost-reports/per-service-task")(_wo_report_per_service_task)
router.get("/cost-reports/per-part")(_wo_report_per_part)
router.get("/cost-reports/per-system")(_wo_report_per_system)
router.get("/cost-reports/per-assembly")(_wo_report_per_assembly)
router.get("/cost-reports/per-vendor")(_wo_report_per_vendor)
router.get("/cost-reports/summary")(_wo_report_summary)
router.get("/cost-reports/monthly-trend")(_wo_report_monthly_trend)


# ── DOT Binder alias ────────────────────────────────────────────
#
# DOT Binder is a stakeholder-facing compliance PDF — same conceptual
# shape as Risk Summary, distinct from the operational Maintenance
# editing surface.  Implementation physically lives in
# ``maintenance.py`` because it queries the maintenance + work_orders
# tables that domain owns; the URL is aliased under ``/api/reports/*``
# so the public surface matches where the dashboard tab lives.  The
# legacy ``/api/maintenance/dot-binder`` URL stays for a release cycle
# so any pinned client (older bundle, integration script) doesn't
# break.

from features.maintenance.router import (
    export_dot_binder as _mt_export_dot_binder,
)

router.get("/dot-binder")(_mt_export_dot_binder)



# ═══ Per-user Scheduled-Reports subscriptions (a Reports component) ═══════════════════════════════════════════════════
# Extracted from the governance router — these endpoints belong to THIS
# domain (docs/FEATURES.md feature→component tree).  URLs unchanged.
from typing import Optional
from pydantic import BaseModel, Field
from interfaces.api.deps import (  # noqa: F811 — section-local completeness
    get_current_db_user, get_current_user, get_platform_db,
    get_tenant_db, require_permission,
)
user_router = APIRouter(prefix="/user", tags=["user"])

# ── Scheduled Reports ──────────────────────────────────────────
# Recurring report delivery (PDF via Telegram).  Underlying storage
# table is still ``digest_subscriptions`` (legacy name kept to avoid a
# migration); the API + UI surface is fully renamed to "Scheduled
# Reports".  The /subscriptions API aliases were dropped — any client
# still calling them has had one release to update.

VALID_FREQUENCIES = {"daily", "weekly", "monthly"}
VALID_REPORT_TYPES = {"faults", "fuel", "health", "efficiency", "camera"}
VALID_DELIVERY_CHANNELS = {"telegram", "email"}


class ScheduledReportRequest(BaseModel):
    frequency: str = Field("daily", pattern=r"^(daily|weekly|monthly)$")
    send_hour: int = Field(7, ge=0, le=23)
    timezone: str = "America/New_York"
    report_type: str = Field("faults", pattern=r"^(faults|fuel|health|efficiency|camera)$")
    # Delivery channels — at least one of "telegram" or "email".
    # Defaults to ["telegram"] to preserve pre-2026-06 client behaviour
    # for any older SPA bundle still in flight.  Validated below.
    delivery_channels: list[str] = Field(default_factory=lambda: ["telegram"])

    def validated_channels(self) -> list[str]:
        """Return the de-duplicated, normalized channel list.

        Raises ``HTTPException`` if invalid (empty, unknown channel,
        all-bogus).  Keeps the route handlers free of inline validation
        boilerplate and ensures one consistent error shape.
        """
        seen: list[str] = []
        for c in self.delivery_channels:
            v = (c or "").strip().lower()
            if v in VALID_DELIVERY_CHANNELS and v not in seen:
                seen.append(v)
        if not seen:
            raise HTTPException(
                status_code=422,
                detail=(
                    "delivery_channels must include at least one of "
                    f"{sorted(VALID_DELIVERY_CHANNELS)}"
                ),
            )
        return seen


@user_router.get("/scheduled-reports")
async def list_scheduled_reports(
    user: dict = Depends(require_permission("can_digest")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Return every active scheduled-report row for the current user.

    Gated on ``can_digest`` so toggling the flag OFF for a role in
    RolePermissions actually disables the dashboard surface (not just
    the bot delivery).  Without this guard the page kept loading and
    accepting writes that the bot would silently never deliver.

    Response envelope: ``{"scheduled_reports": [...]}``  (multi-schedule
    model added 2026-06).  Empty list when the user has no schedules.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    rows = await tenant_db.get_digest_subscriptions(db_user.id)
    return {"scheduled_reports": rows}


@user_router.put("/scheduled-reports")
async def upsert_scheduled_report(
    body: ScheduledReportRequest,
    user: dict = Depends(require_permission("can_digest")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Create or update the user's scheduled report delivery."""
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    channels = body.validated_channels()
    # Email delivery requires a verified address — otherwise the
    # scheduler would silently bounce, which hurts sender reputation.
    # We reject up-front so the dashboard can surface the gap as a
    # clear "verify your email" prompt instead of a mute non-delivery.
    if "email" in channels:
        if not db_user.email:
            raise HTTPException(
                status_code=422,
                detail="Email delivery requires an email address on your profile",
            )
        if not getattr(db_user, "email_verified", False):
            raise HTTPException(
                status_code=422,
                detail="Email delivery requires a verified email address — check your inbox for the verification link",
            )

    await tenant_db.subscribe_digest_ext(
        db_user.id,
        frequency=body.frequency,
        send_hour=body.send_hour,
        timezone=body.timezone,
        report_type=body.report_type,
        delivery_channels=channels,
    )
    # Return the FULL list so the dashboard can render the updated
    # schedule grid without a second roundtrip.  The multi-schedule
    # model means a single PUT can both create one row AND leave the
    # other rows visible — clients want them in the same response.
    rows = await tenant_db.get_digest_subscriptions(db_user.id)
    return {"scheduled_reports": rows}


@user_router.delete("/scheduled-reports")
async def delete_scheduled_report(
    report_type: Optional[str] = None,
    user: dict = Depends(require_permission("can_digest")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Stop scheduled report delivery.

    ``?report_type=fuel`` stops only that schedule (per-row delete from
    the dashboard or per-schedule bot wizard).  Omitting the param
    stops EVERY schedule the user has — the "Stop all" button on the
    dashboard.  Validated against the canonical report registry so a
    malformed value can't deactivate by accident.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if report_type is not None:
        if report_type not in VALID_REPORT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid report_type: must be one of {sorted(VALID_REPORT_TYPES)}",
            )
    await tenant_db.unsubscribe_digest(db_user.id, report_type=report_type)
    return {"ok": True}


