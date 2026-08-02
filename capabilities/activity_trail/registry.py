"""Entity registry — per-feature declarations, hub-side engine.

The Retention-hub pattern, applied to the trail's READ side: each
feature declares the entity types it writes (label, the permission
that gates viewing their history, any sensitive fields) in its own
one-file ``activity.py``; this module only stores and serves the
declarations.  The capability never hardcodes feature facts like
"vendor → can_work_orders_all" — that knowledge lives with the
feature, exactly like keep-windows live in ``features/x/retention.py``.

The generic per-record endpoint (``GET /activity/{type}/{id}``) is
driven entirely by this registry: unknown entity types 404, and the
view gate is the OWNING feature's permission — so the trail can never
become a side door around a feature gate.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityDescriptor:
    """One entity type's declaration (owned by its feature)."""

    entity_type: str            # wire value in activity_events.entity_type
    label: str                  # human label ("Work order")
    feature: str                # owning feature, for docs/debugging
    view_permissions: tuple[str, ...]   # ANY-of FeatureSet flags
    sensitive_fields: frozenset[str] = field(default_factory=frozenset)


_ENTITIES: dict[str, EntityDescriptor] = {}


def register_entity(d: EntityDescriptor) -> None:
    _ENTITIES[d.entity_type] = d


def entity_descriptor(entity_type: str) -> EntityDescriptor | None:
    return _ENTITIES.get(entity_type)


def registered_entity_types() -> frozenset[str]:
    return frozenset(_ENTITIES)


def sensitive_for(entity_type: str) -> frozenset[str]:
    d = _ENTITIES.get(entity_type)
    return d.sensitive_fields if d else frozenset()


# One-file declaration per contributing feature — mirror of the
# Retention hub's module list.  Adding a feature's history = adding
# its activity.py here (a guard test keeps this list honest against
# the frontend's entity vocabulary).
DECLARATION_MODULES: tuple[str, ...] = (
    "features.maintenance.activity",
    "features.vehicles.activity",       # vehicle + inventory_item
    "features.parts.activity",
    "features.vendors.activity",        # vendor + directory + sharing
    "features.work_orders.activity",
    "features.service_tasks.activity",
    "features.loads.activity",
    "features.drivers.activity",        # driver PII masking lives here
    "features.settings.activity",       # user/invite/role/company/schedule/account
    "features.coaching.activity",
    "features.driver_pay.activity",
    "capabilities.integrations.activity",
    "capabilities.notifications.activity",   # alert_type + alert_topic
)

_loaded = False


def ensure_declarations_loaded() -> None:
    """Import every declaration module once (idempotent)."""
    global _loaded
    if _loaded:
        return
    for mod in DECLARATION_MODULES:
        try:
            importlib.import_module(mod)
        except Exception:
            # A broken declaration must not take the API down — the
            # entity type just stays unregistered (its history 404s)
            # and the log says why.
            logger.exception("activity declaration failed to load: %s", mod)
    _loaded = True
