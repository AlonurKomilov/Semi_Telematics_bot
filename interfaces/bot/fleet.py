"""Fleet commands — re-export facade.

Thin facade kept for backwards compatibility with existing import
paths.  Actual implementations live in:

  bot.reports  — fault / fuel / efficiency / health / weather are
                 now dashboard redirects (see ``reports.py``).
                 ``cmd_api_status`` is the only bot-side command
                 still doing real work.
  bot.alerts   — alert settings, toggle, disable, history, pending.
  bot.vehicles — truck lookup, truck detail, truck report, critical.
  bot.cameras  — single-truck AI dashcam check + dashboard redirects
                 for the account-wide / history / export flows.

The PDF/CSV report commands (``cmd_*_pdf``, ``cmd_*_csv``) and the
camera browser commands (``cmd_cam_tool``, ``cmd_cam_company_pick``,
``cmd_cam_page``, ``cmd_camera_check_pdf`` /
``cmd_camera_check_csv``) were retired — they no longer exist on the
source modules, so we don't re-export them here either.
"""

# Re-export reports (post-Tier-3: redirects + api_status only)
from interfaces.bot.reports import (  # noqa: F401
    cmd_faults, cmd_fuel, cmd_efficiency, cmd_health,
    cmd_weather, cmd_api_status,
)

# Re-export alerts
from interfaces.bot.alerts import (  # noqa: F401
    cmd_alerts, cmd_alert_toggle, cmd_ai_alert_toggle, cmd_alert_disable_all,
    cmd_alert_history, cmd_pending_alerts,
)

# Re-export trucks
from interfaces.bot.vehicles import (  # noqa: F401
    cmd_vehicle, cmd_vehicle_report, cmd_critical,
)

# Re-export cameras (post-Tier-3: single-truck check + redirects)
from interfaces.bot.cameras import (  # noqa: F401
    cmd_camera_check, cmd_camera_check_vehicle,
    cmd_cam, cmd_cam_tool, cmd_camera_history,
)
