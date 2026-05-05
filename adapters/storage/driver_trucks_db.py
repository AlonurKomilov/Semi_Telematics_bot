"""Backward-compat shim — import from driver_vehicles_db instead."""

from .driver_vehicles_db import (  # noqa: F401
    DriverVehicle as DriverTruck,
    DriverVehiclesMixin as DriverTrucksMixin,
)

__all__ = ["DriverTruck", "DriverTrucksMixin"]
