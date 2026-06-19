"""
Database layer — async Postgres (asyncpg) with repository-pattern abstractions.

Design
  • All SQL lives in this package — callers never see asyncpg directly.
  • Mixins write portable SQLite-style SQL (``?`` placeholders,
    ``AUTOINCREMENT``, ``INSERT OR IGNORE``, ``datetime('now')``);
    ``adapters/storage/pg_adapter.py::_sqlite_to_pg_sql`` rewrites them
    to native Postgres at the asyncpg boundary.  Kept as the project's
    portable dialect so future engine swaps stay cheap, and so existing
    mixins didn't need a 60-file rewrite at the SQLite→Postgres cutover.
  • Every public function returns plain dicts / dataclasses — no ORM
    leakage into bot.py or samsara_client.py.
  • Schema is versioned via the ``_schema_versions`` table; migrations
    register with ``@_register("NNN_name")`` in ``migrations.py``.
  • All writes go through explicit helper functions (transactions are
    explicit via ``async with tenant.transaction()``).

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
from .account_persona_groups import AccountPersonaGroupsMixin
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
    PTITemplateMixin,
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
from .storage_sync import StorageSyncMixin
from .ai_chat import AIChatHistoryMixin
from .errors import ErrorLogMixin
from .recruitment import RecruitmentMixin
from .account_integrations import AccountIntegrationsMixin, AccountIntegration  # noqa: F401
from .datatruck import DatatruckStorageMixin
from .vehicles_registry import VehiclesRegistryMixin, Vehicle  # noqa: F401
from .platform import PlatformDB


class Database(
    AccountsMixin,
    CompaniesMixin,
    AccountIntegrationsMixin,
    UsersMixin,
    InvitesMixin,
    ChatsMixin,
    ForumRoutingMixin,
    AccountPersonaGroupsMixin,
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
    PTITemplateMixin,
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
    StorageSyncMixin,
    AIChatHistoryMixin,
    ErrorLogMixin,
    RecruitmentMixin,
    DatatruckStorageMixin,
    VehiclesRegistryMixin,
    _DatabaseCore,
):
    """Async Postgres wrapper with typed helpers.

    Usage:
        db = Database()              # reads DATABASE_URL from env
        await db.initialize()        # runs pending migrations
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
