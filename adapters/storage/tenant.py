"""Tenant database — per-account isolated tables."""

from __future__ import annotations

import logging
import os

from .core import _DatabaseCore
from .companies import CompaniesMixin
from .maintenance import MaintenanceMixin
from .fuel import FuelMixin
from .alerts import AlertsMixin
from .settings import SettingsMixin
from .parking import ParkingMixin
from .camera import CameraMixin
from .schedules import SchedulesMixin
from .knowledge import KnowledgeBaseMixin
from .geofence import GeofenceMixin
from .custom_poi import CustomPoiMixin
from .scorecard import ScorecardMixin
from .warehouse import WarehouseMixin
from .payroll import PayrollMixin
from .coaching import CoachingMixin
from . import tenant_schema
from . import tenant_migrations

logger = logging.getLogger(__name__)


class TenantDB(
    CompaniesMixin,
    MaintenanceMixin,
    FuelMixin,
    AlertsMixin,
    SettingsMixin,
    ParkingMixin,
    CameraMixin,
    SchedulesMixin,
    KnowledgeBaseMixin,
    GeofenceMixin,
    ScorecardMixin,
    CustomPoiMixin,
    WarehouseMixin,
    PayrollMixin,
    CoachingMixin,
    _DatabaseCore,
):
    """SQLite database for a single tenant's operational data.

    Each account gets its own tenant_N.db file containing companies,
    alerts, maintenance, fuel, parking, cameras, schedules, settings,
    and audit logs.
    """

    def __init__(self, path: str, account_id: int):
        super().__init__(path)
        self.account_id = account_id

    async def initialize(self):
        """Open DB and create tenant schema."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await self._open_connection()
        await tenant_schema.create_tables(self._db)
        await tenant_migrations.run_all(self._db)

        # Spin up read pool
        for _ in range(self._pool_size):
            conn = await self._open_connection()
            self._all_readers.append(conn)
            self._read_pool.put_nowait(conn)

        logger.info(
            "Tenant DB ready at %s (account %d, writer + %d readers)",
            self.path, self.account_id, self._pool_size,
        )
