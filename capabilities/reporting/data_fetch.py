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
from typing import Optional

from capabilities.reporting import compute_stats
from features.vehicles.faults.report import generate_fault_report_pdf
from features.vehicles.fuel.report import generate_fuel_report_pdf
from features.vehicles.efficiency.report import generate_fleet_efficiency_pdf
from features.vehicles.cameras.report import generate_camera_check_pdf
from features.vehicles.health.report import generate_vehicle_health_pdf
from capabilities.telemetry.service import (
    get_vehicles_with_faults as _svc_vehicles_with_faults,
)
from features.vehicles.service import (
    get_fleet_overview as _svc_fleet_overview,
    prepare_companies as _prepare_companies,
)


async def _fetch_faults(
    account_id: int, company: Optional[str], days: int,  # noqa: ARG001 — days unused
) -> tuple[io.BytesIO, str, str]:
    faulted, total, breakdown = await _svc_vehicles_with_faults(account_id)
    all_fleet = await _svc_fleet_overview(account_id)
    # Apply company filter to the faulted list only — the fleet overview
    # is still useful as background context even when filtering.
    if company:
        faulted = [v for v in faulted if (v.get("_org") or "").upper() == company.upper()]
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
    account_id: int, company: Optional[str], days: int,  # noqa: ARG001
) -> tuple[io.BytesIO, str, str]:
    vehicles = await _svc_fleet_overview(account_id)
    if company:
        vehicles = [v for v in vehicles if (v.get("_org") or "").upper() == company.upper()]
    pdf_buf = await asyncio.to_thread(generate_fuel_report_pdf, vehicles, company)
    caption = f"⛽ <b>Fuel & DEF Report</b>\n📚 {len(vehicles)} trucks"
    return pdf_buf, caption, "Fuel_DEF_Report"


async def _fetch_health(
    account_id: int, company: Optional[str], days: int,  # noqa: ARG001
) -> tuple[io.BytesIO, str, str]:
    faulted, total, _breakdown = await _svc_vehicles_with_faults(account_id)
    all_fleet = await _svc_fleet_overview(account_id)
    if company:
        all_fleet = [v for v in all_fleet if (v.get("_org") or "").upper() == company.upper()]
        faulted = [v for v in faulted if (v.get("_org") or "").upper() == company.upper()]
        total = len(all_fleet)
    pdf_buf = await asyncio.to_thread(
        generate_vehicle_health_pdf, all_fleet, company,
    )
    caption = f"🏥 <b>Vehicle Health Report</b>\n📚 {total} trucks"
    return pdf_buf, caption, "Vehicle_Health_Report"


async def _fetch_efficiency(
    account_id: int, company: Optional[str], days: int,
) -> tuple[io.BytesIO, str, str]:
    vehicles = await _svc_fleet_overview(account_id)
    if company:
        vehicles = [v for v in vehicles if (v.get("_org") or "").upper() == company.upper()]
    pdf_buf = await asyncio.to_thread(
        generate_fleet_efficiency_pdf, vehicles, days, company,
    )
    caption = f"📊 <b>Efficiency Report</b>\n📚 {len(vehicles)} trucks · last {days}d"
    return pdf_buf, caption, "Efficiency_Report"


async def _fetch_camera(
    account_id: int, company: Optional[str], days: int,  # noqa: ARG001 — company/days unused
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


async def build_report_pdf(
    account_id: int,
    report_type: str,
    *,
    company: Optional[str] = None,
    days: int = 7,
    prepare: bool = True,
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

    return await handler(account_id, company, days)
