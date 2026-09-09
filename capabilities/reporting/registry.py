"""Single source of truth for the recurring report catalogue.

These reports share the same delivery pattern: they're generated as a
PDF (or CSV), exposed both via the dashboard "Reports" page tab system
AND the scheduled-delivery (Telegram bot) pipeline AND — except for
Camera Check — via the ``/api/reports/export`` endpoint.

Until this registry existed, the list was reconstructed in five
places (API ``EXPORT_TYPES``, bot ``REPORT_TYPES``, bot keyboard
``type_labels``, dashboard ``ALL_TABS``, dashboard ScheduledReports
``REPORT_TYPES``), with each one drifting independently —
``keyboards.py`` was missing Camera Check entirely as a result.

Reports with bespoke shapes (Risk Summary with its audience variants,
Cost Reports backed by work-order aggregates, DOT Binder) are
intentionally NOT in this registry.  They don't share the delivery
pipeline and folding them in would dilute the abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from features.cameras.report import generate_camera_check_pdf
from features.vehicles.efficiency.report import generate_fleet_efficiency_pdf
from features.vehicles.faults.report import generate_fault_report_pdf
from features.vehicles.fuel.report import generate_fuel_report_pdf
from features.vehicles.health.report import generate_vehicle_health_pdf

from .csv_generators import (
    generate_camera_check_csv,
    generate_efficiency_csv,
    generate_fault_csv,
    generate_fuel_csv,
    generate_health_csv,
)


@dataclass(frozen=True)
class ReportSpec:
    """One recurring report.

    ``key`` is the stable string identifier used in API params, bot
    callback_data, and the ``digest_subscriptions.report_type`` column —
    never rename without a migration.

    ``api_export`` says whether ``/api/reports/export`` can serve this
    report.  It replaced a ``data_method`` STRING that the router used
    to resolve with ``getattr`` — the named methods were renamed
    underneath it and nothing failed until a user asked for the CSV,
    because a string names nothing the interpreter will check.  What
    each report actually reads now lives in
    ``capabilities.reporting.data_fetch``, wired by reference.
    """

    key: str
    emoji: str
    label_short: str          # for dashboard tabs ("Fuel")
    label_full: str           # for menus where short would be ambiguous ("Fuel & DEF")
    permission: str           # gate flag for the API export endpoint
    pdf_generator: Callable
    csv_generator: Callable
    api_export: bool

    @property
    def label_with_emoji(self) -> str:
        """Convenience for bot menus / Telegram messages."""
        return f"{self.emoji} {self.label_full}"


REPORTS: tuple[ReportSpec, ...] = (
    ReportSpec(
        key="faults", emoji="🔧",
        label_short="Faults", label_full="Faults",
        permission="can_view_faults",
        pdf_generator=generate_fault_report_pdf,
        csv_generator=generate_fault_csv,
        api_export=True,
    ),
    ReportSpec(
        key="fuel", emoji="⛽",
        label_short="Fuel & DEF", label_full="Fuel & DEF",
        permission="can_view_fuel",
        pdf_generator=generate_fuel_report_pdf,
        csv_generator=generate_fuel_csv,
        api_export=True,
    ),
    ReportSpec(
        key="health", emoji="🏥",
        label_short="Health", label_full="Vehicle Health",
        permission="can_view_health",
        pdf_generator=generate_vehicle_health_pdf,
        csv_generator=generate_health_csv,
        api_export=True,
    ),
    ReportSpec(
        key="efficiency", emoji="📊",
        label_short="Efficiency", label_full="Efficiency",
        permission="can_view_efficiency",
        pdf_generator=generate_fleet_efficiency_pdf,
        csv_generator=generate_efficiency_csv,
        api_export=True,
    ),
    ReportSpec(
        key="camera", emoji="📷",
        label_short="Cameras", label_full="Camera Check",
        permission="can_view_cameras",
        pdf_generator=generate_camera_check_pdf,
        csv_generator=generate_camera_check_csv,
        api_export=False,
    ),
)

REPORTS_BY_KEY: dict[str, ReportSpec] = {r.key: r for r in REPORTS}


def get_report(key: str) -> Optional[ReportSpec]:
    """Look up a report by stable key.  ``None`` for unknown keys."""
    return REPORTS_BY_KEY.get(key)


def report_keys() -> list[str]:
    """All known report keys in canonical order."""
    return [r.key for r in REPORTS]


def keys_with_api_export() -> list[str]:
    """Report keys the generic ``/api/reports/export`` endpoint serves.

    Camera Check is the one current exception — it pulls media-server
    snapshots, so it has a PDF but no downloadable export path."""
    return [r.key for r in REPORTS if r.api_export]
