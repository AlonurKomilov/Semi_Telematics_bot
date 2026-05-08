"""Reports package — PDF and CSV report generators."""

from .fault_pdf import generate_fault_report_pdf, generate_critical_report_pdf
from .vehicle_pdf import generate_vehicle_detail_pdf
from .efficiency_pdf import generate_fleet_efficiency_pdf
from .fuel_pdf import generate_fuel_report_pdf
from .health_pdf import generate_vehicle_health_pdf
from .weather_pdf import generate_weather_pdf
from .camera_pdf import generate_camera_check_pdf
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
    "generate_fault_report_pdf",
    "generate_critical_report_pdf",
    "generate_vehicle_detail_pdf",
    "generate_fleet_efficiency_pdf",
    "generate_fuel_report_pdf",
    "generate_vehicle_health_pdf",
    "generate_weather_pdf",
    "generate_camera_check_pdf",
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
