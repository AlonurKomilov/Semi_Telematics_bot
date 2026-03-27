"""Fleet commands — re-export facade.

Actual implementations live in:
  bot.fleet_reports  — fault, fuel, efficiency, health, weather, API status
  bot.fleet_alerts   — alert settings, toggle, disable, history, pending
  bot.fleet_trucks   — truck lookup, truck detail, truck report, critical
"""

# Re-export reports
from bot.fleet_reports import (  # noqa: F401
    cmd_faults, cmd_faults_pdf, cmd_faults_csv,
    cmd_fuel, cmd_fuel_pdf, cmd_fuel_csv,
    cmd_efficiency, cmd_efficiency_pdf, cmd_efficiency_csv,
    cmd_health, cmd_health_pdf, cmd_health_csv,
    cmd_weather, cmd_api_status,
)

# Re-export alerts
from bot.fleet_alerts import (  # noqa: F401
    cmd_alerts, cmd_alert_toggle, cmd_ai_alert_toggle, cmd_alert_disable_all,
    cmd_alert_history, cmd_pending_alerts,
)

# Re-export trucks
from bot.fleet_trucks import (  # noqa: F401
    cmd_truck, cmd_truck_report, cmd_critical,
)

# Re-export cameras
from bot.cameras import (  # noqa: F401
    cmd_camera_check, cmd_camera_check_truck,
    cmd_camera_history, cmd_camera_check_pdf, cmd_camera_check_csv,
    cmd_camera_report,
    cmd_cam_tool, _show_cam_truck_list,
)
