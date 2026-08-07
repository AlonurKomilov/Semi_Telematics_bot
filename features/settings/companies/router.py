"""Settings · Companies — per-account company CRUD.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Keeps the historical ``/admin`` URL prefix.
"""
import logging

import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from interfaces.api.deps import (
    require_permission, get_tenant_db,
    get_platform_db, resolve_user_id,
)
from capabilities.activity_trail import record_simple

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
    # Brand/identity.  brand_color is a #RRGGBB hex (or '' to clear) — the
    # pattern blocks CSS injection when it's later applied as a token.
    brand_color: Optional[str] = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    website: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=40)


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
                "brand_color": c.brand_color,
                "website": c.website,
                "phone": c.phone,
                "has_logo": bool(c.logo_object_id),
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
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "company_add", "company", company.id,
        changes={"code": {"from": None, "to": body.code},
                 "display_name": {"from": None,
                                  "to": body.display_name or body.code}},
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

    # Field NAMES only — the values here include rotated API keys,
    # which must never land in the trail (secrets, not PII masking).
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "company_update", "company", company_id,
        note=f"fields: {', '.join(kwargs.keys())}",
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


# ── Company logo (brand identity) ─────────────────────────────

_LOGO_MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@router.post("/companies/{company_id}/logo")
async def upload_company_logo(
    company_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Upload a company logo (JPG/PNG/WEBP ≤ 2 MB).  Magic-byte validated
    (never trusts the client type); stored under a generated key."""
    company = await tenant_db.get_company_in_account(user["account_id"], company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    raw = await file.read()
    from infra.file_safety import validate_upload
    ok, mime, _ = validate_upload(raw, max_bytes=2 * 1024 * 1024)
    if not ok or mime not in _LOGO_MIME_EXT:
        raise HTTPException(status_code=422, detail="Logo must be a JPG, PNG, or WEBP image under 2 MB")
    from adapters.storage.object_storage import get_object_storage_for_account
    from features.work_orders.storage import sanitize_company_folder
    store = await get_object_storage_for_account(user["account_id"], platform_db)
    # Brand assets live IN the company's folder ({COMPANY}/branding/);
    # row id in the FILENAME keeps same-named companies collision-free.
    folder = sanitize_company_folder(company.display_name or getattr(company, "code", "") or "")
    try:
        oid = store.put(f"{folder}/branding", f"logo-{company_id}.{_LOGO_MIME_EXT[mime]}", raw)
    except Exception:
        logger.exception("company logo store failed company=%s", company_id)
        raise HTTPException(status_code=500, detail="Could not store the logo.")
    await tenant_db.update_company(company_id, account_id=user["account_id"], logo_object_id=oid)
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "company_logo_set", "company", company_id,
    )
    return {"ok": True}


@router.get("/companies/{company_id}/logo")
async def get_company_logo(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Serve the company logo to the dashboard (authed, account-scoped)."""
    company = await tenant_db.get_company_in_account(user["account_id"], company_id)
    if not company or not company.logo_object_id:
        raise HTTPException(status_code=404, detail="No logo")
    from adapters.storage.object_storage import get_object_storage_for_account
    store = await get_object_storage_for_account(user["account_id"], platform_db)
    raw = store.get_by_id(company.logo_object_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="No logo")
    from infra.file_safety import sniff_mime
    mime = sniff_mime(raw) or "image/png"
    return StreamingResponse(io.BytesIO(raw), media_type=mime,
                             headers={"Cache-Control": "private, max-age=300"})


@router.delete("/companies/{company_id}/logo")
async def remove_company_logo(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Clear the company logo (drops the reference)."""
    ok = await tenant_db.update_company(company_id, account_id=user["account_id"], logo_object_id="")
    if not ok:
        raise HTTPException(status_code=404, detail="Company not found")
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

    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "company_deactivate", "company", company_id,
        changes={"is_active": {"from": True, "to": False}},
    )
    return {"ok": True}
