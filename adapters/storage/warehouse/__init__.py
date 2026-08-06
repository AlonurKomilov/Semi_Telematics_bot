"""Telemetry-warehouse storage family — one mixin file per stream.

The package replaces the single 3,283-line ``warehouse.py`` at the SAME
dotted path: ``from adapters.storage.warehouse import WarehouseMixin``
keeps working, as do the helper/constant imports tests use.  Files
carry no ``warehouse_`` prefix — the package IS the prefix (the same
rule as the Postgres schema).
"""

from adapters.storage.warehouse._util import (  # noqa: F401
    VELOCITY_DRIVE_DAY_MIN_MILES,
    VELOCITY_MIN_COVERAGE_DAYS,
    VELOCITY_MIN_DRIVE_DAYS,
    _dtc_id,
    _now_iso,
    _opt_float,
)
from adapters.storage.warehouse.aggregates import AggregatesWarehouseMixin
from adapters.storage.warehouse.drivers import DriversWarehouseMixin
from adapters.storage.warehouse.geofences import GeofencesWarehouseMixin
from adapters.storage.warehouse.ledgers import WarehouseLedgersMixin
from adapters.storage.warehouse.safety import SafetyWarehouseMixin
from adapters.storage.warehouse.vehicles import VehiclesWarehouseMixin


class WarehouseMixin(
    VehiclesWarehouseMixin,
    SafetyWarehouseMixin,
    DriversWarehouseMixin,
    GeofencesWarehouseMixin,
    AggregatesWarehouseMixin,
    WarehouseLedgersMixin,
):
    """Combined facade — byte-compatible with the pre-split monolith."""
