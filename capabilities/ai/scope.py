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
    try:
        from features.vehicles.service import get_vehicles_overview
        fleet = await get_vehicles_overview(account_id)
    except Exception as e:
        # Fail CLOSED: a company-restricted user whose fleet we can't read
        # gets an empty scope (the gate blocks everything) rather than a
        # silent cross-company leak.
        logger.warning(
            "AI scope: fleet overview failed acct=%s (failing closed): %s",
            account_id, e,
        )
        return []

    names = [
        v.get("name")
        for v in fleet
        if str(v.get("company_code") or v.get("_org") or "").strip().upper() in allowed
        and v.get("name")
    ]
    return names
