"""Role-default page layouts — tier two of the page-config model.

A role manager saves their team's default arrangement of a feature
page's sections; every teammate's page then starts from it (each user's
personal preference still applies on top — Option A, a default not a
lock).

WHO may write is decided by the Permissions matrix, so the owner sets
the scale: ``can_manage_account`` may set ANY role's default;
``can_manage_config_role`` (seeded on at manager tier, delegatable to
any tier via the matrix) may set the caller's OWN role's only.  The
own-role wall is code, not configuration — no grant combination lets a
fleet user rearrange the safety team's page.

WHAT is valid is shape-only here.  The backend does not know the
frontend's section registry, so required-section enforcement lives in
the frontend resolver, which ignores an invalid stored default
wholesale.  This endpoint just refuses obvious garbage so the table
can't be used as a scratchpad.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from capabilities.config.role import (
    ALLOWED_FEATURES, VALID_ROLES, may_manage_config_role,
)
from interfaces.api.deps import get_current_user, get_tenant_db

router = APIRouter(prefix="/page-layouts", tags=["page-layouts"])

# _ALLOWED_FEATURES, _VALID_ROLES and the own-role wall moved to
# capabilities/config/role.py — the config family's home, beside the
# account-scope registry.  What stays here is routing: this module is the
# interface layer and the only one that may import interfaces.api.deps.
_ALLOWED_FEATURES = ALLOWED_FEATURES
_VALID_ROLES = VALID_ROLES
_may_manage_config_role = may_manage_config_role


class PageLayoutBody(BaseModel):
    # Bounds are generous for any real page (alerts has 10 sections) and
    # tight enough that the row can't become a dumping ground.
    sections: list[str] = Field(..., min_length=1, max_length=40)


@router.get("")
async def list_page_layouts(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Every stored role default for the account, keyed role → feature.

    Readable by ANY authed user: each person needs their own role's
    default to render their page, and the rows contain nothing but
    section ids — there is no data here to protect from a teammate.
    """
    rows = await tenant_db.get_page_layouts(user["account_id"])
    layouts: dict = {}
    for r in rows:
        layouts.setdefault(r["role"], {})[r["feature"]] = r["sections"]
    return {"layouts": layouts}


@router.put("/{role}/{feature}")
async def set_page_layout(
    role: str,
    feature: str,
    body: PageLayoutBody,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail="Unknown role")
    if feature not in _ALLOWED_FEATURES:
        raise HTTPException(status_code=422, detail="Unknown feature page")
    if not await _may_manage_config_role(user, role):
        raise HTTPException(
            status_code=403,
            detail="Setting this role's team default needs the feature-config permission",
        )
    sections = [s.strip() for s in body.sections if s.strip()]
    if not sections or len(set(sections)) != len(sections):
        raise HTTPException(status_code=422, detail="Sections must be unique and non-empty")
    if any(len(s) > 64 for s in sections):
        raise HTTPException(status_code=422, detail="Section id too long")
    await tenant_db.upsert_page_layout(
        user["account_id"], role, feature, sections, int(user["sub"]),
    )
    return {"ok": True, "role": role, "feature": feature, "sections": sections}


@router.delete("/{role}/{feature}")
async def clear_page_layout(
    role: str,
    feature: str,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    # Same guards as PUT.  A row can only exist for values PUT accepted,
    # so this is symmetry rather than necessity — but the next handler
    # copied from this one may not have that invariant.
    if role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail="Unknown role")
    if feature not in _ALLOWED_FEATURES:
        raise HTTPException(status_code=422, detail="Unknown feature page")
    if not await _may_manage_config_role(user, role):
        raise HTTPException(
            status_code=403,
            detail="Clearing this role's team default needs the feature-config permission",
        )
    removed = await tenant_db.delete_page_layout(user["account_id"], role, feature)
    if removed is None:
        raise HTTPException(status_code=404, detail="No team default stored for this page")
    return {"ok": True}
