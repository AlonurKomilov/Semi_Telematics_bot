"""Retention hub — cross-cutting data-lifecycle (keep N days, then delete).

``discover()`` imports every module that contributes retention targets or
needs so their ``register_*`` calls fire — the same "collect feature
contributions" pattern the Alerting hub uses.  Each feature/capability owns
both its targets (HOW to prune) and its needs (HOW LONG) in its own
``retention.py``; this hub holds only the registry + engine.  Lazy +
idempotent so it can't trigger import cycles at module load.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Modules that contribute retention targets and/or needs.  Each owns the
# data it governs.  Add a line when a new feature declares one; missing
# modules are skipped (logged), never fatal.
_CONTRIBUTORS = (
    "features.vehicles.retention",        # owns vehicle.timeline_5min/hourly, vehicle.metrics_daily, vehicle.faults
    "features.maintenance.retention",     # consumer need on vehicle.metrics_daily
    "features.work_orders.retention",     # consumer need on vehicle.metrics_daily
    "features.drivers.retention",         # owns driver.efficiency_daily
    "features.events.retention",          # owns safety_events
    "capabilities.scorecards.retention",  # owns scorecards.score_history
    "capabilities.email.retention",       # owns email.delivery_events
    "infra.scan_retention",               # owns platform.scan_log (AV scan audit)
)

_discovered = False


def discover() -> None:
    global _discovered
    if _discovered:
        return
    import importlib

    for mod in _CONTRIBUTORS:
        try:
            importlib.import_module(mod)
        except Exception:
            logger.exception("retention: failed to load contributor module %s", mod)

    _discovered = True
