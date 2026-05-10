"""
Telegram message formatters — clean, rich, easy-to-read fleet reports.
Uses HTML parse mode.  Multi-company aware.  Role-aware help menus.

Split into domain submodules; this facade re-exports every public name
via ``from capabilities.formatting import X``.
"""

# ── vehicles ────────────────────────────────────────────────────────
from capabilities.formatting.vehicle_formatting import format_vehicle_detail, format_vehicle_picker

# ── faults ────────────────────────────────────────────────────────
from capabilities.formatting.faults import (
    format_fault_alert,
    format_new_fault_alert,
    format_critical_fault_alert,
)

# ── health ────────────────────────────────────────────────────────
from capabilities.formatting.health import format_health_alert, format_vehicle_health

# ── fuel ──────────────────────────────────────────────────────────
from capabilities.formatting.fuel import format_low_fuel, format_low_fuel_alert

# ── efficiency ────────────────────────────────────────────────────
from capabilities.formatting.efficiency import format_fleet_efficiency

# ── weather ───────────────────────────────────────────────────────
from capabilities.formatting.weather import format_fleet_weather

# ── events ────────────────────────────────────────────────────────
from capabilities.formatting.events import (
    format_event_alert,
    format_events_dashboard,
    format_alert_history_footer,
    _EVENT_EMOJI,
    _EVENT_TYPE_KEYS,
)

# ── registration / onboarding ────────────────────────────────────
from capabilities.formatting.registration import (
    format_help,
    format_welcome_unregistered,
    format_unregistered_member,
    format_register_success,
    format_join_success,
    format_invite_created,
    format_org_added,
)

# ── account / team ────────────────────────────────────────────────
from capabilities.formatting.account import format_account_info, format_users_list

# ── admin ─────────────────────────────────────────────────────────
from capabilities.formatting.admin import (
    format_system_owner_welcome,
    format_admin_dashboard,
    format_admin_account_detail,
    format_admin_accounts_list,
)

__all__ = [
    # vehicles
    "format_vehicle_detail",
    "format_vehicle_picker",
    # faults
    "format_new_fault_alert",
    "format_critical_fault_alert",
    # health
    "format_health_alert",
    "format_vehicle_health",
    # fuel
    "format_low_fuel",
    "format_low_fuel_alert",
    # efficiency
    "format_fleet_efficiency",
    # weather
    "format_fleet_weather",
    # events
    "format_event_alert",
    "format_events_dashboard",
    "format_alert_history_footer",
    "_EVENT_EMOJI",
    "_EVENT_TYPE_KEYS",
    # registration
    "format_help",
    "format_welcome_unregistered",
    "format_unregistered_member",
    "format_register_success",
    "format_join_success",
    "format_invite_created",
    "format_org_added",
    # account
    "format_account_info",
    "format_users_list",
    # admin
    "format_system_owner_welcome",
    "format_admin_dashboard",
    "format_admin_account_detail",
    "format_admin_accounts_list",
]
