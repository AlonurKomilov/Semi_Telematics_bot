"""Driver-recruiting API — public application intake + recruiter dashboard.

PUBLIC surface (no auth — anyone with a recruiting link):
  POST /recruitment/apply           submit a driver application

RECRUITER surface (gated by can_recruit_applicants):
  POST   /recruitment/links                 create a shareable link
  GET    /recruitment/links                 list links
  POST   /recruitment/links/{id}/revoke     deactivate a link
  GET    /recruitment/applications          list submissions
  GET    /recruitment/applications/{id}     full detail (PII decrypted)
  PATCH  /recruitment/applications/{id}/status
  PATCH  /recruitment/applications/{id}/notes
  POST   /recruitment/applications/{id}/convert   → driver invite (can_convert_to_driver)

SECURITY POSTURE for the public endpoint (no Turnstile by product
decision — real drivers must not be blocked):
  • Rate-limited per IP (generous, DoS-bounded).
  • Token resolves to an account or 404 (no existence oracle).
  • Every uploaded file is sniffed by MAGIC BYTES (infra.file_safety),
    NOT the client Content-Type — only real JPEG/PNG/WEBP/PDF pass; an
    executable/script disguised as an image is rejected before storage.
  • Uploaded bytes are NEVER executed and stored under server-generated
    keys (no client filename → no path traversal).
  • All text fields are server-validated + length-capped; arrays capped.
  • PII (DOB/SSN) encrypted at rest by the storage layer.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import base64

import asyncio

from fastapi import (
    APIRouter, Depends, Form, File, UploadFile, HTTPException, Request, Query,
    BackgroundTasks, Response,
)
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_current_user, get_platform_db, get_tenant_db, require_permission,
    get_current_db_user,
)
from interfaces.api.rate_limit import limiter
from infra.file_safety import validate_upload

logger = logging.getLogger("api.recruitment")

router = APIRouter(prefix="/recruitment", tags=["recruitment"])

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Per-file cap for the public intake (lower than the authed driver-docs
# 20 MB — applicants upload phone photos / a PDF, not archives).
_MAX_FILE_BYTES = 8 * 1024 * 1024
# Defensive caps so a malicious payload can't be giant even within the
# body-size envelope.
_MAX_STR = 500
_MAX_TEXTAREA = 4000
_MAX_ARRAY = 30


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _cap_strings(obj):
    """Recursively trim strings to defensive lengths + strip NULs.

    Stored data is escaped by React on render, but we still cap length
    and strip control characters server-side so a hostile payload can't
    bloat the DB or smuggle NUL bytes.
    """
    if isinstance(obj, str):
        s = obj.replace("\x00", "")
        return s[:_MAX_TEXTAREA]
    if isinstance(obj, list):
        return [_cap_strings(x) for x in obj[:_MAX_ARRAY]]
    if isinstance(obj, dict):
        return {k: _cap_strings(v) for k, v in obj.items()}
    return obj


def _validate_application(data: dict) -> None:
    """Server-side re-validation — never trust the client.

    Raises HTTPException(422) on a structural / required-field failure.
    Mirrors the form's client validators but is the authoritative gate.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Malformed application")
    personal = data.get("personal")
    if not isinstance(personal, dict):
        raise HTTPException(status_code=422, detail="Missing personal section")
    for req in ("first", "last", "email"):
        v = personal.get(req)
        if not isinstance(v, str) or not v.strip():
            raise HTTPException(status_code=422, detail=f"Missing required field: {req}")
        if len(v) > _MAX_STR:
            raise HTTPException(status_code=422, detail=f"Field too long: {req}")
    if not _EMAIL_RE.match(personal["email"].strip()):
        raise HTTPException(status_code=422, detail="Invalid email")
    # Employment history must be a (bounded) list per §391.21.
    emp = data.get("employment")
    if emp is not None and (not isinstance(emp, list) or len(emp) > _MAX_ARRAY):
        raise HTTPException(status_code=422, detail="Invalid employment history")
    # All legally load-bearing consents must be affirmatively given —
    # these authorize the FMCSA/FCRA background queries the carrier is
    # required to obtain (PSP/MCMIS, MVR, Drug & Alcohol Clearinghouse
    # §382.701, FCRA consumer report, DOT drug screen) plus the truthful
    # certification.  Server-enforced (not just client-side) because the
    # consents are the legal basis for pulling a candidate's records; a
    # direct API POST must not be able to submit without them.  Keys
    # mirror the public form's CONSENTS list.
    consents = data.get("consents") or {}
    _REQUIRED_CONSENTS = ("psp", "mvr", "clearinghouse", "fcra", "drug", "truthful")
    missing = [k for k in _REQUIRED_CONSENTS if not consents.get(k)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Required consent(s) not accepted: {', '.join(missing)}",
        )


def _gen_reference() -> str:
    return "APP-" + secrets.token_hex(3).upper()


def _decode_data_url(data_url: str) -> bytes | None:
    """Decode a ``data:...;base64,XXXX`` URL → bytes (the signature canvas)."""
    try:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        return base64.b64decode(data_url, validate=True)
    except Exception:
        return None


# ── New-application notification fan-out ────────────────────────────


async def _notify_new_application(
    platform_db, account_id: int, application_id: int, reference: str,
    applicant_name: str,
) -> None:
    """Alert every account user holding ``can_recruit_applicants`` that a
    new application landed — on each recipient's chosen channels.

    Targeting is by the PERMISSION (the SSOT, via ``get_account_permissions``
    so per-account overrides are honoured), never a hardcoded role.  Each
    recipient's channel set (telegram / email / dashboard) is their own
    preference; a missing prefs row means all channels.  Runs as a
    background task — wholly best-effort, one failed channel/recipient
    never affects another or the applicant's submission.
    """
    try:
        from capabilities.permissions.roles import get_account_permissions
        users = await platform_db.list_account_users(account_id)
    except Exception as e:
        logger.debug("notify_new_application: setup failed: %s", e)
        return

    # Resolve effective perms once per distinct role (cached).
    perm_cache: dict = {}

    async def _can_recruit(role) -> bool:
        key = getattr(role, "value", role)
        if key not in perm_cache:
            try:
                fs = await get_account_permissions(role, account_id)
                perm_cache[key] = bool(getattr(fs, "can_recruit_applicants", False))
            except Exception:
                perm_cache[key] = False
        return perm_cache[key]

    # Bot app (may be absent on an API-only worker).
    try:
        from infra.bot_registry import get_app_for_account
        bot_app = get_app_for_account(account_id)
    except Exception:
        bot_app = None

    acct_name = ""
    try:
        acct = await platform_db.get_account(account_id)
        acct_name = getattr(acct, "name", "") or ""
    except Exception:
        pass
    review_url = f"{_recruit_review_base()}/workforce/applications"
    title = "New driver application"
    body = f"{applicant_name or 'A driver'} applied · {reference}"

    for u in users:
        try:
            if not await _can_recruit(u.role):
                continue
            channels = await platform_db.get_recruitment_notify_channels(u.id)

            if "dashboard" in channels:
                try:
                    await platform_db.create_recruitment_notification(
                        account_id, u.id, application_id=application_id,
                        reference=reference, title=title, body=body,
                    )
                except Exception as e:
                    logger.debug("in-app notif for user %s failed: %s", u.id, e)

            if "email" in channels and getattr(u, "email", None):
                try:
                    from capabilities.email.recruiting_emails import send_new_application_email
                    await asyncio.to_thread(
                        send_new_application_email,
                        to=u.email, account_name=acct_name,
                        applicant_name=applicant_name, reference=reference,
                        review_url=review_url,
                    )
                except Exception as e:
                    logger.debug("email notif for user %s failed: %s", u.id, e)

            if "telegram" in channels and getattr(u, "telegram_id", None) and bot_app and getattr(bot_app, "bot", None):
                try:
                    from telegram.constants import ParseMode
                    await bot_app.bot.send_message(
                        chat_id=u.telegram_id,
                        text=(f"📝 <b>New driver application</b>\n"
                              f"{applicant_name or 'A driver'} · <code>{reference}</code>\n"
                              f"<i>Review it on your dashboard → Applications.</i>"),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e:
                    logger.debug("telegram notif for user %s failed: %s", u.id, e)
        except Exception as e:
            logger.debug("notify recipient %s failed: %s", getattr(u, "id", "?"), e)


def _recruit_review_base() -> str:
    """Base origin for the recruiter review link in notifications."""
    import os
    return (
        os.getenv("DASHBOARD_BASE_URL")
        or os.getenv("AUTH_BASE_URL")
        or "https://dash.4truck.us"
    ).rstrip("/")


# ── Public: submit an application ───────────────────────────────────


@router.post("/apply")
@limiter.limit("30/hour")
async def submit_application(
    request: Request,
    link_token: str = Form(...),
    application: str = Form(...),
    cdl_front: UploadFile | None = File(None),
    cdl_back: UploadFile | None = File(None),
    medical: UploadFile | None = File(None),
    truck_photo: UploadFile | None = File(None),
    dot_inspection: UploadFile | None = File(None),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
    platform_db=Depends(get_platform_db),
):
    """Public driver-application submission.  No auth; link token gates it."""
    # 1. Token → account (uniform 404; no oracle for which tokens exist).
    link = await platform_db.resolve_recruitment_link(link_token.strip())
    if not link:
        raise HTTPException(status_code=404, detail="This application link is no longer available.")
    account_id = link["account_id"]

    # 2. Parse + cap + validate the JSON payload.
    try:
        data = json.loads(application)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Malformed application payload")
    data = _cap_strings(data)
    _validate_application(data)

    reference = _gen_reference()

    # 3. Files — magic-byte validation, then store under generated keys.
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(account_id, platform_db)
    bucket = f"applications/{reference}"
    docs: dict[str, str] = {}

    file_slots = {
        "cdlFront": cdl_front, "cdlBack": cdl_back, "medical": medical,
        "truckPic": truck_photo, "dotInspection": dot_inspection,
    }
    required_slots = ("cdlFront", "cdlBack", "medical")
    ext_for = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}

    for slot, upload in file_slots.items():
        if upload is None:
            if slot in required_slots:
                raise HTTPException(status_code=422, detail=f"Missing required document: {slot}")
            continue
        raw = await upload.read()
        ok, mime, reason = validate_upload(raw, max_bytes=_MAX_FILE_BYTES)
        if not ok:
            raise HTTPException(
                status_code=422,
                detail=f"Document '{slot}' rejected: {reason.replace('_', ' ')}",
            )
        key = f"{slot}.{ext_for[mime]}"
        try:
            docs[slot] = store.put(bucket, key, raw)
        except Exception:
            logger.exception("application doc store failed slot=%s ref=%s", slot, reference)
            raise HTTPException(status_code=500, detail="Could not store uploaded document.")

    # 4. Signature canvas (draw mode) — validate it's a real PNG, store it.
    consents = data.get("consents") or {}
    sig_data_url = consents.get("sigDataUrl")
    if consents.get("sigMode") == "draw" and sig_data_url:
        sig_bytes = _decode_data_url(sig_data_url)
        ok, mime, _ = validate_upload(sig_bytes or b"", max_bytes=2 * 1024 * 1024)
        if ok and mime == "image/png":
            try:
                docs["signature"] = store.put(bucket, "signature.png", sig_bytes)
            except Exception:
                logger.exception("signature store failed ref=%s", reference)

    # 5. Persist (storage layer encrypts DOB/SSN).
    try:
        created = await platform_db.create_driver_application(
            account_id, link_token=link_token.strip(), reference=reference,
            data=data, docs=docs, submit_ip=_client_ip(request),
        )
    except Exception:
        logger.exception("create_driver_application failed ref=%s acct=%s", reference, account_id)
        raise HTTPException(status_code=500, detail="Could not save your application. Please try again.")

    # 6. Audit (best-effort).
    try:
        await platform_db.add_platform_audit(
            "driver_application_submitted",
            account_id=account_id,
            actor="public-applicant",
            details=f"ref={reference} ip={_client_ip(request)}",
        )
    except Exception:
        logger.exception("application audit write failed ref=%s", reference)

    # 7. Notify the recruiting team (background — never blocks the
    #    applicant's response; targets can_recruit_applicants holders on
    #    each one's chosen channels).
    personal = data.get("personal") or {}
    applicant_name = f"{personal.get('first', '')} {personal.get('last', '')}".strip()
    if background_tasks is not None:
        background_tasks.add_task(
            _notify_new_application, platform_db, account_id,
            created["id"], reference, applicant_name,
        )

    logger.info("Driver application %s submitted for account %s", reference, account_id)
    return {"success": True, "reference": reference, "application_id": created["id"]}


class TrackViewRequest(BaseModel):
    token: str = Field(..., max_length=80)


@router.post("/track-view", status_code=204)
@limiter.limit("300/hour")
async def track_link_view(
    request: Request,
    body: TrackViewRequest,
    platform_db=Depends(get_platform_db),
):
    """Public per-link view ping (top-of-funnel analytics).  ALWAYS 204 —
    increments only when the token is valid+active, so it never reveals
    which tokens exist (same no-oracle stance as /apply)."""
    try:
        await platform_db.increment_link_view(body.token.strip())
    except Exception:
        pass
    return Response(status_code=204)


# ── Recruiter: links ────────────────────────────────────────────────


class CreateLinkRequest(BaseModel):
    label: str = Field(default="", max_length=120)
    source: str = Field(default="", max_length=60)
    # Auto-close window in days; None / 0 → never expires.  Capped at ~2yr.
    expires_in_days: int | None = Field(default=None, ge=0, le=730)


@router.post("/links")
async def create_link(
    body: CreateLinkRequest,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    """Create a shareable recruiting link.  The recruiter copies the token
    into a /apply/<token> URL and distributes it."""
    db_user_id = None
    try:
        from interfaces.api.deps import get_current_db_user
        du = await get_current_db_user(user, platform_db)
        db_user_id = du.id if du else None
    except Exception:
        pass
    return await platform_db.create_recruitment_link(
        user["account_id"], label=body.label, source=body.source,
        created_by=db_user_id, expires_in_days=body.expires_in_days,
    )


@router.get("/links")
async def list_links(
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    return {"items": await platform_db.list_recruitment_links(user["account_id"])}


@router.post("/links/{link_id}/revoke")
async def revoke_link(
    link_id: int,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    ok = await platform_db.set_recruitment_link_active(user["account_id"], link_id, False)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"status": "revoked"}


# ── Recruiter: applications ─────────────────────────────────────────


@router.get("/applications")
async def list_applications(
    status: str = Query(default="", description="filter by status"),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    items = await platform_db.list_driver_applications(
        user["account_id"], status=status, limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/applications/{app_id}")
async def get_application(
    app_id: int,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    app = await platform_db.get_driver_application(user["account_id"], app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    # Re-applicant detection (recruiter-side only — never blocked the
    # public form): other applications in this account sharing SSN/email/phone.
    try:
        app["related"] = await platform_db.find_duplicate_applications(user["account_id"], app_id)
    except Exception:
        app["related"] = []
    return app


class StatusRequest(BaseModel):
    status: str = Field(..., min_length=2, max_length=30)


# Allowed lifecycle states — server is the authority.
_VALID_STATUSES = frozenset({
    "submitted", "screening", "interview", "approved",
    "rejected", "withdrawn", "hired",
})

# Legal transitions for the manual status PATCH — the server is the
# authority, so the pipeline can't be jumped arbitrarily.  Two invariants
# matter most:
#   * 'hired' is NOT reachable here — it is set ONLY by the Hire action
#     (POST .../convert), which mints the driver invite.  Flipping to
#     'hired' via this PATCH would leave a "hired" record with no driver.
#   * 'hired' is terminal here — a real driver now exists, so they're
#     managed in Team Management, not by reverting the application.
# Forward + sideways moves among the active stages are allowed; a
# rejected/withdrawn candidate can be re-opened to 'screening'.
_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "submitted":  frozenset({"screening", "interview", "approved", "rejected", "withdrawn"}),
    "screening":  frozenset({"submitted", "interview", "approved", "rejected", "withdrawn"}),
    "interview":  frozenset({"screening", "approved", "rejected", "withdrawn"}),
    "approved":   frozenset({"screening", "interview", "rejected", "withdrawn"}),
    "rejected":   frozenset({"screening"}),
    "withdrawn":  frozenset({"screening"}),
    "hired":      frozenset(),
}

# Pre-hire vetting checks.  The applicant consented to each FMCSA query;
# recording that it was actually run is what makes 'approved' a real
# compliance gate.  ``_REQUIRED_VETTING`` must all be done before a
# candidate can be approved (and convert already requires 'approved').
_VETTING_CHECKS = ("psp", "mvr", "clearinghouse", "drug", "background")
_REQUIRED_VETTING = ("psp", "mvr", "clearinghouse")


def _missing_required_vetting(app: dict) -> list[str]:
    vetting = app.get("vetting") or {}
    return [c for c in _REQUIRED_VETTING if not (vetting.get(c) or {}).get("done")]


@router.patch("/applications/{app_id}/status")
async def set_status(
    app_id: int,
    body: StatusRequest,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    if body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status")

    # Enforce the lifecycle: load the current state and reject illegal
    # jumps (404 if the app doesn't exist / isn't this account's).
    current_app = await platform_db.get_driver_application(
        user["account_id"], app_id, decrypt_pii=False,
    )
    if not current_app:
        raise HTTPException(status_code=404, detail="Application not found")
    current = current_app.get("status") or "submitted"
    if body.status != current:
        if body.status == "hired":
            raise HTTPException(
                status_code=409,
                detail="Use the Hire action to convert an applicant to a driver",
            )
        if body.status not in _STATUS_TRANSITIONS.get(current, frozenset()):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot move an application from '{current}' to '{body.status}'",
            )
        # Approval is gated on a completed vetting checklist — a candidate
        # can't be 'approved' (and therefore hired) until the required
        # FMCSA queries are recorded as run.
        if body.status == "approved":
            missing = _missing_required_vetting(current_app)
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail="Complete the required checks before approving: "
                           + ", ".join(c.upper() for c in missing),
                )

    reviewer = None
    try:
        from interfaces.api.deps import get_current_db_user
        du = await get_current_db_user(user, platform_db)
        reviewer = du.id if du else None
    except Exception:
        pass
    ok = await platform_db.update_application_status(
        user["account_id"], app_id, body.status, reviewed_by=reviewer,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"status": body.status}


class NotesRequest(BaseModel):
    notes: str = Field(default="", max_length=4000)


@router.patch("/applications/{app_id}/notes")
async def set_notes(
    app_id: int,
    body: NotesRequest,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    ok = await platform_db.set_application_notes(user["account_id"], app_id, body.notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"status": "saved"}


class VettingRequest(BaseModel):
    check: str = Field(..., min_length=2, max_length=30)
    done: bool = True


@router.patch("/applications/{app_id}/vetting")
async def set_vetting(
    app_id: int,
    body: VettingRequest,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    """Tick (or untick) one pre-hire check — PSP / MVR / Clearinghouse /
    drug / background.  Stamps who ran it + when.  Required checks gate
    the 'approved' transition (see ``set_status``)."""
    if body.check not in _VETTING_CHECKS:
        raise HTTPException(status_code=422, detail="Unknown check")
    reviewer = None
    try:
        du = await get_current_db_user(user, platform_db)
        reviewer = du.id if du else None
    except Exception:
        pass
    vetting = await platform_db.update_application_vetting(
        user["account_id"], app_id, body.check, body.done, reviewer,
    )
    if vetting is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"vetting": vetting, "required": list(_REQUIRED_VETTING)}


@router.get("/applications/{app_id}/packet.pdf")
async def download_application_packet(
    app_id: int,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    """The printable §391.51 Driver Qualification File packet — full
    application + consents + vetting + signature, as a retainable PDF.
    Permission-gated, account-scoped, ``no-store`` (contains PII)."""
    app = await platform_db.get_driver_application(user["account_id"], app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    # Embed the drawn signature image if there is one.
    sig_png = None
    sig_id = (app.get("docs") or {}).get("signature")
    if sig_id:
        try:
            from adapters.storage.object_store import get_object_store_for_account
            store = await get_object_store_for_account(user["account_id"], platform_db)
            sig_png = store.get_by_id(sig_id)
        except Exception:
            sig_png = None

    acct_name = ""
    try:
        acct = await platform_db.get_account(user["account_id"])
        acct_name = getattr(acct, "name", "") or ""
    except Exception:
        pass

    import datetime as _dt
    from capabilities.reporting.dq_packet_pdf import build_dq_packet_pdf
    try:
        buf = build_dq_packet_pdf(
            app, account_name=acct_name, signature_png=sig_png,
            generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        )
    except Exception:
        logger.exception("DQ packet build failed app=%s", app_id)
        raise HTTPException(status_code=500, detail="Could not build the packet PDF.")

    from fastapi.responses import StreamingResponse
    ref = app.get("reference") or f"app-{app_id}"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="application-{ref}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/applications/{app_id}/convert")
async def convert_to_driver(
    app_id: int,
    user: dict = Depends(require_permission("can_convert_to_driver")),
    platform_db=Depends(get_platform_db),
):
    """Hire an applicant → mint a driver invite + mark the app hired.

    Gated by the NARROW ``can_convert_to_driver`` (not the broad
    ``can_invite``) so a recruiter can hire without full user-invite
    power.  Returns the invite code + a /signup/<code> link the recruiter
    shares with the new driver.  The invite carries
    ``source_application_id`` so ``redeem_invite`` stamps this
    application's ``converted_to_user_id`` once the driver onboards —
    closing the application↔driver round-trip.
    """
    from adapters.storage import Role

    app = await platform_db.get_driver_application(user["account_id"], app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.get("status") == "hired":
        raise HTTPException(status_code=409, detail="Applicant already hired")
    # Gate the hire on a completed review — a driver must not be onboarded
    # before their FMCSA vetting (PSP / MVR / Clearinghouse) is reviewed and
    # the application moved to 'approved'.  Without this the pipeline stages
    # are decorative and a recruiter could hire straight from 'submitted'.
    if app.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="Applicant must be 'approved' before hiring",
        )

    db_user_id = None
    try:
        from interfaces.api.deps import get_current_db_user
        du = await get_current_db_user(user, platform_db)
        db_user_id = du.id if du else None
    except Exception:
        pass

    invite = await platform_db.create_invite(
        user["account_id"],
        created_by=db_user_id or 0,
        role=Role.DRIVER,
        hours=168,  # 7-day window for a new hire to onboard
        source_application_id=app_id,
    )

    await platform_db.update_application_status(
        user["account_id"], app_id, "hired", reviewed_by=db_user_id,
    )
    try:
        await platform_db.add_platform_audit(
            "driver_application_converted",
            account_id=user["account_id"],
            actor=f"recruiter:{db_user_id}",
            details=f"app_id={app_id} ref={app.get('reference')} invite={invite.code}",
        )
    except Exception:
        logger.exception("convert audit write failed app=%s", app_id)

    from interfaces.api.auth import _signup_base_url
    return {
        "status": "hired",
        "invite_code": invite.code,
        "invite_link": f"{_signup_base_url()}/signup/{invite.code}",
    }


# ── Recruiter: view an uploaded document ────────────────────────────

# The document slots a reviewer may fetch.  The stored values are
# server-generated object-store ids (never client-supplied paths), so
# there is no traversal surface — we only ever resolve the requested slot
# against THIS application's own docs map.
_DOC_SLOTS = frozenset({
    "cdlFront", "cdlBack", "medical", "truckPic", "dotInspection", "signature",
})
_MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}


@router.get("/applications/{app_id}/docs/{slot}")
async def get_application_doc(
    app_id: int,
    slot: str,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    """Stream one uploaded document (CDL front/back, DOT medical card,
    truck photo, DOT inspection, or the signature) back to a reviewer.

    Account-scoped + permission-gated; served ``inline`` so the dashboard
    can preview it.  PII (a driver's licence image) → ``no-store`` so it
    never lands in a shared/disk cache.
    """
    if slot not in _DOC_SLOTS:
        raise HTTPException(status_code=404, detail="Unknown document")
    app = await platform_db.get_driver_application(
        user["account_id"], app_id, decrypt_pii=False,
    )
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    object_id = (app.get("docs") or {}).get(slot)
    if not object_id:
        raise HTTPException(status_code=404, detail="Document not found")

    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(user["account_id"], platform_db)
    raw = store.get_by_id(object_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="Document not found")

    from infra.file_safety import sniff_mime
    mime = sniff_mime(raw) or "application/octet-stream"
    ext = _MIME_EXT.get(mime, "bin")

    import io
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(raw),
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{slot}.{ext}"',
            "Cache-Control": "private, no-store",
        },
    )


# ── Recruiter: in-app notifications + channel preferences ───────────


async def _recipient_id(user: dict, platform_db) -> int:
    """The logged-in user's DB id — the notification recipient key."""
    du = await get_current_db_user(user, platform_db)
    if du is None:
        raise HTTPException(status_code=401, detail="User not found")
    return du.id


@router.get("/notifications")
async def list_my_notifications(
    unread: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    acct = user["account_id"]
    items = await platform_db.list_recruitment_notifications(acct, uid, unread_only=unread, limit=limit)
    unread_count = await platform_db.count_unread_recruitment_notifications(acct, uid)
    return {"items": items, "unread_count": unread_count}


class MarkReadRequest(BaseModel):
    # None / omitted → mark ALL of the user's notifications read.
    ids: list[int] | None = None


@router.post("/notifications/read")
async def mark_my_notifications_read(
    body: MarkReadRequest,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    marked = await platform_db.mark_recruitment_notifications_read(
        user["account_id"], uid, ids=body.ids,
    )
    return {"marked": marked}


@router.get("/notify-prefs")
async def get_my_notify_prefs(
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    channels = await platform_db.get_recruitment_notify_channels(uid)
    return {"channels": sorted(channels)}


class NotifyPrefsRequest(BaseModel):
    channels: list[str] = Field(default_factory=list)


@router.put("/notify-prefs")
async def set_my_notify_prefs(
    body: NotifyPrefsRequest,
    user: dict = Depends(require_permission("can_recruit_applicants")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    channels = await platform_db.set_recruitment_notify_channels(
        user["account_id"], uid, body.channels,
    )
    return {"channels": sorted(channels)}
