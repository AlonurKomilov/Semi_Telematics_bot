"""Retention hub — keep each data tier N days, then delete.

Part of the data-lifecycle family (``capabilities/data_lifecycle``); the DELETE
half (rollups is the BUILD half).  Each feature/capability owns both its
targets (HOW to prune) and its needs (HOW LONG) in its own module; this hub
holds only the registry + engine.  ``discover()`` imports the contributors so
their ``register_*`` calls fire (built from the family's shared
``make_discover`` — lazy + idempotent, so no import cycles at module load).
"""

from __future__ import annotations

from .._common import make_discover

# Modules that contribute retention targets and/or needs.  Each owns the
# data it governs.  Add a line when a new feature declares one; missing
# modules are skipped (logged), never fatal.
_CONTRIBUTORS = (
    "features.vehicles.lifecycle",        # owns vehicle telemetry tiers + faults (build + keep, colocated)
    "features.maintenance.retention",     # consumer need on vehicle.metrics_daily
    "features.work_orders.retention",     # consumer need on vehicle.metrics_daily
    "features.drivers.retention",         # owns driver.efficiency_daily
    "features.events.retention",          # owns safety_events
    "features.applications.retention",    # owns applications.drafts (save & resume)
    "features.loads.retention",           # owns loads.records (load/shipment history)
    "capabilities.scorecards.retention",  # owns scorecards.score_history
    "capabilities.email.retention",       # owns email.delivery_events
    "capabilities.ai.retention",          # owns ai.chat_history (age-cap)
    "infra.scan_retention",               # owns platform.scan_log (AV scan audit)
)

discover = make_discover(_CONTRIBUTORS)
