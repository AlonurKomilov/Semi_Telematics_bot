"""Datatruck-shaped storage tables — TMS data, kept separate from
the existing Samsara/telemetry tables.

Each table is owned by exactly one resource file (drivers, trucks,
trailers, orders, work_orders) so dropping Datatruck later means
dropping one subpackage + five ``datatruck_*`` tables — no schema
entanglement with Samsara.

All five tables share the ELT shape (see ``_base.py``): promoted
columns for what screens filter/join on, plus a raw ``payload`` JSON
column so un-promoted upstream fields survive locally and never need
a re-fetch through Datatruck's 20 req/min budget.

``DatatruckStorageMixin`` composes the five resource mixins into the
single class the ``Database`` registers — per the house rule of one
mixin entry per domain on ``Database``.
"""

from __future__ import annotations

from .drivers import DatatruckDriver, DatatruckDriversMixin
from .orders import DatatruckOrder, DatatruckOrdersMixin
from .trailers import DatatruckTrailer, DatatruckTrailersMixin
from .trucks import DatatruckTruck, DatatruckTrucksMixin
from .work_orders import DatatruckWorkOrder, DatatruckWorkOrdersMixin


class DatatruckStorageMixin(
    DatatruckDriversMixin,
    DatatruckTrucksMixin,
    DatatruckTrailersMixin,
    DatatruckOrdersMixin,
    DatatruckWorkOrdersMixin,
):
    """All five Datatruck resource mixins, composed for ``Database``."""


__all__ = [
    "DatatruckDriver",
    "DatatruckOrder",
    "DatatruckStorageMixin",
    "DatatruckTrailer",
    "DatatruckTruck",
    "DatatruckWorkOrder",
]
