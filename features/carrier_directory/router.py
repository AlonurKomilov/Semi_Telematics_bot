"""Carrier Knowledge Base API.

Read (list/detail) is gated on ``can_carrier_directory`` — held by every
``recruiter`` (employee or manager).  Every write (create/update/delete) is
gated on ``can_manage_carrier_directory`` — granted only to a recruiting
MANAGER (``recruiter`` + ``is_manager``, via MANAGER_GRANTS) — so plain
recruiters get a strictly read-only view.  Every row is account-scoped.

The per-carrier ``content`` is opaque JSON authored by the dashboard (the
sectioned pre-qual / presentation / recruiter-only field templates); the API
only validates it is a JSON object and bounds its size.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import get_platform_db, require_permission, resolve_user_id

logger = logging.getLogger("api.carrier_directory")

router = APIRouter(prefix="/carrier-directory", tags=["carrier_directory"])

# Generous cap on the JSON profile body — it holds ~70 short label→value rows
# plus a few text blocks, never file bytes (files are a later increment).
_MAX_CONTENT_BYTES = 256 * 1024


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
    return _hydrate(row)


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
