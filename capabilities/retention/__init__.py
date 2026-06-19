"""Retention hub — cross-cutting data-lifecycle (keep N days, then delete).

``discover()`` imports the target declarations + every feature's retention
module so their ``register_*`` calls fire — the same "collect feature
contributions" pattern the Alerting hub uses.  Lazy + idempotent so it
can't trigger import cycles at module load.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Feature modules that contribute retention NEEDS.  Add a line when a new
# feature declares one; missing modules are skipped (logged), never fatal.
_NEED_MODULES = (
    "features.vehicles.retention",
    "features.work_orders.retention",
    "features.maintenance.retention",
)

_discovered = False


def discover() -> None:
    global _discovered
    if _discovered:
        return
    import importlib

    # Platform target declarations (also carries the scorecards/email needs).
    from . import targets  # noqa: F401

    for mod in _NEED_MODULES:
        try:
            importlib.import_module(mod)
        except Exception:
            logger.exception("retention: failed to load need module %s", mod)

    _discovered = True
