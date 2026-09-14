"""ELD API — the duty-clock mirror, over HTTP.

router.py is interface-layer code co-located with its feature — only
router.py may import interfaces.api.deps.

Routes:

    GET /eld/hours              every driver's current clocks
    GET /eld/hours/{user_id}    one driver's, by OUR user id

Both answers carry ``connected`` and each reading's age, because the
two questions this feature must never confuse are "nobody is near
their limit" and "no ELD has ever reported".

Gate: ``can_view_eld``, the feature's own verb.  There is no write
gate and no write route — the certified ELD is the system of record,
we mirror it, and the only setting is the integration's own toggle.

Scope: Team Management's unit question, asked through
``get_member_vehicle_scope`` rather than the paired-feature machinery.
ELD was born under the verb rule and has no ``_all``/``_vehicle``
pair for ``member_unit_scope`` to look up, and the width answer never
depended on the feature name anyway — it is the member's own scope
plus their role's default.  A member row that cannot be read falls
CLOSED to 'assigned': less data, never more, which for driver duty
status is the only acceptable direction to fail.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from features.eld.service import get_hours
from capabilities.permissions.vehicle_scope import (
    VehicleScope, build_vehicle_scope,
)
from interfaces.api.deps import (
    get_member_vehicle_scope,
    get_tenant_db,
    get_user_vehicle_assignments,
    require_permission,
)

router = APIRouter(prefix="/eld", tags=["eld"])

_VIEW = require_permission("can_view_eld")


async def _vehicle_scope(user: dict, tenant_db) -> VehicleScope | None:
    """The trucks this caller may see drivers for, or ``None`` for all.

    Assignments carry their registry identity, so a scope built from
    them admits ONE truck where a human has said which — and not its
    same-numbered twin in another company.  Names alone cannot do that,
    and duty status is the last place to be approximate about whose
    driver you are looking at.

    An empty scope is a real answer and is honoured as one: a member
    narrowed to assigned vehicles who has none assigned sees nobody.
    Reading an empty assignment list as "unrestricted" is how a scoped
    member would quietly get the whole account.
    """
    if await get_member_vehicle_scope(user) != "assigned":
        return None
    trucks = await get_user_vehicle_assignments(user)
    if not trucks:
        return VehicleScope()
    return await build_vehicle_scope(tenant_db, int(user["account_id"]), trucks)


@router.get("/hours")
async def hours(
    user: dict = Depends(_VIEW),
    tenant_db=Depends(get_tenant_db),
):
    """Current duty clocks for every driver this caller may see."""
    return await get_hours(
        tenant_db, int(user["account_id"]),
        vehicle_scope=await _vehicle_scope(user, tenant_db),
    )


@router.get("/hours/{user_id}")
async def driver_hours(
    user_id: int,
    user: dict = Depends(_VIEW),
    tenant_db=Depends(get_tenant_db),
):
    """One driver's clocks, for the Drivers page's HOS tab.

    Narrowed by the caller's vehicle scope as well as by the driver id,
    so asking for a specific person cannot reach past the scope that
    the list respects — the classic way a per-id route becomes the hole
    in a filtered list.

    404 rather than an empty list when the caller may not see that
    driver, so the route does not confirm the driver exists.  The
    ``connected`` field still tells the page whether an ELD is wired
    at all, which is a fact about the account and not about a person.
    """
    answer = await get_hours(
        tenant_db, int(user["account_id"]),
        user_id=user_id,
        vehicle_scope=await _vehicle_scope(user, tenant_db),
    )
    if not answer["drivers"]:
        if not answer["connected"]:
            return answer
        raise HTTPException(404, "No hours-of-service reading for that driver")
    return answer
