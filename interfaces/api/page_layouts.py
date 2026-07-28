"""Role-default page layouts — tier two of the page-config model.

A role manager saves their team's default arrangement of a feature
page's sections; every teammate's page then starts from it (each user's
personal preference still applies on top — Option A, a default not a
lock).

WHO may write follows the Group-delivery precedent exactly: the
``can_manage_role_pages`` grant gates the UI, and the API re-checks
is_manager + role regardless — owner/admin may set any role's default,
a manager only their own role's.  The flag alone never widens which
role may be edited.

WHAT is valid is shape-only here.  The backend does not know the
frontend's section registry, so required-section enforcement lives in
the frontend resolver, which ignores an invalid stored default
wholesale.  This endpoint just refuses obvious garbage so the table
can't be used as a scratchpad.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import get_current_user, get_tenant_db

router = APIRouter(prefix="/page-layouts", tags=["page-layouts"])

# Feature pages that HAVE configurable sections.  Grows one entry per
# page that opts into the gear — an allow-list, so the table can't
# accumulate rows for keys no page will ever read.
_ALLOWED_FEATURES = frozenset({"alerts"})

# Roles a team default may exist for — the dashboard personas that render
# Pattern-B pages.  Driver is deliberately absent (the Mini App is their
# surface); owner/admin ARE present so an owner can tune their own view's
# default too.
_VALID_ROLES = frozenset({
    "owner", "admin", "fleet", "dispatcher", "safety",
    "hr", "accounting", "recruiter",
})


def _may_manage_role_pages(user: dict, role: str) -> bool:
    """Owner/admin: any role.  A role manager: their OWN role only.

    Mirrors ``_may_manage_persona_bot`` — the grant is a UI affordance;
    THIS check is the enforcement, so a mis-seeded flag can never let a
    fleet manager rearrange the safety team's page.
    """
    own = user.get("role", "")
    if own in ("owner", "admin"):
        return True
    return bool(user.get("is_manager")) and own == role


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
    if not _may_manage_role_pages(user, role):
        raise HTTPException(
            status_code=403,
            detail="Only this role's manager (or an owner/admin) can set its team default",
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
    if not _may_manage_role_pages(user, role):
        raise HTTPException(
            status_code=403,
            detail="Only this role's manager (or an owner/admin) can clear its team default",
        )
    removed = await tenant_db.delete_page_layout(user["account_id"], role, feature)
    if removed is None:
        raise HTTPException(status_code=404, detail="No team default stored for this page")
    return {"ok": True}
