"""
Database layer — async SQLite with repository-pattern abstractions.

Future-proof design:
  • All SQL lives in this package — swap SQLite → PostgreSQL by
    replacing the engine, not the callers.
  • Every public function uses plain dicts / dataclasses — no ORM
    leakage into bot.py or samsara_client.py.
  • Schema is versioned via `schema_version` pragma.
  • All writes go through explicit helper functions (easy to wrap
    in a transaction / connection-pool later with asyncpg).

Tables
------
accounts       — one per subscribing company
companies      — Samsara company API keys owned by an account
users          — Telegram users linked to an account + role
invites        — one-time join codes (expire 24 h)
"""

from .models import (
    SCHEMA_VERSION,
    Role,
    Account,
    Company,
    User,
    AuthorizedChat,
    Invite,
)

from .core import _DatabaseCore
from .accounts import AccountsMixin
from .companies import CompaniesMixin
from .users import UsersMixin
from .invites import InvitesMixin
from .chats import ChatsMixin
from .maintenance import MaintenanceMixin
from .fuel import FuelMixin
from .alerts import AlertsMixin
from .settings import SettingsMixin
from .parking import ParkingMixin
from .camera import CameraMixin
from .schedules import SchedulesMixin
from .knowledge import KnowledgeBaseMixin
from .permissions import PermissionsMixin
from .driver_vehicles import DriverVehiclesMixin
from .user_companies import UserCompaniesMixin

from .billing import BillingMixin
from .geofence import GeofenceMixin
from .custom_poi import CustomPoiMixin
from .scorecard import ScorecardMixin
from .warehouse import WarehouseMixin
from .payroll import PayrollMixin
from .coaching import CoachingMixin
from .platform import PlatformDB
from .tenant import TenantDB
from .tenant_router import TenantRouter, LegacyRouter


class Database(
    AccountsMixin,
    CompaniesMixin,
    UsersMixin,
    InvitesMixin,
    ChatsMixin,
    MaintenanceMixin,
    FuelMixin,
    AlertsMixin,
    SettingsMixin,
    ParkingMixin,
    CameraMixin,
    SchedulesMixin,
    KnowledgeBaseMixin,
    PermissionsMixin,
    DriverVehiclesMixin,
    UserCompaniesMixin,
    BillingMixin,
    GeofenceMixin,
    CustomPoiMixin,
    ScorecardMixin,
    # WarehouseMixin owns vehicle_state / safety_event_log / etc — needed
    # for ingest_vehicle_state to upsert odometer + telemetry rows in the
    # legacy single-DB layout.  Without this the LegacyRouter path crashes
    # with "'Database' has no attribute 'upsert_vehicle_state'".
    WarehouseMixin,
    PayrollMixin,
    CoachingMixin,
    _DatabaseCore,
):
    """Async SQLite wrapper with typed helpers.

    Usage:
        db = Database("data/bot.db")
        await db.initialize()
        ...
        await db.close()
    """
    pass


__all__ = [
    "SCHEMA_VERSION",
    "Database",
    "Role",
    "Account",
    "Company",
    "User",
    "AuthorizedChat",
    "Invite",
    "PlatformDB",
    "TenantDB",
    "TenantRouter",
    "LegacyRouter",
]
