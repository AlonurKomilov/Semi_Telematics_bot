"""Settings · Companies — per-account company CRUD.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Keeps the historical ``/admin`` URL prefix.
"""
import asyncio
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from interfaces.api.deps import (
    require_permission, get_current_db_user, get_tenant_db,
    get_platform_db, paginate, resolve_user_id,
)
from adapters.storage.models import Role
from capabilities.permissions.roles import validate_role_change, role_rank

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["settings"])


# ── Companies ─────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    # Samsara keys are managed on the Integration card now, so the key is
    # optional here — a company can be created with just code + MC/DOT.
    samsara_api_key: str = ""
    display_name: str = ""
    active_days: int = Field(30, ge=1, le=365)
    mc_number: str = Field("", max_length=40)
    usdot_number: str = Field("", max_length=40)


class CompanyUpdate(BaseModel):
    display_name: Optional[str] = None
    samsara_api_key: Optional[str] = None
    active_days: Optional[int] = Field(None, ge=1, le=365)
    mc_number: Optional[str] = Field(None, max_length=40)
    usdot_number: Optional[str] = Field(None, max_length=40)


@router.get("/companies")
async def list_companies(
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """List companies in the account."""
    companies = await tenant_db.get_account_companies(user["account_id"], active_only=False)
    return {
        "companies": [
            {
                "id": c.id,
                "code": c.code,
                "display_name": c.display_name,
                "active_days": c.active_days,
                "is_active": c.is_active,
                "created_at": c.created_at,
                "has_api_key": bool(c.samsara_api_key),
                "mc_number": c.mc_number,
                "usdot_number": c.usdot_number,
            }
            for c in companies
        ],
        "count": len(companies),
    }


@router.post("/companies")
async def add_company(
    body: CompanyCreate,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Add a new company to the account."""
    existing = await tenant_db.get_company_by_code(user["account_id"], body.code)
    if existing:
        raise HTTPException(status_code=409, detail="Company code already exists")

    from interfaces.api.deps import enforce_company_quota
    await enforce_company_quota(user["account_id"], tenant_db=tenant_db)

    company = await tenant_db.add_company(
        account_id=user["account_id"],
        code=body.code,
        samsara_api_key=body.samsara_api_key,
        display_name=body.display_name or body.code,
        active_days=body.active_days,
        mc_number=body.mc_number,
        usdot_number=body.usdot_number,
    )
    # Drop the cached MultiCompanyClient so the new company is visible
    # to the next telematics call.  Without this, an owner who connected
    # the integration BEFORE adding companies keeps hitting the cached
    # zero-company client — "Test all" says "no companies" even though
    # the company list clearly shows one, until a process restart.
    from infra.services import invalidate_client
    await invalidate_client(user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_add",
        target_type="company", target_id=str(company.id),
        details=f"Code: {body.code}",
    )

    # Kick off historical backfill on the ARQ worker (4truck-queue
    # service) — runs in a *separate process* so the API stays
    # responsive for live users.  A 90-day backfill on a large fleet
    # can take 30-60s of Samsara round-trips; doing that inline in a
    # FastAPI worker would tie up a connection slot for the duration.
    # Idempotent (every writer dedups via UNIQUE/UPSERT), gap-aware
    # (skips sources that already have coverage), and dedup'd at the
    # queue level via job_id so rapid-fire admin actions don't spawn
    # parallel backfills for the same account.
    try:
        from infra import jobs as _jobs
        await _jobs.enqueue(
            "backfill_account_initial",
            account_id=user["account_id"],
            job_id=f"backfill_acct_{user['account_id']}",
        )
    except Exception:
        logger.exception("Failed to enqueue on-connect backfill for acct=%d", user["account_id"])

    return {"id": company.id, "code": company.code, "status": "created"}


@router.put("/companies/{company_id}")
async def update_company(
    company_id: int,
    body: CompanyUpdate,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Update company details."""
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    ok = await tenant_db.update_company(company_id, account_id=user["account_id"], **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Company not found")

    # Same rationale as add_company — key rotations and display
    # changes must not serve a stale cached client.
    from infra.services import invalidate_client
    await invalidate_client(user["account_id"])

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_update",
        target_type="company", target_id=str(company_id),
        details=str(list(kwargs.keys())),
    )

    # Re-run backfill on the ARQ worker when the token is rotated —
    # the new key may expose data the old one couldn't reach (different
    # license / wider scope).  job_id collapses duplicate enqueues so a
    # rapid double-PUT doesn't queue two parallel backfills.
    if "samsara_api_key" in kwargs:
        try:
            from infra import jobs as _jobs
            await _jobs.enqueue(
                "backfill_account_initial",
                account_id=user["account_id"],
                job_id=f"backfill_acct_{user['account_id']}",
            )
        except Exception:
            logger.exception("Failed to enqueue backfill on token rotation acct=%d", user["account_id"])

    return {"ok": True}


@router.delete("/companies/{company_id}")
async def deactivate_company(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Soft-delete (deactivate) a company."""
    ok = await tenant_db.remove_company(company_id, account_id=user["account_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Company not found")

    # Same rationale as add_company — drop the cached client so the
    # deactivated company stops being polled immediately.
    from infra.services import invalidate_client
    await invalidate_client(user["account_id"])

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_deactivate",
        target_type="company", target_id=str(company_id),
    )
    return {"ok": True}
