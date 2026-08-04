"""
Database layer — async Postgres (asyncpg) with repository-pattern abstractions.

  ⚠ "Storage" here means the DATABASE.  Files (attachments, invoices,
  driver documents) live in ``capabilities/object_storage/`` and its
  adapter ``adapters/storage/object_storage.py``.

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
from .bot_instances import BotInstancesMixin
from .alert_topics import AlertTopicsMixin
from .maintenance import MaintenanceMixin
from .work_orders import WorkOrdersMixin
from .vendors import VendorsMixin
from .parts_catalog import PartsCatalogMixin
from .service_tasks import ServiceTasksMixin
from .service_task_library import ServiceTaskLibraryMixin
from .service_assemblies import ServiceAssembliesMixin
from .part_directory import PartDirectoryMixin
from .platform_settings import PlatformSettingsMixin
from .vendor_directory import VendorDirectoryMixin
from .market_intel import MarketIntelMixin
from .vehicle_inventory import VehicleInventoryMixin
from .fuel import FuelMixin
from .alerts import AlertsMixin
from .page_layouts import PageLayoutsMixin
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
from .user_preferences import UserPreferencesMixin

from .billing import BillingMixin
from .geofence import GeofenceMixin
from .custom_poi import CustomPoiMixin
from .scorecard import ScorecardMixin
from .warehouse import WarehouseMixin
from .driver_pay import DriverPayMixin
from .coaching import CoachingMixin
from .object_storage_sync import ObjectStorageSyncMixin
from .ai_chat import AIChatHistoryMixin
from .ai_actions import AIActionProposalsMixin
from .notification_prefs import NotificationPrefsMixin
from .notification_inbox import NotificationInboxMixin
from .notification_deliveries import NotificationDeliveriesMixin
from .push_subscriptions import PushSubscriptionsMixin
from .errors import ErrorLogMixin
from .scan_log import ScanLogMixin
from .applications import ApplicationsMixin
from .application_drafts import ApplicationDraftsMixin
from .application_verifications import ApplicationVerificationsMixin
from .carrier_directory import CarrierDirectoryMixin
from .account_integrations import AccountIntegrationsMixin, AccountIntegration  # noqa: F401
from .datatruck import DatatruckStorageMixin
from .system_metrics import SystemMetricsMixin
from .vehicles_registry import VehiclesRegistryMixin, Vehicle  # noqa: F401
from .vehicle_departure import VehicleDepartureMixin
from .loads import LoadsMixin, Load  # noqa: F401
from .activity_trail import ActivityTrailMixin
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
    BotInstancesMixin,
    AlertTopicsMixin,
    MaintenanceMixin,
    WorkOrdersMixin,
    VendorsMixin,
    PartsCatalogMixin,
    PartDirectoryMixin,
    ServiceTasksMixin,
    ServiceTaskLibraryMixin,
    ServiceAssembliesMixin,
    PlatformSettingsMixin,
    VendorDirectoryMixin,
    MarketIntelMixin,
    VehicleInventoryMixin,
    FuelMixin,
    AlertsMixin,
    PageLayoutsMixin,
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
    UserPreferencesMixin,
    BillingMixin,
    GeofenceMixin,
    CustomPoiMixin,
    ScorecardMixin,
    WarehouseMixin,
    DriverPayMixin,
    CoachingMixin,
    ObjectStorageSyncMixin,
    AIChatHistoryMixin,
    AIActionProposalsMixin,
    NotificationPrefsMixin,
    NotificationInboxMixin,
    NotificationDeliveriesMixin,
    PushSubscriptionsMixin,
    ErrorLogMixin,
    ScanLogMixin,
    ApplicationsMixin,
    ApplicationDraftsMixin,
    ApplicationVerificationsMixin,
    CarrierDirectoryMixin,
    DatatruckStorageMixin,
    SystemMetricsMixin,
    VehiclesRegistryMixin,
    VehicleDepartureMixin,
    LoadsMixin,
    ActivityTrailMixin,
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
