"""Carrier Knowledge Base API.

Read (list/detail) is gated on ``can_carrier_directory`` — held by every
``recruiter`` (employee or manager).  Every write (create/update/delete) is
gated on ``can_manage_carrier_directory`` — granted only to a recruiting
MANAGER (``recruiter`` + ``is_manager``, via MANAGER_GRANTS) — so plain
recruiters get a strictly read-only view.  Every row is account-scoped.

The per-carrier ``content`` is opaque JSON authored by the dashboard (the
sectioned pre-qual / presentation / recruiter-only field templates); the API
only validates it is a JSON object and bounds its size.

Carrier self-fill intake: a manager can mint a tokenized public link
(``POST /carriers/{id}/intake-link``) and send it to the external carrier,
who fills their own sheet on the public apply host through the two
``/intake`` endpoints below.  Those are the ONLY unauthenticated routes
here — token-gated in-app, rate-limited, and edge-allowlisted on the apply
subdomain only (nginx denies every other /api/* path there).  The
"For Recruiters Only" section never crosses that boundary: the public GET
strips it and the public POST cannot write it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from interfaces.api.deps import get_platform_db, require_permission, resolve_user_id
from interfaces.api.rate_limit import limiter

logger = logging.getLogger("api.carrier_directory")

router = APIRouter(prefix="/carrier-directory", tags=["carrier_directory"])

# Generous cap on the JSON profile body — it holds ~70 short label→value rows
# plus a few text blocks, never file bytes (files are a later increment).
_MAX_CONTENT_BYTES = 256 * 1024

# Content sections an external carrier may read and write through the
# intake link.  ``recruiter_only`` is deliberately absent — it's the
# agency's internal playbook (turnaround, ownership terms).
_INTAKE_TEXT_SECTIONS = ("application_process",)
_INTAKE_ROW_SECTIONS = ("prequal", "presentation")


class CarrierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    website: str = Field("", max_length=300)
    video_url: str = Field("", max_length=500)
    experience_summary: str = Field("", max_length=500)
    content: dict = Field(default_factory=dict)


class CarrierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    website: str | None = Field(None, max_length=300)
    video_url: str | None = Field(None, max_length=500)
    experience_summary: str | None = Field(None, max_length=500)
    content: dict | None = None


def _dump_content(content: dict) -> str:
    """Serialise + size-check the profile body before it hits the DB."""
    raw = json.dumps(content, ensure_ascii=False)
    if len(raw.encode("utf-8")) > _MAX_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="Carrier profile is too large.")
    return raw


def _hydrate(row: dict) -> dict:
    """Parse the stored ``content`` JSON back into an object for the client."""
    out = dict(row)
    try:
        out["content"] = json.loads(row.get("content") or "{}")
    except (ValueError, TypeError):
        out["content"] = {}
    return out


async def _can_manage(user: dict) -> bool:
    """Whether the (already read-authorised) caller also holds the edit
    right — decides if the intake token is included in a detail response."""
    from adapters.storage import Role
    from capabilities.permissions.roles import get_user_permissions
    try:
        perms = await get_user_permissions(
            Role(user["role"]), user["account_id"],
            is_manager=bool(user.get("is_manager")),
            is_primary_owner=bool(user.get("is_primary_owner")),
        )
        return bool(perms.can_manage_carrier_directory)
    except Exception:
        return False


def _clean_rows(rows: object, *, max_rows: int = 150) -> list[dict]:
    """Normalise a carrier-submitted label→value list: strings only,
    bounded lengths/count, blanks dropped."""
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for r in rows[:max_rows]:
        if not isinstance(r, dict):
            continue
        label = str(r.get("label") or "").strip()[:160]
        value = str(r.get("value") or "").strip()[:4000]
        if label and value:
            out.append({"label": label, "value": value})
    return out


# ── Read — any recruiter (employee or manager) ──────────────────────
@router.get("/carriers")
async def list_carriers(
    user: dict = Depends(require_permission("can_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    items = await platform_db.list_carrier_profiles(user["account_id"])
    return {"items": items}


@router.get("/carriers/{carrier_id:int}")
async def get_carrier(
    carrier_id: int,
    user: dict = Depends(require_permission("can_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    row = await platform_db.get_carrier_profile(user["account_id"], carrier_id)
    if not row:
        raise HTTPException(status_code=404, detail="Carrier not found")
    out = _hydrate(row)
    # The intake token is an edit credential (the public form writes with
    # it) — read-only recruiters never receive it.
    if not await _can_manage(user):
        out.pop("intake_token", None)
    return out


# ── Write — recruiter managers only (recruiter + is_manager) ─────────
@router.post("/carriers")
async def create_carrier(
    body: CarrierCreate,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    row = await platform_db.create_carrier_profile(
        user["account_id"],
        name=body.name.strip(),
        website=body.website.strip(),
        video_url=body.video_url.strip(),
        experience_summary=body.experience_summary.strip(),
        content=_dump_content(body.content),
        created_by=await resolve_user_id(user),
    )
    return _hydrate(row)


@router.patch("/carriers/{carrier_id:int}")
async def update_carrier(
    carrier_id: int,
    body: CarrierUpdate,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    account_id = user["account_id"]
    if not await platform_db.get_carrier_profile(account_id, carrier_id):
        raise HTTPException(status_code=404, detail="Carrier not found")
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
    if body.website is not None:
        fields["website"] = body.website.strip()
    if body.video_url is not None:
        fields["video_url"] = body.video_url.strip()
    if body.experience_summary is not None:
        fields["experience_summary"] = body.experience_summary.strip()
    if body.content is not None:
        fields["content"] = _dump_content(body.content)
    await platform_db.update_carrier_profile(account_id, carrier_id, **fields)
    # A manager save doubles as the review acknowledgment for a carrier
    # intake submission — the "review pending" badge clears here.
    await platform_db.clear_carrier_intake_review(account_id, carrier_id)
    updated = await platform_db.get_carrier_profile(account_id, carrier_id)
    return _hydrate(updated)  # type: ignore[arg-type]


@router.delete("/carriers/{carrier_id:int}")
async def delete_carrier(
    carrier_id: int,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    account_id = user["account_id"]
    if not await platform_db.get_carrier_profile(account_id, carrier_id):
        raise HTTPException(status_code=404, detail="Carrier not found")
    await platform_db.delete_carrier_profile(account_id, carrier_id)
    return {"ok": True}


# ── Carrier self-fill intake links ───────────────────────────────────


class IntakeLinkCreate(BaseModel):
    expires_in_days: int = Field(30, ge=1, le=180)
    email: str = Field("", max_length=200)


@router.post("/carriers/{carrier_id:int}/intake-link")
async def create_intake_link(
    carrier_id: int,
    body: IntakeLinkCreate,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    """Mint (or rotate) the carrier's public fill-it-yourself link and
    optionally email it to the carrier's contact.  Minting again replaces
    the previous token, so an emailed link can always be invalidated by
    issuing a fresh one (or by DELETE below)."""
    from datetime import datetime, timezone, timedelta
    account_id = user["account_id"]
    row = await platform_db.get_carrier_profile(account_id, carrier_id)
    if not row:
        raise HTTPException(status_code=404, detail="Carrier not found")
    token = secrets.token_urlsafe(18)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    ).isoformat()
    email = body.email.strip().lower()
    await platform_db.set_carrier_intake(
        account_id, carrier_id, token=token, expires_at=expires_at,
        email=email, invited_by=await resolve_user_id(user),
    )
    from features.applications.service import apply_base_url
    url = f"{apply_base_url()}/carrier/{token}"
    emailed = False
    if email:
        acct_name = ""
        try:
            acct = await platform_db.get_account(account_id)
            acct_name = getattr(acct, "name", "") or ""
        except Exception:
            pass
        # Honest effort-lowering line: only claim "partly filled" when the
        # carrier-visible sheet really holds content.
        pub = _public_content(row.get("content"))
        prefilled = bool(
            (row.get("website") or "").strip()
            or (row.get("video_url") or "").strip()
            or (row.get("experience_summary") or "").strip()
            or any(v.strip() if isinstance(v, str) else v for v in pub.values())
        )
        from capabilities.email.application_emails import send_carrier_intake_email
        emailed = await asyncio.to_thread(
            send_carrier_intake_email,
            to=email, carrier_name=row["name"], agency_name=acct_name,
            intake_url=url, expires_days=body.expires_in_days,
            prefilled=prefilled,
        )
    return {"url": url, "token": token, "expires_at": expires_at, "emailed": emailed}


@router.delete("/carriers/{carrier_id:int}/intake-link")
async def revoke_intake_link(
    carrier_id: int,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    account_id = user["account_id"]
    if not await platform_db.get_carrier_profile(account_id, carrier_id):
        raise HTTPException(status_code=404, detail="Carrier not found")
    await platform_db.revoke_carrier_intake(account_id, carrier_id)
    return {"ok": True}


# ── Public intake endpoints (token-gated, on the apply host) ─────────


def _public_content(raw: str | None) -> dict:
    """The stored content minus everything a carrier must not see."""
    try:
        content = json.loads(raw or "{}")
    except (ValueError, TypeError):
        content = {}
    if not isinstance(content, dict):
        content = {}
    return {
        k: v for k, v in content.items()
        if k in _INTAKE_TEXT_SECTIONS or k in _INTAKE_ROW_SECTIONS
    }


@router.get("/intake")
@limiter.limit("120/hour")
async def public_intake_get(
    request: Request, token: str, platform_db=Depends(get_platform_db),
):
    """The carrier's current sheet, prefilled for the public form.  An
    unknown/revoked/expired token is a uniform 404."""
    row = await platform_db.resolve_carrier_intake(token.strip())
    if not row:
        raise HTTPException(status_code=404, detail="Link not available")
    agency = ""
    try:
        acct = await platform_db.get_account(row["account_id"])
        agency = getattr(acct, "name", "") or ""
    except Exception:
        pass
    return {
        "carrier": {
            "name": row["name"],
            "website": row["website"],
            "video_url": row["video_url"],
            "experience_summary": row["experience_summary"],
            "content": _public_content(row.get("content")),
        },
        "agency": agency,
        "expires_at": row.get("intake_expires_at"),
    }


class IntakeSubmit(BaseModel):
    token: str = Field(..., max_length=64)
    website: str = Field("", max_length=300)
    video_url: str = Field("", max_length=500)
    experience_summary: str = Field("", max_length=500)
    content: dict = Field(default_factory=dict)


@router.post("/intake")
@limiter.limit("30/hour")
async def public_intake_submit(
    request: Request, body: IntakeSubmit, platform_db=Depends(get_platform_db),
):
    """Store the carrier's answers and flag the profile for manager review.

    Only the carrier-visible sections are writable; the stored
    recruiter-only section is carried over untouched.  The link stays
    live until expiry so the carrier can revise their answers."""
    row = await platform_db.resolve_carrier_intake(body.token.strip())
    if not row:
        raise HTTPException(status_code=404, detail="Link not available")

    # Rebuild content server-side: sanitised carrier sections + the
    # preserved internal ones.  Nothing the carrier sends can create
    # unknown keys or touch recruiter_only.
    try:
        stored = json.loads(row.get("content") or "{}")
    except (ValueError, TypeError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    content: dict = {
        k: v for k, v in stored.items()
        if k not in _INTAKE_TEXT_SECTIONS and k not in _INTAKE_ROW_SECTIONS
    }
    for key in _INTAKE_TEXT_SECTIONS:
        content[key] = str(body.content.get(key) or "").strip()[:20_000]
    for key in _INTAKE_ROW_SECTIONS:
        content[key] = _clean_rows(body.content.get(key))

    await platform_db.submit_carrier_intake(
        row["id"],
        website=body.website.strip(),
        video_url=body.video_url.strip(),
        experience_summary=body.experience_summary.strip(),
        content=_dump_content(content),
    )
    # Best-effort manager notification — never blocks the carrier's submit.
    asyncio.create_task(_notify_intake_submitted(
        platform_db, row["account_id"], row["id"], row["name"],
    ))
    return {"ok": True}


async def _notify_intake_submitted(
    platform_db, account_id: int, carrier_id: int, carrier_name: str,
) -> None:
    """Email every user who can manage the directory that a carrier filled
    in their sheet.  Wholly best-effort."""
    try:
        from adapters.storage import Role
        from capabilities.permissions.roles import get_user_permissions
        from capabilities.email.application_emails import (
            send_carrier_intake_submitted_email,
        )
        from features.applications.service import review_base_url
        users = await platform_db.list_account_users(account_id)
        profile_url = f"{review_base_url()}/workforce/carrier-directory/{carrier_id}"
        for u in users:
            try:
                email = getattr(u, "email", None)
                if not email:
                    continue
                perms = await get_user_permissions(
                    Role(getattr(u.role, "value", u.role)), account_id,
                    is_manager=bool(getattr(u, "is_manager", False)),
                    is_primary_owner=bool(getattr(u, "is_primary_owner", False)),
                )
                if not perms.can_manage_carrier_directory:
                    continue
                await asyncio.to_thread(
                    send_carrier_intake_submitted_email,
                    to=email, carrier_name=carrier_name, profile_url=profile_url,
                )
            except Exception as e:
                logger.debug("intake notify for user failed: %s", e)
    except Exception as e:
        logger.debug("intake notify fan-out failed: %s", e)
