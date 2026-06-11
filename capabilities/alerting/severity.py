"""Backward-compat shim — canonical home is capabilities/vehicles/severity.py."""

from features.vehicles.severity import (  # noqa: F401
    classify_is_critical,
    classify_fault_severity,
)
