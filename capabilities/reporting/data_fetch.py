"""Shared data-fetch path for the recurring report PDFs.

Single source of truth for the question "give me the rendered PDF for
``account_id``'s {faults|fuel|health|efficiency|camera} report right
now."  Used by both the API ``/reports/export`` endpoint and the bot's
scheduled-delivery pipeline (``scheduled_reports._generate_report_pdf``) —
before this module existed each channel reached for a different
upstream service, so the same user could download a different vehicle
list from the dashboard than what their Telegram subscription
delivered five minutes earlier.

Channel-agnostic contract:
- ``build_report_pdf(account_id, report_type, *, company=None,
  days=7)`` returns ``(buf, caption, filename_stem)``.
- ``buf`` is a ``BytesIO`` already positioned at 0.
- ``caption`` is a short HTML-safe summary line the bot puts in the
  Telegram document caption; the API ignores it.
- ``filename_stem`` is the base of the download filename (without
  extension), used by both channels.

Camera-Check intentionally lives here too even though it has no API
export today — folding it in keeps the dispatch table small and lets
us add an API endpoint later without re-implementing.
"""

from __future__ import annotations

import asyncio
import io
from typing import Awaitable, Callable, Optional

from capabilities.reporting import compute_stats
from features.vehicles.faults.report import generate_fault_report_pdf
from features.vehicles.fuel.report import generate_fuel_report_pdf
from features.vehicles.efficiency.report import generate_fleet_efficiency_pdf
from capabilities.reporting.csv_generators import (
    generate_efficiency_csv, generate_fault_csv,
    generate_fuel_csv, generate_health_csv,
)
from features.cameras.report import generate_camera_check_pdf
from features.vehicles.health.report import generate_vehicle_health_pdf
from features.vehicles.warehouse.service import (
    get_fleet_efficiency as _svc_fleet_efficiency,
    get_vehicle_health as _svc_vehicle_health,
    get_vehicles_with_faults as _svc_vehicles_with_faults,
)
from features.vehicles.service import (
    get_vehicles_overview as _svc_vehicles_overview,
    prepare_companies as _prepare_companies,
)


# ``scope`` is how the per-CALLER narrowing reaches these builders.
# The account decides WHAT the report covers; the caller's role decides
# how much of it they may see, and only the caller's own layer knows
# that — so the filters stay in the router and arrive here as one
# callable.  Both formats take it.  The bot's scheduled delivery passes
# None, which is correct there: a digest subscription is account-wide by
# definition and has no requesting viewer to narrow to.
RowScope = Optional[Callable[[list[dict]], Awaitable[list[dict]]]]


async def _scoped(
    rows: list[dict], company: Optional[str], scope: RowScope,
) -> list[dict]:
    """Company filter first (cheap, local), then the caller's scope."""
    if company:
        rows = [v for v in rows
                if (v.get("_org") or "").upper() == company.upper()]
    if scope is not None:
        rows = await scope(rows)
    return rows


async def _fetch_faults(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001 — days unused
) -> tuple[io.BytesIO, str, str]:
    faulted, total, breakdown = await _svc_vehicles_with_faults(account_id)
    all_fleet = await _svc_vehicles_overview(account_id)
    # The company filter narrows the faulted list only — the fleet
    # overview is background context even when filtering.  The CALLER's
    # scope narrows both: it is not a view preference but a limit on
    # what this person may see, so background context is no exception.
    faulted = await _scoped(faulted, company, scope)
    all_fleet = await _scoped(all_fleet, None, scope)
    if scope is not None:
        total = len(all_fleet)
    pdf_buf = await asyncio.to_thread(
        generate_fault_report_pdf,
        faulted, total,
        company_breakdown=breakdown,
        company_filter=company,
        all_vehicles=all_fleet,
    )
    stats = compute_stats(faulted, total)
    caption = (
        f"🔧 <b>Fault Report</b>\n"
        f"📚 {stats['total']} trucks · 🔴 {stats['faulted']} faulted · "
        f"✅ {stats['clean']} clean"
    )
    return pdf_buf, caption, "Fault_Report"


async def _fetch_fuel(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001
) -> tuple[io.BytesIO, str, str]:
    vehicles = await _scoped(
        await _svc_vehicles_overview(account_id), company, scope)
    pdf_buf = await asyncio.to_thread(generate_fuel_report_pdf, vehicles, company)
    caption = f"⛽ <b>Fuel & DEF Report</b>\n📚 {len(vehicles)} trucks"
    return pdf_buf, caption, "Fuel_DEF_Report"


async def _fetch_health(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001
) -> tuple[io.BytesIO, str, str]:
    # ``get_vehicle_health``, NOT the fleet overview: the health
    # generators read ``_health`` and ``_health_alerts``, and the
    # overview carries neither.  Fed the overview, every health report
    # rendered a full truck list with zero alerts on it — a silent
    # wrong answer, which is worse than the AttributeError the CSV
    # path threw.
    vehicles = await _scoped(
        await _svc_vehicle_health(account_id, company=company), None, scope)
    pdf_buf = await asyncio.to_thread(
        generate_vehicle_health_pdf, vehicles, company,
    )
    caption = f"🏥 <b>Vehicle Health Report</b>\n📚 {len(vehicles)} trucks"
    return pdf_buf, caption, "Vehicle_Health_Report"


async def _fetch_efficiency(
    account_id: int, company: Optional[str], days: int, scope: RowScope,
) -> tuple[io.BytesIO, str, str]:
    # ``get_fleet_efficiency``, NOT the fleet overview — see
    # ``_fetch_health``.  This one did not fail quietly: the PDF
    # generator reads ``v["_engine_hours"]`` as a ``.get()`` DEFAULT,
    # which Python evaluates eagerly, so the export 500'd on a KeyError
    # for every caller.
    vehicles = await _scoped(
        await _svc_fleet_efficiency(account_id, days=days, company=company),
        None, scope)
    pdf_buf = await asyncio.to_thread(
        generate_fleet_efficiency_pdf, vehicles, days, company,
    )
    caption = f"📊 <b>Efficiency Report</b>\n📚 {len(vehicles)} trucks · last {days}d"
    return pdf_buf, caption, "Efficiency_Report"


async def _fetch_camera(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001 — company/days/scope unused
) -> tuple[Optional[io.BytesIO], str, Optional[str]]:
    # Late-import the cameras bot helpers to keep this module free of
    # interfaces.bot.* dependencies at module load time (otherwise the
    # API import path would pull in PTB indirectly).
    from interfaces.bot.cameras import (
        _gather_snapshots, _analyze_snapshot, _save_camera_results,
    )

    snapshots, _ = await _gather_snapshots(account_id)
    if not snapshots:
        return None, "📷 No dashcam footage found for camera check.", None

    sem = asyncio.Semaphore(5)
    tasks = [_analyze_snapshot(s, account_id, sem) for s in snapshots]
    results = await asyncio.gather(*tasks)

    priority = {"PROBLEM": 0, "WARNING": 1, "ERROR": 2, "OK": 3}
    results = sorted(
        results,
        key=lambda r: (priority.get(r.get("status", "OK"), 9), r["vehicle"]),
    )
    await _save_camera_results(account_id, results)

    pdf_buf = await asyncio.to_thread(generate_camera_check_pdf, results)
    problems = sum(1 for r in results if r.get("status") == "PROBLEM")
    warnings = sum(1 for r in results if r.get("status") == "WARNING")
    caption = (
        f"📷 <b>Camera Check Report</b>\n"
        f"📚 {len(results)} camera(s)"
    )
    if problems:
        caption += f" · 🚨 {problems} problem(s)"
    if warnings:
        caption += f" · ⚠️ {warnings} warning(s)"
    return pdf_buf, caption, "Camera_Check_Report"


_DISPATCH = {
    "faults":     _fetch_faults,
    "fuel":       _fetch_fuel,
    "health":     _fetch_health,
    "efficiency": _fetch_efficiency,
    "camera":     _fetch_camera,
}


# ── CSV, from the SAME upstream as the PDF ────────────────────────
#
# The CSV path used to call ``getattr(client, spec.data_method)`` on the
# Samsara client while the PDF path came through the services above.
# Two upstreams for one report is drift waiting to happen, and it
# happened: the client's methods were renamed (``get_fault_codes`` ->
# ``get_vehicles_with_faults``, ``get_fuel_levels`` ->
# ``get_low_fuel_vehicles``) and nothing tied the registry's strings to
# them, so two of the four CSV exports died with AttributeError while
# their PDFs kept working. The router comment already named this
# consolidation as the follow-up; this is it.
#
# Each builder returns the finished buffer, so the shape mismatch that
# also broke faults — a 3-argument generator called through a
# 2-argument call site — is settled here rather than in a caller that
# has to know which report is which.

async def _csv_faults(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001
):
    faulted, total, _breakdown = await _svc_vehicles_with_faults(account_id)
    faulted = await _scoped(faulted, company, scope)
    if scope is not None:
        # ``total`` is the account-wide denominator the service hands
        # back.  A caller who may not see every truck may not see every
        # truck COUNTED either, so the denominator is re-derived from
        # the same scope as the numerator.
        total = len(await _scoped(await _svc_vehicles_overview(account_id),
                                  company, scope))
    return await asyncio.to_thread(
        generate_fault_csv, faulted, total, company), "Fault_Report"


async def _csv_fuel(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001
):
    vehicles = await _scoped(
        await _svc_vehicles_overview(account_id), company, scope)
    return await asyncio.to_thread(
        generate_fuel_csv, vehicles, company), "Fuel_DEF_Report"


async def _csv_health(
    account_id: int, company: Optional[str], days: int, scope: RowScope,  # noqa: ARG001
):
    vehicles = await _scoped(
        await _svc_vehicle_health(account_id), company, scope)
    return await asyncio.to_thread(
        generate_health_csv, vehicles, company), "Vehicle_Health_Report"


async def _csv_efficiency(
    account_id: int, company: Optional[str], days: int, scope: RowScope,
):
    vehicles = await _scoped(
        await _svc_fleet_efficiency(account_id, days=days), company, scope)
    return await asyncio.to_thread(
        generate_efficiency_csv, vehicles, days, company), "Efficiency_Report"


_CSV_DISPATCH = {
    "faults": _csv_faults,
    "fuel": _csv_fuel,
    "health": _csv_health,
    "efficiency": _csv_efficiency,
}


async def build_report_csv(
    account_id: int,
    report_type: str,
    *,
    company: Optional[str] = None,
    days: int = 7,
    prepare: bool = True,
    scope: RowScope = None,
) -> tuple[io.BytesIO, str]:
    """Build a CSV report — the sibling of :func:`build_report_pdf`.

    Same account, same services, same company-filter semantics; only the
    renderer differs. Returns ``(buf, filename_stem)``.

    ``scope`` narrows every vehicle list to what THIS caller may see,
    applied after the company filter.  It is optional because the bot's
    scheduled delivery is account-wide by subscription; the API always
    passes one.

    Camera is deliberately absent: its CSV takes probe results the
    report path does not gather, and the registry already marks it as
    having no data method. A caller asking for it gets the same
    ValueError as any unknown type rather than an empty file.
    """
    handler = _CSV_DISPATCH.get(report_type)
    if handler is None:
        raise ValueError(f"Unknown CSV report type: {report_type}")
    if prepare:
        await _prepare_companies(account_id)
    return await handler(account_id, company, days, scope)


async def build_report_pdf(
    account_id: int,
    report_type: str,
    *,
    company: Optional[str] = None,
    days: int = 7,
    prepare: bool = True,
    scope: RowScope = None,
) -> tuple[Optional[io.BytesIO], str, Optional[str]]:
    """Build a PDF report for an account.

    ``report_type`` must be one of: faults, fuel, health, efficiency,
    camera (see ``capabilities.reporting.registry.REPORTS``).

    ``company`` is an optional org-code filter (uppercase comparison).
    ``days`` is the lookback window for the Efficiency report; ignored
    by the others.

    ``prepare=True`` (the default) calls
    ``vehicles.service.prepare_companies`` first — needed by the bot
    path before service calls work, no-op once cached.  Callers that
    have already prepared can pass ``prepare=False`` to skip the
    redundant call.

    Returns ``(buf, caption, filename_stem)``.  ``buf=None`` indicates
    a soft-failure (e.g. no dashcam footage for camera check); caption
    holds the explanatory message and filename_stem is None.
    """
    handler = _DISPATCH.get(report_type)
    if handler is None:
        raise ValueError(f"Unknown report type: {report_type}")

    if prepare:
        await _prepare_companies(account_id)

    return await handler(account_id, company, days, scope)
