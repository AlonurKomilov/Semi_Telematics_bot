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
    ForumGroup,
    AlertRoute,
    ALERT_TYPE_KEYS,
)

from .core import _DatabaseCore
from .accounts import AccountsMixin
from .companies import CompaniesMixin
from .users import UsersMixin
from .invites import InvitesMixin
from .chats import ChatsMixin
from .forum_routing import ForumRoutingMixin
from .maintenance import MaintenanceMixin
from .work_orders import WorkOrdersMixin
from .fuel import FuelMixin
from .alerts import AlertsMixin
from .settings import SettingsMixin
from .parking import ParkingMixin
from .camera import CameraMixin
from .schedules import SchedulesMixin
from .knowledge import KnowledgeBaseMixin
from .permissions import PermissionsMixin
from .driver_vehicles import DriverVehiclesMixin
from .drivers import (
    DriverProfileMixin,
    DriverVehicleAssignmentsMixin,
    DriverDocumentsMixin,
)
from .driver_future import (
    DriverInspectionsMixin,
    DriverTrainingsMixin,
    DriverHosStatusMixin,
)
from .user_companies import UserCompaniesMixin

from .billing import BillingMixin
from .geofence import GeofenceMixin
from .custom_poi import CustomPoiMixin
from .scorecard import ScorecardMixin
from .warehouse import WarehouseMixin
from .payroll import PayrollMixin
from .coaching import CoachingMixin
from .platform import PlatformDB


class Database(
    AccountsMixin,
    CompaniesMixin,
    UsersMixin,
    InvitesMixin,
    ChatsMixin,
    ForumRoutingMixin,
    MaintenanceMixin,
    WorkOrdersMixin,
    FuelMixin,
    AlertsMixin,
    SettingsMixin,
    ParkingMixin,
    CameraMixin,
    SchedulesMixin,
    KnowledgeBaseMixin,
    PermissionsMixin,
    DriverVehiclesMixin,
    DriverProfileMixin,
    DriverVehicleAssignmentsMixin,
    DriverDocumentsMixin,
    DriverInspectionsMixin,
    DriverTrainingsMixin,
    DriverHosStatusMixin,
    UserCompaniesMixin,
    BillingMixin,
    GeofenceMixin,
    CustomPoiMixin,
    ScorecardMixin,
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
    "ForumGroup",
    "AlertRoute",
    "ALERT_TYPE_KEYS",
    "PlatformDB",
]
