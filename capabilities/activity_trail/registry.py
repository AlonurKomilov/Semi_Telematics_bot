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
    # ── Restore-from-trail (FAIL-CLOSED: all three unset = the entity
    # cannot be restored, no button, no endpoint path) ──
    # ANY-of flags gating the restore WRITE (manage-grade, not view).
    restore_permissions: tuple[str, ...] = ()
    # The table restore_trail_row inserts into (allowlisted in the
    # storage mixin — the declaration names it, the mixin owns the SQL).
    restore_table: str = ""
    # "insert" (hard-deleted rows come back) | "reactivate" (soft-
    # deleted rows flip active again).
    restore_mode: str = "insert"
    # True when the feature's own by-id routes enforce the per-user
    # company assignment (maintenance's _require_company_visible_task,
    # work orders' _require_visible_work_order).  Restore must honor the
    # SAME boundary or it becomes the one write path around it — the
    # permission flag alone is not the whole gate for these entities.
    company_scoped: bool = False


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


async def viewer_entity_flags(user: dict) -> dict[str, bool]:
    """Which entity types THIS viewer may see AT ALL — one answer, one
    place, for every reader of the trail.

    This is the FEATURE axis only — the owning feature's
    ``view_permissions``, any-of, the same predicate the per-record
    endpoint uses to 403.  (The product models access on two axes: the
    Permissions matrix grants WHICH FEATURES a role holds; Team
    Management sets WHOSE DATA a user sees.)

    The COMPANY axis is deliberately NOT answered here.  This map is
    keyed by entity TYPE, while company scope is a property of each
    ROW — so it belongs where the rows are, in
    ``facade.filter_company_scoped``.  An earlier edition clamped
    ``company_scoped`` types off wholesale at this level, because a
    per-row owning-table lookup read as N+1; with only two owning
    tables it is two queries per page, so the clamp gave way to the
    real wall and a restricted viewer now sees their own companies'
    rows instead of none.

    A type absent from the map reads as False at every call site, so an
    UNREGISTERED entity_type — including the empty string the AI write
    path can emit — is invisible rather than exposed.  That is the
    whole reason callers must look flags up with ``.get(et, False)``.
    """
    from capabilities.permissions.roles import Role, get_user_permissions
    ensure_declarations_loaded()
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    return {
        et: any(getattr(perms, p, False) for p in d.view_permissions)
        for et, d in _ENTITIES.items()
    }


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
    "features.kpi.activity",            # kpi_run (incentive runs = pay)
    "features.applications.activity",   # hiring decisions (FMCSA audit trail)
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
