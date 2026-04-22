"""Fleet commands — re-export facade.

Actual implementations live in:
  bot.reports  — fault, fuel, efficiency, health, weather, API status
  bot.alerts   — alert settings, toggle, disable, history, pending
  bot.vehicles — truck lookup, truck detail, truck report, critical
  bot.cameras  — camera checks, history, PDF/CSV exports
"""

# Re-export reports
from interfaces.bot.reports import (  # noqa: F401
    cmd_faults, cmd_faults_pdf, cmd_faults_csv,
    cmd_fuel, cmd_fuel_pdf, cmd_fuel_csv,
    cmd_efficiency, cmd_efficiency_pdf, cmd_efficiency_csv,
    cmd_health, cmd_health_pdf, cmd_health_csv,
    cmd_weather, cmd_api_status,
)

# Re-export alerts
from interfaces.bot.alerts import (  # noqa: F401
    cmd_alerts, cmd_alert_toggle, cmd_ai_alert_toggle, cmd_alert_disable_all,
    cmd_alert_history, cmd_pending_alerts,
)

# Re-export trucks
from interfaces.bot.vehicles import (  # noqa: F401
    cmd_truck, cmd_truck_report, cmd_critical,
)

# Re-export cameras
from interfaces.bot.cameras import (  # noqa: F401
    cmd_camera_check, cmd_camera_check_truck,
    cmd_camera_history, cmd_camera_check_pdf, cmd_camera_check_csv,
    cmd_cam_tool, cmd_cam_company_pick, cmd_cam_page,
)
