"""Reports package — shared report infra + cross-feature generators.

The single-vehicle detail PDF moved to ``features/vehicles/report.py``
(co-located with its feature, like ``features/cameras/report.py``); import
``generate_vehicle_detail_pdf`` from there.  The shared ``pdf_base`` +
cross-feature reports (shift, risk, DOT binder) stay in this hub.
"""

from .shift_pdf import generate_shift_report_pdf
from .risk_summary_pdf import generate_risk_summary_pdf
from .risk_profile import RiskProfile, build_risk_profile
from .audiences import AUDIENCE_CONFIG, get_audience_config, is_valid_audience
from .pdf_base import compute_stats

from .csv_generators import (
    generate_efficiency_csv,
    generate_fuel_csv,
    generate_health_csv,
    generate_fault_csv,
    generate_events_csv,
    generate_camera_check_csv,
    generate_risk_summary_csv,
)

__all__ = [
    "generate_vehicle_health_pdf",
    "generate_shift_report_pdf",
    "generate_risk_summary_pdf",
    "RiskProfile",
    "build_risk_profile",
    "AUDIENCE_CONFIG",
    "get_audience_config",
    "is_valid_audience",
    "compute_stats",
    "generate_efficiency_csv",
    "generate_fuel_csv",
    "generate_health_csv",
    "generate_fault_csv",
    "generate_events_csv",
    "generate_camera_check_csv",
    "generate_risk_summary_csv",
]
