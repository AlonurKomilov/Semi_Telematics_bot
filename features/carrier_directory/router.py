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
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from features.carrier_directory.service import (
    MAX_SENDER_NAME, PUBLIC_FIELD_COUNT, clean_sender_name, resolve_sender_name,
)
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


def _safe_url(raw: str) -> str:
    """Accept only http(s) URLs for fields that end up in an ``<a href>``.

    ``website`` and ``video_url`` are writable by an UNAUTHENTICATED external
    carrier through the public intake endpoint, and the manager dashboard
    renders both as links — so a ``javascript:`` value would be stored XSS
    against the authenticated user reviewing the submission.  Anything with
    a non-http(s) scheme is dropped rather than coerced; a bare host gets
    ``https://``.  Mirrors ``safeHref`` in the dashboard's fields.ts, which
    is the second line of the same defence.
    """
    t = (raw or "").strip()
    if not t:
        return ""
    if re.match(r"^https?://", t, re.I):
        return t
    # A control-char-split payload ("java\tscript:") slips past this regex
    # by design: the fallback below prepends https://, turning it into a
    # harmless URL. The allow-list shape is the guarantee, not this regex.
    if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):
        return ""
    return f"https://{t}"


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
        website=_safe_url(body.website),
        video_url=_safe_url(body.video_url),
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
        fields["website"] = _safe_url(body.website)
    if body.video_url is not None:
        fields["video_url"] = _safe_url(body.video_url)
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


class BulkIds(BaseModel):
    # Bounded so one request can't walk the whole directory row-by-row.
    ids: list[int] = Field(..., min_length=1, max_length=200)


async def _bulk_over_carriers(platform_db, account_id: int, ids: list[int], *,
                              precondition, action) -> dict:
    """Run a per-carrier action over a selection, enforcing the SAME rules
    the single-carrier endpoint would.

    Bulk must never be a way to do something the individual action refuses,
    so each row is fetched account-scoped and checked before acting; a row
    that fails is SKIPPED WITH A REASON rather than failing the batch. The
    caller reports both halves, matching the applications bulk pattern —
    a silent partial success is how an operator ends up believing they
    actioned rows they didn't.
    """
    updated: list[int] = []
    skipped: list[dict] = []
    for cid in dict.fromkeys(ids):          # de-dupe, preserve order
        row = await platform_db.get_carrier_profile(account_id, cid)
        if not row:
            skipped.append({"id": cid, "reason": "not found"})
            continue
        reason = precondition(row)
        if reason:
            skipped.append({"id": cid, "reason": reason})
            continue
        await action(cid)
        updated.append(cid)
    return {"updated": len(updated), "updated_ids": updated, "skipped": skipped}


@router.post("/carriers/bulk-mark-reviewed")
async def bulk_mark_reviewed(
    body: BulkIds,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    """Clear the review flag on several carriers at once."""
    account_id = user["account_id"]
    return await _bulk_over_carriers(
        platform_db, account_id, body.ids,
        precondition=lambda r: (
            "" if r.get("intake_review_pending") else "nothing to review"
        ),
        action=lambda cid: platform_db.clear_carrier_intake_review(account_id, cid),
    )


@router.post("/carriers/bulk-revoke-link")
async def bulk_revoke_links(
    body: BulkIds,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    """Revoke several fill links at once.  Anything a carrier already
    submitted is kept — this only kills the URLs."""
    account_id = user["account_id"]
    return await _bulk_over_carriers(
        platform_db, account_id, body.ids,
        precondition=lambda r: (
            "" if (r.get("intake_token") or "") else "no active link"
        ),
        action=lambda cid: platform_db.revoke_carrier_intake(account_id, cid),
    )


@router.post("/carriers/{carrier_id:int}/mark-reviewed")
async def mark_carrier_reviewed(
    carrier_id: int,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    """Clear the review-pending flag without editing anything.

    Reviewing a carrier's submission and finding it correct is the common
    outcome; before this endpoint the only way to stop the badge was to
    perform a no-op save, which bumped ``updated_at`` and made the audit
    trail lie about who changed what."""
    account_id = user["account_id"]
    if not await platform_db.get_carrier_profile(account_id, carrier_id):
        raise HTTPException(status_code=404, detail="Carrier not found")
    await platform_db.clear_carrier_intake_review(account_id, carrier_id)
    return {"ok": True}


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
    # Sender name this one carrier sees.  Blank falls back to the
    # account-wide public name, then to neutral wording — never to the
    # tenant's registered name.  See service.resolve_sender_name.
    display_name: str = Field("", max_length=MAX_SENDER_NAME)


class IntakeLinkUpdate(BaseModel):
    """Partial edit of a live link.  Every field is optional; '' is a
    real value (clear the override), so omission is the only "leave
    alone" signal — read via ``model_fields_set``."""
    expires_in_days: int | None = Field(None, ge=1, le=180)
    email: str | None = Field(None, max_length=200)
    display_name: str | None = Field(None, max_length=MAX_SENDER_NAME)


async def _inviter_email(platform_db, user: dict) -> str:
    """The inviting manager's address, for the invite's Reply-To.

    A carrier asked for pay data by a deliberately unnamed sender still
    needs a reachable human — withholding the tenant's legal name is a
    privacy choice, leaving no reply path is just a dead end.  Best-effort:
    a missing address only costs the Reply-To header."""
    try:
        uid = await resolve_user_id(user)
        if not uid:
            return ""
        u = await platform_db.get_user_by_id(uid)
        return (getattr(u, "email", "") or "").strip()
    except Exception:
        return ""


async def _sender_name(platform_db, profile_row: dict, account_id: int) -> str:
    """Resolve the outward sender name for one carrier's link."""
    acct = None
    try:
        acct = await platform_db.get_account(account_id)
    except Exception:
        pass
    return resolve_sender_name(profile_row, acct)


@router.get("/sender-identity")
async def get_sender_identity(
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    """The account-wide public name a blank per-link override falls back
    to.  Lets the invite form state plainly what "leave blank" will do,
    so a manager is never guessing whether blank means anonymous or
    means the company name leaks.  Read-only — setting it is an
    account-wide right (Settings · Account)."""
    try:
        acct = await platform_db.get_account(user["account_id"])
    except Exception:
        # The caller treats this as "unknown" and hides the hint rather
        # than asserting a wrong one — a 500 here would be worse.
        raise HTTPException(status_code=503, detail="Could not read the account name")
    return {
        "account_display_name": getattr(acct, "public_display_name", "") or "",
    }


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
    issuing a fresh one (or by DELETE below).  To change the sender name
    or expiry WITHOUT killing an already-sent link, PATCH instead."""
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
    display_name = clean_sender_name(body.display_name)
    await platform_db.set_carrier_intake(
        account_id, carrier_id, token=token, expires_at=expires_at,
        email=email, invited_by=await resolve_user_id(user),
        display_name=display_name,
    )
    from features.applications.service import apply_base_url
    url = f"{apply_base_url()}/carrier/{token}"
    emailed = False
    if email:
        # Same resolver the public page uses, against the row as it now
        # stands — the email and the page can never name different senders.
        sender = await _sender_name(
            platform_db, {**row, "intake_display_name": display_name}, account_id,
        )
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
            to=email, carrier_name=row["name"], agency_name=sender,
            intake_url=url, expires_days=body.expires_in_days,
            prefilled=prefilled, reply_to=await _inviter_email(platform_db, user),
            field_count=PUBLIC_FIELD_COUNT,
        )
        # Stamp only a CONFIRMED hand-off, so the UI can tell "we emailed
        # them" apart from "a manager typed an address and the send failed".
        if emailed:
            await platform_db.mark_carrier_intake_emailed(carrier_id)
    return {
        "url": url, "token": token, "expires_at": expires_at,
        # False here when an address was given but the send failed — the
        # caller MUST surface that rather than reporting a flat success.
        "emailed": emailed,
        "email_requested": bool(email),
    }


@router.patch("/carriers/{carrier_id:int}/intake-link")
async def update_intake_link(
    carrier_id: int,
    body: IntakeLinkUpdate,
    user: dict = Depends(require_permission("can_manage_carrier_directory")),
    platform_db=Depends(get_platform_db),
):
    """Edit the live link's sender name / contact / expiry, KEEPING its
    token so a link already in the carrier's inbox keeps working.

    404s when the carrier has no live link — there is nothing to edit,
    and this must never bring a revoked token back."""
    from datetime import datetime, timezone, timedelta
    account_id = user["account_id"]
    row = await platform_db.get_carrier_profile(account_id, carrier_id)
    if not row:
        raise HTTPException(status_code=404, detail="Carrier not found")
    if not (row.get("intake_token") or ""):
        raise HTTPException(status_code=404, detail="No active link to edit")

    provided = body.model_fields_set
    fields: dict = {}
    if "display_name" in provided:
        fields["intake_display_name"] = clean_sender_name(body.display_name)
    if "email" in provided:
        fields["intake_email"] = (body.email or "").strip().lower()
    if "expires_in_days" in provided and body.expires_in_days is not None:
        # Re-bases the window from now, matching what a fresh mint would do.
        fields["intake_expires_at"] = (
            datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        ).isoformat()
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to update")

    # Trust the storage guard, not the check above: a concurrent revoke can
    # land between them, and the UPDATE's ``intake_token != ''`` is what
    # actually decides.  rowcount 0 means the link died under us — say so
    # rather than reporting success over a now-revoked row.
    if not await platform_db.update_carrier_intake(account_id, carrier_id, **fields):
        raise HTTPException(status_code=404, detail="No active link to edit")
    updated = await platform_db.get_carrier_profile(account_id, carrier_id)
    return {
        "ok": True,
        "expires_at": (updated or {}).get("intake_expires_at"),
        "display_name": (updated or {}).get("intake_display_name") or "",
        "email": (updated or {}).get("intake_email") or "",
    }


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
    unknown/revoked/expired token is a uniform 404.

    ``agency`` is the RESOLVED sender name and may legitimately be '' —
    the tenant's registered ``accounts.name`` is never sent here.  The
    page renders neutral wording when it's blank."""
    row = await platform_db.resolve_carrier_intake(token.strip())
    if not row:
        raise HTTPException(status_code=404, detail="Link not available")
    agency = await _sender_name(platform_db, row, row["account_id"])
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
        # Lets a returning carrier see that their answers DID land, instead
        # of a page byte-identical to their first visit.  A timestamp only —
        # nothing about who reviewed it or what the agency thought.
        "submitted_at": (row.get("intake_submitted_at") or "") or None,
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
        website=_safe_url(body.website),
        video_url=_safe_url(body.video_url),
        experience_summary=body.experience_summary.strip(),
        content=_dump_content(content),
    )
    # How much of the sheet actually came back, so the manager's mail can
    # distinguish a substantive submission from a one-field typo fix.
    filled = sum(
        1 for key in _INTAKE_ROW_SECTIONS
        for r in content.get(key, []) if str(r.get("value") or "").strip()
    ) + sum(
        1 for v in (
            body.website, body.video_url, body.experience_summary,
            content.get(_INTAKE_TEXT_SECTIONS[0], ""),
        ) if str(v or "").strip()
    )
    # Best-effort manager notification — never blocks the carrier's submit.
    asyncio.create_task(_notify_intake_submitted(
        platform_db, row["account_id"], row["id"], row["name"],
        filled_count=filled,
        # A second submit through a still-live link is a REVISION, not a
        # first fill — the two used to send identical mail.
        revision=bool((row.get("intake_submitted_at") or "").strip()),
    ))
    return {"ok": True}


async def _notify_intake_submitted(
    platform_db, account_id: int, carrier_id: int, carrier_name: str,
    *, filled_count: int = 0, revision: bool = False,
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
                    filled_count=filled_count, total_count=PUBLIC_FIELD_COUNT,
                    revision=revision,
                )
            except Exception as e:
                logger.debug("intake notify for user failed: %s", e)
    except Exception as e:
        logger.debug("intake notify fan-out failed: %s", e)
