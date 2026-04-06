"""Reports package — PDF and CSV report generators."""

from .fault_pdf import generate_fault_report_pdf, generate_critical_report_pdf
from .truck_pdf import generate_truck_detail_pdf
from .efficiency_pdf import generate_fleet_efficiency_pdf
from .fuel_pdf import generate_fuel_report_pdf
from .health_pdf import generate_vehicle_health_pdf
from .weather_pdf import generate_weather_pdf
from .camera_pdf import generate_camera_check_pdf
from .shift_pdf import generate_shift_report_pdf
from .pdf_base import compute_stats

from .csv_generators import (
    generate_efficiency_csv,
    generate_fuel_csv,
    generate_health_csv,
    generate_fault_csv,
    generate_events_csv,
    generate_camera_check_csv,
)

__all__ = [
    "generate_fault_report_pdf",
    "generate_critical_report_pdf",
    "generate_truck_detail_pdf",
    "generate_fleet_efficiency_pdf",
    "generate_fuel_report_pdf",
    "generate_vehicle_health_pdf",
    "generate_weather_pdf",
    "generate_camera_check_pdf",
    "generate_shift_report_pdf",
    "compute_stats",
    "generate_efficiency_csv",
    "generate_fuel_csv",
    "generate_health_csv",
    "generate_fault_csv",
    "generate_events_csv",
    "generate_camera_check_csv",
]
