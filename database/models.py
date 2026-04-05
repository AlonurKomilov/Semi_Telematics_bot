"""Data models and enums used across the database layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ─── Schema version ──────────────────────────────────────────────
SCHEMA_VERSION = 1


# ─── Roles ────────────────────────────────────────────────────────

class Role(str, Enum):
    """User roles — ordered from most to least privileged."""
    OWNER       = "owner"
    ADMIN       = "admin"
    FLEET_MGR   = "fleet_manager"
    DISPATCHER  = "dispatcher"
    DRIVER      = "driver"

    @classmethod
    def from_str(cls, s: str) -> "Role":
        s = s.strip().lower()
        for r in cls:
            if r.value == s:
                return r
        raise ValueError(f"Unknown role: {s}")


# ─── Data classes ─────────────────────────────────────────────────

@dataclass
class Account:
    id: int
    name: str
    slug: str
    tier: str
    is_active: bool
    created_at: str

@dataclass
class Company:
    id: int
    account_id: int
    code: str
    display_name: str
    samsara_api_key: str
    active_days: int
    is_active: bool
    created_at: str

@dataclass
class User:
    id: int
    telegram_id: int
    account_id: int
    role: Role
    department: str
    truck_num: Optional[str]   # for driver role
    alerts_on: bool
    is_active: bool
    created_at: str
    # Per-type alert preferences (all default ON when alerts_on is True)
    display_name: str = ""
    alert_faults: bool = True
    alert_health: bool = True
    alert_fuel: bool = True
    alert_geofence: bool = True
    ai_fault: bool = False      # Proactive AI on fault alerts
    ai_health: bool = False     # Proactive AI on health alerts
    ai_fuel: bool = False       # Proactive AI on fuel alerts
    alert_events: bool = True   # Safety event alerts
    ai_events: bool = False     # Proactive AI on event alerts
    alert_camera: bool = True   # Camera alerts
    alert_parking: bool = True  # Unsafe parking alerts
    ai_parking: bool = False    # Proactive AI on parking alerts
    quiet_start: Optional[int] = None   # DND start hour (0-23)
    quiet_end: Optional[int] = None     # DND end hour (0-23)
    timezone: str = "America/New_York"
    language: str = "en"                # UI language (en/es/ru/uk/fr)

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def is_admin_or_above(self) -> bool:
        return self.role in (Role.OWNER, Role.ADMIN)

    def wants_alert(self, alert_type: str) -> bool:
        """Check if user wants a specific alert type.

        alert_type: 'faults', 'health', 'fuel', 'geofence', or 'events'
        """
        if not self.alerts_on:
            return False
        return getattr(self, f"alert_{alert_type}", True)

    def is_in_quiet_hours(self) -> bool:
        """Check if the user is currently in their DND quiet hours.

        quiet_start/quiet_end define the WORKING hours window (when
        alerts should be delivered).  This method returns True when
        the current local time is OUTSIDE that window, meaning the
        user is in quiet / do-not-disturb mode.

        Returns False if quiet hours are not configured.
        """
        if self.quiet_start is None or self.quiet_end is None:
            return False
        try:
            from zoneinfo import ZoneInfo
            user_tz = ZoneInfo(self.timezone)
            local_hour = datetime.now(timezone.utc).astimezone(user_tz).hour
            start, end = self.quiet_start, self.quiet_end
            # Determine if the user is INSIDE working hours
            if start <= end:
                in_working = start <= local_hour < end
            else:
                # Wraps midnight, e.g., 22:00 - 06:00
                in_working = local_hour >= start or local_hour < end
            # Quiet hours = NOT in working hours
            return not in_working
        except Exception:
            return False

    @property
    def label(self) -> str:
        """Human-readable name for UI display, falls back to telegram_id."""
        return self.display_name or str(self.telegram_id)

    @property
    def linked_label(self) -> str:
        """Clickable name linking to the user's Telegram profile."""
        name = self.display_name or str(self.telegram_id)
        return f"<a href='tg://user?id={self.telegram_id}'>{name}</a>"

@dataclass
class AuthorizedChat:
    id: int
    account_id: int
    chat_id: int             # Telegram group/channel ID (negative)
    chat_title: str
    added_by: int            # user.id who authorized
    is_active: bool
    created_at: str

@dataclass
class Invite:
    id: int
    code: str
    account_id: int
    role: str
    department: str
    truck_num: Optional[str]
    created_by: int          # user.id
    expires_at: str
    used_by: Optional[int]   # user.id who redeemed
    created_at: str

    @property
    def is_expired(self) -> bool:
        exp = datetime.fromisoformat(self.expires_at)
        return datetime.now(timezone.utc) > exp

    @property
    def is_used(self) -> bool:
        return self.used_by is not None
