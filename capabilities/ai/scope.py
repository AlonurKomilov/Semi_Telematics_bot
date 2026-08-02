"""Resolve a user's effective AI data scope from the Vehicle Access SSOT.

Vehicle Access (Team Management → Access tab) is the single source of truth
for *which vehicles' data* a user may see.  It has three modes, stored in the
same tables the dashboard / API read:

  * **All**      → unrestricted (owners, and anyone left on "All")
  * **Vehicle**  → the exact vehicles picked (``driver_trucks`` junction);
                   a Driver is simply this case scoped to their truck(s)
  * **Company**  → every vehicle in the allowed companies (``user_companies``)

``resolve_vehicle_scope`` collapses those modes into the concrete set of
vehicle *names* the AI may serve the user:

  * returns ``None``  → unrestricted (no isolation)
  * returns ``[...]`` → restricted to exactly these vehicle names
  * returns ``[]``    → restricted to NOTHING (fail-closed)

Because it reads the very same ``get_user_vehicle_nums`` / company helpers the
dashboard and API use, changing a user's Vehicle Access in Team Management
governs the AI immediately — no separate AI setting.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("bot.ai")


async def resolve_vehicle_scope(
    platform_db,
    account_id: int,
    user_id: int,
    role: object,
    truck_num: Optional[str] = None,
) -> Optional[list[str]]:
    """Return the vehicle names a user may access via the AI, or ``None`` if
    unrestricted.  See module docstring for the None / list / [] contract."""
    role_str = role.value if hasattr(role, "value") else (str(role or "").lower())

    # Owners are never scope-restricted.
    if role_str == "owner":
        return None

    # Vehicle mode (and Drivers): an explicit per-vehicle assignment wins.
    try:
        vehicle_nums = list(await platform_db.get_user_vehicle_nums(user_id) or [])
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("AI scope: get_user_vehicle_nums failed user=%s: %s", user_id, e)
        vehicle_nums = []
    if not vehicle_nums and truck_num:
        vehicle_nums = [truck_num]

    if role_str == "driver":
        # Drivers are ALWAYS isolated to their own vehicle(s) — an unassigned
        # driver gets an empty (fail-closed) scope, never "All".
        return [v for v in vehicle_nums if v]

    if vehicle_nums:
        return [v for v in vehicle_nums if v]

    # Company mode: expand the allowed companies to their vehicles.
    try:
        company_codes = list(await platform_db.get_user_company_codes(user_id) or [])
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("AI scope: get_user_company_codes failed user=%s: %s", user_id, e)
        company_codes = []
    if not company_codes:
        return None  # "All" — no company restriction

    allowed = {c.strip().upper() for c in company_codes if c}
    # The registry is the roster of record: it still knows trucks whose
    # gateway went dark and units that only the TMS ever reported, both
    # of which the live overview silently drops — and history questions
    # are exactly what people ask the assistant about such trucks.  The
    # live overview is unioned in so a vehicle the registry has not
    # adopted yet stays reachable.
    names: set[str] = set()
    try:
        from infra.platform import get_tenant_db as _get_tenant
        tenant = await _get_tenant(account_id)
        for rv in await tenant.list_vehicles(account_id):
            if (rv.company_code or "").strip().upper() in allowed and rv.unit_number:
                names.add(rv.unit_number.strip())
    except Exception as e:  # registry unavailable — the overview may still serve
        logger.warning(
            "AI scope: registry roster failed acct=%s: %s", account_id, e,
        )
    try:
        from features.vehicles.service import get_vehicles_overview
        vehicles = await get_vehicles_overview(account_id)
        for v in vehicles:
            co = str(v.get("company_code") or v.get("_org") or "").strip().upper()
            if co in allowed and v.get("name"):
                names.add(str(v.get("name")).strip())
    except Exception as e:
        if not names:
            # Fail CLOSED: a company-restricted user whose vehicles we
            # cannot read from ANY source gets an empty scope (the gate
            # blocks everything) rather than a silent cross-company leak.
            logger.warning(
                "AI scope: vehicle overview failed acct=%s (failing closed): %s",
                account_id, e,
            )
            return []
        logger.warning(
            "AI scope: overview failed acct=%s; registry roster serves: %s",
            account_id, e,
        )
    return sorted(names)


async def resolve_scope_ladder(
    account_id: int, names: Optional[list[str]],
) -> Optional[dict]:
    """Resolve a name scope's identity rungs via the registry.

    Returns ``None`` for an unrestricted caller, else
    ``{"registry_ids": [...], "external_ids": [...]}`` (either may be
    empty when the registry does not know the names).  Names stay the
    wire contract; the rungs ride beside them so tools can decide by
    identity — a provider rename makes name equality miss the caller's
    OWN truck, and name equality alone cannot separate same-number
    twins across companies.
    """
    if names is None:
        return None
    if not names:
        return {"registry_ids": [], "external_ids": []}
    try:
        from capabilities.permissions.vehicle_scope import build_vehicle_scope
        from infra.platform import get_tenant_db as _get_tenant
        tenant = await _get_tenant(account_id)
        scope = await build_vehicle_scope(tenant, account_id, list(names))
        return {
            "registry_ids": sorted(scope.registry_ids),
            "external_ids": sorted(scope.external_ids),
        }
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "AI scope: ladder resolution failed acct=%s (name rung only): %s",
            account_id, e,
        )
        return {"registry_ids": [], "external_ids": []}
