"""GET /me/tour-signals — a user's own action counts, nothing else."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from capabilities.tour import ALLOWED_SIGNALS
from interfaces.api.deps import get_current_user, get_tenant_db, resolve_user_id

router = APIRouter(prefix="/me", tags=["tour"])

_MAX_WINDOW_DAYS = 90


@router.get("/tour-signals")
async def tour_signals(
    pairs: str = Query(..., min_length=1, max_length=500),
    days: int = Query(14, ge=1, le=_MAX_WINDOW_DAYS),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Counts of the CALLER's own recent actions, per requested pair.

    ``pairs`` is comma-separated ``entity_type:action``.  Unknown pairs
    are refused, not ignored — a 400 teaches the author the allowlist
    exists, silence would teach them the signal is always zero.
    """
    wanted: list[tuple[str, str]] = []
    for raw in pairs.split(","):
        raw = raw.strip()
        if not raw:
            continue
        entity_type, _, action = raw.partition(":")
        if (entity_type, action) not in ALLOWED_SIGNALS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown signal pair {raw!r} — add it to "
                       f"capabilities/tour ALLOWED_SIGNALS first",
            )
        wanted.append((entity_type, action))

    actor = await resolve_user_id(user)
    out: dict[str, dict] = {}
    for entity_type, action in wanted:
        counts = await tenant_db.count_actor_actions(
            user["account_id"], actor, entity_type, action, days=days,
        )
        out[f"{entity_type}:{action}"] = counts
    return {"signals": out, "days": days}
