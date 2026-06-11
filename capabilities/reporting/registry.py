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

from features.vehicles.cameras.report import generate_camera_check_pdf
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

    ``data_method`` is a tenant_db method name resolved at call time
    (string indirection keeps this module free of warehouse imports
    so it can be imported anywhere without cycles).  ``None`` means
    the report doesn't have a generic data path (e.g. Camera Check
    pulls media-server snapshots, not warehouse rows).
    """

    key: str
    emoji: str
    label_short: str          # for dashboard tabs ("Fuel")
    label_full: str           # for menus where short would be ambiguous ("Fuel & DEF")
    permission: str           # gate flag for the API export endpoint
    pdf_generator: Callable
    csv_generator: Callable
    data_method: Optional[str]

    @property
    def label_with_emoji(self) -> str:
        """Convenience for bot menus / Telegram messages."""
        return f"{self.emoji} {self.label_full}"


REPORTS: tuple[ReportSpec, ...] = (
    ReportSpec(
        key="faults", emoji="🔧",
        label_short="Faults", label_full="Faults",
        permission="can_faults",
        pdf_generator=generate_fault_report_pdf,
        csv_generator=generate_fault_csv,
        data_method="get_fault_codes",
    ),
    ReportSpec(
        key="fuel", emoji="⛽",
        label_short="Fuel & DEF", label_full="Fuel & DEF",
        permission="can_fuel",
        pdf_generator=generate_fuel_report_pdf,
        csv_generator=generate_fuel_csv,
        data_method="get_fuel_levels",
    ),
    ReportSpec(
        key="health", emoji="🏥",
        label_short="Health", label_full="Vehicle Health",
        permission="can_health",
        pdf_generator=generate_vehicle_health_pdf,
        csv_generator=generate_health_csv,
        data_method="get_vehicle_health",
    ),
    ReportSpec(
        key="efficiency", emoji="📊",
        label_short="Efficiency", label_full="Efficiency",
        permission="can_efficiency",
        pdf_generator=generate_fleet_efficiency_pdf,
        csv_generator=generate_efficiency_csv,
        data_method="get_fleet_efficiency",
    ),
    ReportSpec(
        key="camera", emoji="📷",
        label_short="Cameras", label_full="Camera Check",
        permission="can_digest",
        pdf_generator=generate_camera_check_pdf,
        csv_generator=generate_camera_check_csv,
        data_method=None,
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
    """Report keys that have a data_method (i.e. can be served by the
    generic ``/api/reports/export`` endpoint).  Camera Check is the
    one current exception."""
    return [r.key for r in REPORTS if r.data_method is not None]
