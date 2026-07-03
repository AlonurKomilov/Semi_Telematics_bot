"""Driver Applications API — public application intake + recruiter dashboard.

router.py is interface-layer code co-located with its feature (see
docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
service/storage modules never do.  Naming: the *feature* is "applications";
the *role* that manages it is "recruiter" — intentionally distinct words.
(The physical DB tables keep their legacy ``recruitment_*`` names — renaming
live tables isn't worth fighting schema.create_tables for invisible names.)

PUBLIC surface (no auth — anyone with an application link):
  POST /applications/apply           submit a driver application

RECRUITER surface (gated by can_manage_applications):
  POST   /applications/links                 create a shareable link
  GET    /applications/links                 list links
  POST   /applications/links/{id}/revoke     deactivate a link
  GET    /applications          list submissions
  GET    /applications/{id}     full detail (PII decrypted)
  PATCH  /applications/{id}/status
  PATCH  /applications/{id}/notes
  POST   /applications/{id}/convert   → driver invite (can_convert_to_driver)

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


from fastapi import (
    APIRouter, Depends, Form, File, UploadFile, HTTPException, Request, Query,
    BackgroundTasks, Response,
)
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_platform_db, require_permission, get_current_db_user,
)
from interfaces.api.rate_limit import limiter
from infra.file_safety import validate_upload
from features.applications import service

logger = logging.getLogger("api.applications")

router = APIRouter(prefix="/applications", tags=["applications"])

# Per-file cap for the public intake (lower than the authed driver-docs
# 20 MB — applicants upload phone photos / a PDF, not archives).
_MAX_FILE_BYTES = 8 * 1024 * 1024


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


# ── Public: submit an application ───────────────────────────────────


@router.post("/apply")
@limiter.limit("30/hour")
async def submit_application(
    request: Request,
    link_token: str = Form(...),
    application: str = Form(...),
    hp: str = Form(""),  # honeypot — must stay empty
    cdl_front: UploadFile | None = File(None),
    cdl_back: UploadFile | None = File(None),
    medical: UploadFile | None = File(None),
    truck_photo: UploadFile | None = File(None),
    dot_inspection: UploadFile | None = File(None),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
    platform_db=Depends(get_platform_db),
):
    """Public driver-application submission.  No auth; link token gates it."""
    # 0. Honeypot — a hidden field only a form-filling bot completes.  Accept-
    #    and-drop SILENTLY (a 200 that looks like success) so the bot gets no
    #    signal to adapt; store nothing, notify no one.
    if hp.strip():
        logger.info("Honeypot tripped on /apply (ip=%s) — dropping silently", _client_ip(request))
        return {"success": True, "reference": service.gen_reference(), "application_id": 0}

    # 1. Token → account (uniform 404; no oracle for which tokens exist).
    link = await platform_db.resolve_application_link(link_token.strip())
    if not link:
        raise HTTPException(status_code=404, detail="This application link is no longer available.")
    account_id = link["account_id"]

    # 2. Parse + cap + validate the JSON payload.
    try:
        data = json.loads(application)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Malformed application payload")
    data = service.cap_strings(data)
    try:
        service.validate_application(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    reference = service.gen_reference()

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
        sig_bytes = service.decode_data_url(sig_data_url)
        ok, mime, _ = validate_upload(sig_bytes or b"", max_bytes=2 * 1024 * 1024)
        if ok and mime == "image/png" and sig_bytes:
            try:
                docs["signature"] = store.put(bucket, "signature.png", sig_bytes)
            except Exception:
                logger.exception("signature store failed ref=%s", reference)

    # 5. Persist (storage layer encrypts DOB/SSN).
    try:
        created = await platform_db.create_driver_application(
            account_id, link_token=link_token.strip(), reference=reference,
            data=data, docs=docs, submit_ip=_client_ip(request),
            company_id=link.get("company_id"),
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
    #    applicant's response; targets can_manage_applications holders on
    #    each one's chosen channels).
    personal = data.get("personal") or {}
    applicant_name = f"{personal.get('first', '')} {personal.get('last', '')}".strip()
    if background_tasks is not None:
        background_tasks.add_task(
            service.notify_new_application, platform_db, account_id,
            created["id"], reference, applicant_name,
        )

    # The submitted application supersedes any saved draft — drop it so the
    # recruiter's "In progress" list never shows an already-submitted person.
    try:
        if personal.get("email"):
            await platform_db.delete_application_draft(
                account_id, link_token=link_token.strip(), email=personal["email"],
            )
    except Exception:   # best-effort cleanup; never fail the submission
        pass

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


# ── Public: carrier branding for the apply form ─────────────────────


async def _link_company(platform_db, token: str):
    """token → (link, company) or (None, None).  Shared by the brand +
    logo endpoints.  Never reveals the Samsara key."""
    link = await platform_db.resolve_application_link((token or "").strip())
    if not link or not link.get("company_id"):
        return link, None
    co = await platform_db.get_company_in_account(link["account_id"], link["company_id"])
    return link, co


@router.get("/brand")
@limiter.limit("120/hour")
async def public_link_brand(
    request: Request, token: str, platform_db=Depends(get_platform_db),
):
    """Public carrier branding for a recruiting link's apply form.

    Returns ONLY display fields (name/colour/contact/MC/DOT/logo flag) —
    NEVER the company's Samsara key.  An unknown/expired token or a link
    with no company returns ``{"company": null}`` (the form falls back to
    its generic look); always 200, so it's no oracle for token existence."""
    _link, co = await _link_company(platform_db, token)
    if not co:
        return {"company": None}
    return {"company": {
        "name": co.display_name or co.code,
        "brand_color": co.brand_color,
        "website": co.website,
        "phone": co.phone,
        "mc_number": co.mc_number,
        "usdot_number": co.usdot_number,
        "has_logo": bool(co.logo_object_id),
        "headline": co.headline,
        # Perks → a clean list of selling-point lines for the form.
        "perks": [p.strip() for p in (co.perks or "").splitlines() if p.strip()],
        "has_banner": bool(co.banner_object_id),
        # Pre-qual gate thresholds — the form adapts its question text.
        "req_experience_years": co.req_experience_years,
        "req_min_age": co.req_min_age,
        "req_cdl_class": co.req_cdl_class,
        # Base theme + optional extra colours (header band / page bg).
        "form_theme": co.form_theme,
        "surface_color": co.surface_color,
        "header_color": co.header_color,
        "bg_color": co.bg_color,
        "heading_color": co.heading_color,
        # Legal/compliance details that fill the consent disclosures.
        "legal_address": co.legal_address,
        "compliance_email": co.compliance_email,
        "cra_name": co.cra_name,
        "cra_address": co.cra_address,
        "cra_phone": co.cra_phone,
        "cra_site": co.cra_site,
    }}


@router.get("/brand-logo")
@limiter.limit("240/hour")
async def public_link_logo(
    request: Request, token: str, platform_db=Depends(get_platform_db),
):
    """Public carrier logo for the apply form (served for an ``<img src>``).
    404 when the token/company/logo is absent."""
    link, co = await _link_company(platform_db, token)
    if not co or not co.logo_object_id:
        raise HTTPException(status_code=404, detail="No logo")
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(link["account_id"], platform_db)
    try:
        raw = store.get_by_id(co.logo_object_id)
    except Exception:
        raw = None
    if not raw:
        raise HTTPException(status_code=404, detail="No logo")
    from infra.file_safety import sniff_mime
    mime = sniff_mime(raw) or "image/png"
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "public, max-age=300"})


@router.get("/brand-banner")
@limiter.limit("240/hour")
async def public_link_banner(
    request: Request, token: str, platform_db=Depends(get_platform_db),
):
    """Public carrier hero photo for the apply form (served for an
    ``<img src>``).  404 when the token/company/photo is absent."""
    link, co = await _link_company(platform_db, token)
    if not co or not co.banner_object_id:
        raise HTTPException(status_code=404, detail="No photo")
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(link["account_id"], platform_db)
    try:
        raw = store.get_by_id(co.banner_object_id)
    except Exception:
        raw = None
    if not raw:
        raise HTTPException(status_code=404, detail="No photo")
    from infra.file_safety import sniff_mime
    mime = sniff_mime(raw) or "image/jpeg"
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "public, max-age=300"})


@router.post("/ocr-cdl")
@limiter.limit("10/hour")
async def ocr_cdl(
    request: Request,
    token: str = Form(...),
    file: UploadFile = File(...),
    platform_db=Depends(get_platform_db),
):
    """Read a CDL-front photo → prefill fields (the form's "fast-fill").

    Public but guarded: a valid recruiting-link token is required, the rate
    limit is tight (each call is a paid vision-model request), and the image
    passes the same magic-byte validation as the apply upload.  Best-effort
    by contract — the response is always 200 with ``{"fields": null}`` on
    any extraction failure, so the form silently falls back to manual entry.
    The image is processed in memory only; nothing is stored here.
    """
    link = await platform_db.resolve_application_link(token.strip())
    if not link:
        raise HTTPException(status_code=404, detail="Link not available")
    raw = await file.read()
    ok, mime, reason = validate_upload(raw, max_bytes=_MAX_FILE_BYTES)
    if not ok or not mime.startswith("image/"):
        raise HTTPException(
            status_code=422,
            detail="Please upload a photo (JPG/PNG/WEBP) of your license.",
        )
    from features.applications.ocr import extract_cdl_fields
    fields = await extract_cdl_fields(raw, account_id=link["account_id"])
    return {"fields": fields}


# ── Save & resume (drafts) ──────────────────────────────────────────
# The in-progress form syncs server-side once the applicant's email is
# known, so they can continue on ANY device via an emailed resume link.
# The draft body is pre-consent PII: stored encrypted, never readable by
# recruiters (their list shows name/progress only), unlocked only by the
# resume token + the matching email, and purged after 14 idle days.

_MAX_DRAFT_BYTES = 256 * 1024


def _db_time(s: object):
    """Stored timestamp (``_now()`` tz-aware isoformat, or a pg cast) → naive
    UTC datetime; ``None`` when unparseable.  Naive-vs-aware comparisons raise
    TypeError, so everything is normalised to naive UTC before comparing."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


class DraftSave(BaseModel):
    link_token: str = Field(..., max_length=64)
    email: str = Field(..., max_length=200)
    draft_secret: str | None = Field(None, max_length=64)
    first_name: str = Field("", max_length=80)
    last_name: str = Field("", max_length=80)
    step: int = Field(0, ge=0, le=50)
    steps_total: int = Field(0, ge=0, le=50)
    data: dict = Field(default_factory=dict)


@router.post("/draft")
@limiter.limit("120/hour")
async def save_draft(
    request: Request, body: DraftSave, platform_db=Depends(get_platform_db),
):
    """Upsert the caller's draft.  Returns the in-session write credential;
    the resume token travels ONLY by email (see /draft/send-link)."""
    link = await platform_db.resolve_application_link(body.link_token.strip())
    if not link:
        raise HTTPException(status_code=404, detail="Link not available")
    email = body.email.strip().lower()
    if not service.EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Valid email required")
    raw = json.dumps(body.data, ensure_ascii=False)
    if len(raw.encode("utf-8")) > _MAX_DRAFT_BYTES:
        raise HTTPException(status_code=413, detail="Draft too large")
    from infra.crypto import encrypt
    saved = await platform_db.upsert_application_draft(
        link["account_id"], link_token=body.link_token.strip(), email=email,
        draft_secret=body.draft_secret,
        first_name=body.first_name.strip()[:80], last_name=body.last_name.strip()[:80],
        step=body.step, steps_total=body.steps_total,
        data_encrypted=encrypt(raw),
    )
    if saved is None:
        # A draft exists for this (link, email) but the caller doesn't hold
        # its secret — refuse to clobber it.
        raise HTTPException(status_code=403, detail="Draft belongs to another session")
    return {"saved": True, "draft_secret": saved["draft_secret"]}


class DraftSendLink(BaseModel):
    link_token: str = Field(..., max_length=64)
    email: str = Field(..., max_length=200)
    draft_secret: str = Field(..., max_length=64)


@router.post("/draft/send-link")
@limiter.limit("6/hour")
async def send_draft_link(
    request: Request, body: DraftSendLink, platform_db=Depends(get_platform_db),
):
    """Email the applicant their cross-device resume link."""
    link = await platform_db.resolve_application_link(body.link_token.strip())
    if not link:
        raise HTTPException(status_code=404, detail="Link not available")
    email = body.email.strip().lower()
    # Prove draft ownership WITHOUT touching the draft: read-only secret check.
    probe = await platform_db.get_application_draft_by_secret(
        link["account_id"], link_token=body.link_token.strip(), email=email,
        draft_secret=body.draft_secret,
    )
    if probe is None:
        raise HTTPException(status_code=403, detail="No saved draft for this session")
    _link, co = await _link_company(platform_db, body.link_token.strip())
    carrier = (co.display_name or co.code) if co else ""
    resume_url = f"{service.apply_base_url()}/resume/{probe['resume_token']}"
    from capabilities.email.application_emails import send_resume_link_email
    sent = send_resume_link_email(to=email, carrier_name=carrier, resume_url=resume_url)
    if sent:
        await platform_db.mark_draft_emailed(probe["id"])
    return {"sent": bool(sent)}


class DraftResume(BaseModel):
    resume_token: str = Field(..., max_length=64)
    email: str = Field(..., max_length=200)


@router.post("/draft/resume")
@limiter.limit("12/hour")
async def resume_draft(
    request: Request, body: DraftResume, platform_db=Depends(get_platform_db),
):
    """Unlock a draft: the emailed token AND the matching email (re-entered
    by the applicant) are both required.  Expired drafts refuse."""
    row = await platform_db.get_application_draft_by_resume(
        body.resume_token.strip(), body.email,
    )
    if not row:
        raise HTTPException(status_code=404, detail="No saved application found")
    from datetime import datetime, timedelta
    updated = _db_time(row["updated_at"])
    if updated is not None and datetime.utcnow() > updated + timedelta(days=14):
        raise HTTPException(status_code=410, detail="This saved application has expired")
    from infra.crypto import decrypt
    try:
        data = json.loads(decrypt(row["data_encrypted"]) or "{}")
    except (ValueError, TypeError):
        data = {}
    return {
        "link_token": row["link_token"],
        "draft_secret": row["draft_secret"],
        "step": row["step"],
        "data": data,
    }


@router.get("/drafts")
async def list_drafts(
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Recruiter view of in-progress applications — name/progress only.

    The draft BODY is pre-consent PII and is deliberately never exposed
    here; the email is masked (contact happens via the Remind nudge, not
    by recruiters copying addresses)."""
    rows = await platform_db.list_application_drafts(user["account_id"])
    def _mask(e: str) -> str:
        name, _, dom = (e or "").partition("@")
        return (name[:1] + "***@" + dom) if dom else "***"
    return {"items": [{
        "id": r["id"], "first_name": r["first_name"], "last_name": r["last_name"],
        "email_masked": _mask(r["email"]),
        "step": r["step"], "steps_total": r["steps_total"],
        "link_label": r.get("link_label") or "",
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        "reminder_sent_at": r.get("reminder_sent_at"),
    } for r in rows]}


@router.post("/drafts/{draft_id:int}/remind")
async def remind_draft(
    draft_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Re-send the resume link to the applicant (recruiter nudge).  Capped
    to one reminder per 20 hours per draft."""
    row = await platform_db.get_application_draft(user["account_id"], draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    from datetime import datetime, timedelta
    last = _db_time(row.get("reminder_sent_at")) if row.get("reminder_sent_at") else None
    if last is not None and datetime.utcnow() < last + timedelta(hours=20):
        raise HTTPException(status_code=429, detail="Reminder already sent recently")
    _link, co = await _link_company(platform_db, row["link_token"])
    carrier = (co.display_name or co.code) if co else ""
    resume_url = f"{service.apply_base_url()}/resume/{row['resume_token']}"
    from capabilities.email.application_emails import send_resume_link_email
    sent = send_resume_link_email(
        to=row["email"], carrier_name=carrier, resume_url=resume_url, reminder=True,
    )
    if sent:
        await platform_db.mark_draft_emailed(row["id"], reminder=True)
    return {"sent": bool(sent)}


class StatusCheckRequest(BaseModel):
    reference: str = Field(..., max_length=40)
    email: str = Field(..., max_length=200)


@router.post("/application-status")
@limiter.limit("20/hour")
async def check_application_status(
    request: Request,
    body: StatusCheckRequest,
    platform_db=Depends(get_platform_db),
):
    """Public self-service status check — TWO-FACTOR (reference + email).
    Returns a uniform {found: false} on any mismatch (no enumeration
    oracle) and is rate-limited.  Surfaces only the status + submit date —
    no other PII."""
    row = await platform_db.get_application_status_public(body.reference, body.email)
    if not row:
        return {"found": False}
    return {"found": True, "status": row.get("status"), "submitted_at": row.get("submitted_at")}


# ── Recruiter: links ────────────────────────────────────────────────


class CreateLinkRequest(BaseModel):
    label: str = Field(default="", max_length=120)
    source: str = Field(default="", max_length=60)
    # Auto-close window in days; None / 0 → never expires.  Capped at ~2yr.
    expires_in_days: int | None = Field(default=None, ge=0, le=730)
    # Which sub-company (carrier) this link is for — brands the apply form.
    # None → generic/account brand.
    company_id: int | None = None


@router.get("/companies")
async def list_recruiter_companies(
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Company list + brand preview for the link company-picker — lets a
    recruiter pick a carrier WITHOUT granting company management (the
    Settings·Companies surface stays owner/admin-gated).  Returns the
    cosmetic brand (logo flag / colour / contact) plus the owner-managed
    identity (name / MC / DOT) for the preview — NEVER the Samsara key."""
    companies = await platform_db.get_account_companies(user["account_id"], active_only=True)
    return {"items": [
        {"id": c.id, "code": c.code, "display_name": c.display_name,
         "has_logo": bool(c.logo_object_id), "brand_color": c.brand_color,
         "website": c.website, "phone": c.phone,
         "mc_number": c.mc_number, "usdot_number": c.usdot_number,
         "headline": c.headline, "perks": c.perks,
         "has_banner": bool(c.banner_object_id),
         "req_experience_years": c.req_experience_years,
         "req_min_age": c.req_min_age, "req_cdl_class": c.req_cdl_class,
         "form_theme": c.form_theme, "surface_color": c.surface_color,
         "header_color": c.header_color, "bg_color": c.bg_color,
         "heading_color": c.heading_color,
         "legal_address": c.legal_address, "compliance_email": c.compliance_email,
         "cra_name": c.cra_name, "cra_address": c.cra_address,
         "cra_phone": c.cra_phone, "cra_site": c.cra_site}
        for c in companies
    ]}


# ── Recruiter: company brand touch-up (cosmetic only) ───────────────
# A recruiter managing apply forms can fix a carrier's COSMETIC brand
# (logo / colour / contact) without owner access to Settings·Companies.
# The owner-verified identity (name / MC / DOT) and the Samsara key are
# NEVER writable here — those stay on the can_manage_companies surface.

_LOGO_MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class BrandUpdate(BaseModel):
    brand_color: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    website: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)
    # Apply-form content: a one-line pitch + newline-separated selling points.
    headline: str | None = Field(None, max_length=140)
    perks: str | None = Field(None, max_length=800)
    # Per-carrier pre-qual gate thresholds (adapt the form's gate questions).
    req_experience_years: int | None = Field(None, ge=0, le=20)
    req_min_age: int | None = Field(None, ge=18, le=99)
    req_cdl_class: str | None = Field(None, pattern=r"^[ABC]$")
    # Apply-form base theme + optional extra colours (hex or empty=default).
    form_theme: str | None = Field(None, pattern=r"^(light|dark)$")
    surface_color: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    header_color: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    bg_color: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    heading_color: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    # Legal/compliance details that fill the consent disclosures (blanks only).
    legal_address: str | None = Field(None, max_length=200)
    compliance_email: str | None = Field(None, max_length=120)
    cra_name: str | None = Field(None, max_length=160)
    cra_address: str | None = Field(None, max_length=200)
    cra_phone: str | None = Field(None, max_length=40)
    cra_site: str | None = Field(None, max_length=200)


@router.patch("/companies/{company_id:int}/brand")
async def update_company_brand(
    company_id: int,
    body: BrandUpdate,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Update a carrier's cosmetic brand (colour / website / phone)."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co:
        raise HTTPException(status_code=404, detail="Company not found")
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if fields:
        await platform_db.update_company(company_id, account_id=user["account_id"], **fields)
    return {"ok": True}


@router.post("/companies/{company_id:int}/logo")
async def upload_company_logo(
    company_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Upload a carrier logo (JPG/PNG/WEBP ≤ 2 MB).  Magic-byte validated."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co:
        raise HTTPException(status_code=404, detail="Company not found")
    raw = await file.read()
    ok, mime, _ = validate_upload(raw, max_bytes=2 * 1024 * 1024)
    if not ok or mime not in _LOGO_MIME_EXT:
        raise HTTPException(status_code=422, detail="Logo must be a JPG, PNG, or WEBP image under 2 MB")
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(user["account_id"], platform_db)
    try:
        oid = store.put(f"company-logos/{company_id}", f"logo.{_LOGO_MIME_EXT[mime]}", raw)
    except Exception:
        logger.exception("recruiter company logo store failed company=%s", company_id)
        raise HTTPException(status_code=500, detail="Could not store the logo.")
    await platform_db.update_company(company_id, account_id=user["account_id"], logo_object_id=oid)
    return {"ok": True}


@router.get("/companies/{company_id:int}/logo")
async def get_recruiter_company_logo(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Serve a carrier logo to the recruiter's create-link preview."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co or not co.logo_object_id:
        raise HTTPException(status_code=404, detail="No logo")
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(user["account_id"], platform_db)
    raw = store.get_by_id(co.logo_object_id)
    if not raw:
        raise HTTPException(status_code=404, detail="No logo")
    from infra.file_safety import sniff_mime
    mime = sniff_mime(raw) or "image/png"
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "private, max-age=60"})


@router.delete("/companies/{company_id:int}/logo")
async def remove_recruiter_company_logo(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Clear a carrier logo."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co:
        raise HTTPException(status_code=404, detail="Company not found")
    await platform_db.update_company(company_id, account_id=user["account_id"], logo_object_id="")
    return {"ok": True}


@router.post("/companies/{company_id:int}/banner")
async def upload_company_banner(
    company_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Upload the apply-form hero photo (JPG/PNG/WEBP ≤ 4 MB).  Magic-byte
    validated; never trusts the client type."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co:
        raise HTTPException(status_code=404, detail="Company not found")
    raw = await file.read()
    ok, mime, _ = validate_upload(raw, max_bytes=4 * 1024 * 1024)
    if not ok or mime not in _LOGO_MIME_EXT:
        raise HTTPException(status_code=422, detail="Photo must be a JPG, PNG, or WEBP image under 4 MB")
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(user["account_id"], platform_db)
    try:
        oid = store.put(f"company-banners/{company_id}", f"banner.{_LOGO_MIME_EXT[mime]}", raw)
    except Exception:
        logger.exception("recruiter company banner store failed company=%s", company_id)
        raise HTTPException(status_code=500, detail="Could not store the photo.")
    await platform_db.update_company(company_id, account_id=user["account_id"], banner_object_id=oid)
    return {"ok": True}


@router.get("/companies/{company_id:int}/banner")
async def get_recruiter_company_banner(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Serve a carrier hero photo to the recruiter's create-link preview."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co or not co.banner_object_id:
        raise HTTPException(status_code=404, detail="No photo")
    from adapters.storage.object_store import get_object_store_for_account
    store = await get_object_store_for_account(user["account_id"], platform_db)
    raw = store.get_by_id(co.banner_object_id)
    if not raw:
        raise HTTPException(status_code=404, detail="No photo")
    from infra.file_safety import sniff_mime
    mime = sniff_mime(raw) or "image/jpeg"
    return Response(content=raw, media_type=mime,
                    headers={"Cache-Control": "private, max-age=60"})


@router.delete("/companies/{company_id:int}/banner")
async def remove_recruiter_company_banner(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Clear the carrier hero photo."""
    co = await platform_db.get_company_in_account(user["account_id"], company_id)
    if not co:
        raise HTTPException(status_code=404, detail="Company not found")
    await platform_db.update_company(company_id, account_id=user["account_id"], banner_object_id="")
    return {"ok": True}


@router.post("/links")
async def create_link(
    body: CreateLinkRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Create a shareable recruiting link.  The recruiter copies the token
    into a /apply/<token> URL and distributes it."""
    # If a company is chosen, it MUST belong to this account.
    if body.company_id is not None:
        co = await platform_db.get_company_in_account(user["account_id"], body.company_id)
        if not co:
            raise HTTPException(status_code=422, detail="Unknown company")

    db_user_id = None
    try:
        du = await get_current_db_user(user, platform_db)
        db_user_id = du.id if du else None
    except Exception:
        pass
    return await platform_db.create_application_link(
        user["account_id"], label=body.label, source=body.source,
        created_by=db_user_id, expires_in_days=body.expires_in_days,
        company_id=body.company_id,
    )


@router.get("/links")
async def list_links(
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    return {"items": await platform_db.list_application_links(user["account_id"])}


@router.post("/links/{link_id}/revoke")
async def revoke_link(
    link_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    ok = await platform_db.set_application_link_active(user["account_id"], link_id, False)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"status": "revoked"}


class UpdateLinkRequest(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    source: str | None = Field(default=None, max_length=60)
    company_id: int | None = None
    expires_in_days: int | None = Field(default=None, ge=0, le=730)
    # Auto-remind policy for abandoned drafts on this link: cadence in hours
    # (0 = off) + the lifetime per-draft cap.  Cadence is restricted to the
    # offered presets so a typo can't configure hourly spam.
    remind_every_hours: int | None = None
    remind_max: int | None = Field(default=None, ge=1, le=3)


@router.patch("/links/{link_id:int}")
async def update_link(
    link_id: int,
    body: UpdateLinkRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Edit an existing link — only the fields present in the body change.
    ``company_id: null`` clears the carrier; ``expires_in_days`` resets the
    auto-close window from now (0/null → never)."""
    provided = body.model_fields_set
    kwargs: dict = {}
    if "label" in provided:
        kwargs["label"] = body.label or ""
    if "source" in provided:
        kwargs["source"] = body.source or ""
    if "company_id" in provided:
        if body.company_id is not None:
            co = await platform_db.get_company_in_account(user["account_id"], body.company_id)
            if not co:
                raise HTTPException(status_code=422, detail="Unknown company")
        kwargs["company_id"] = body.company_id
    if "expires_in_days" in provided:
        expires_at = None
        if body.expires_in_days and body.expires_in_days > 0:
            from datetime import datetime, timezone, timedelta
            expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat()
        kwargs["expires_at"] = expires_at
    if "remind_every_hours" in provided and body.remind_every_hours is not None:
        if body.remind_every_hours not in (0, 24, 48, 72, 168):
            raise HTTPException(status_code=422, detail="Invalid reminder cadence")
        kwargs["remind_every_hours"] = body.remind_every_hours
    if "remind_max" in provided and body.remind_max is not None:
        kwargs["remind_max"] = body.remind_max
    if not kwargs:
        return {"ok": True}
    ok = await platform_db.update_application_link(user["account_id"], link_id, **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"ok": True}


@router.delete("/links/{link_id:int}")
async def delete_link(
    link_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Permanently remove a link (e.g. to declutter revoked ones).  Submitted
    applications are kept — they're keyed by the token text, not a link FK."""
    ok = await platform_db.delete_application_link(user["account_id"], link_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"ok": True}


# ── Recruiter: applications ─────────────────────────────────────────


@router.get("")
async def list_applications(
    status: str = Query(default="", description="filter by status"),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    items = await platform_db.list_driver_applications(
        user["account_id"], status=status, limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/{app_id:int}")
async def get_application(
    app_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
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


# The status state machine + vetting rules live in service.py (domain
# logic); the routes below just enforce them.


@router.patch("/{app_id:int}/status")
async def set_status(
    app_id: int,
    body: StatusRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    if body.status not in service.VALID_STATUSES:
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
        if body.status not in service.STATUS_TRANSITIONS.get(current, frozenset()):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot move an application from '{current}' to '{body.status}'",
            )
        # Approval is gated on a completed vetting checklist — a candidate
        # can't be 'approved' (and therefore hired) until the required
        # FMCSA queries are recorded as run.
        if body.status == "approved":
            missing = service.missing_required_vetting(current_app)
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


class BulkStatusRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    status: str = Field(..., min_length=2, max_length=30)


@router.post("/bulk-status")
async def bulk_set_status(
    body: BulkStatusRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Move many applications at once (table triage).  Applies the SAME
    per-application rules as the single-status PATCH — illegal jumps, the
    'approved' vetting gate, and 'hired'-only-via-Hire are all enforced —
    and reports which moved vs. were skipped (with a reason).  Never
    bulk-hires."""
    if body.status not in service.VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status")
    if body.status == "hired":
        raise HTTPException(status_code=409, detail="Use the Hire action to convert an applicant")

    reviewer = None
    try:
        du = await get_current_db_user(user, platform_db)
        reviewer = du.id if du else None
    except Exception:
        pass

    acct = user["account_id"]
    updated: list[int] = []
    skipped: list[dict] = []
    for app_id in body.ids[:200]:   # bounded
        cur = await platform_db.get_driver_application(acct, app_id, decrypt_pii=False)
        if not cur:
            skipped.append({"id": app_id, "reason": "not found"})
            continue
        current = cur.get("status") or "submitted"
        if body.status == current:
            updated.append(app_id)
            continue
        if body.status not in service.STATUS_TRANSITIONS.get(current, frozenset()):
            skipped.append({"id": app_id, "reason": f"can't move from {current}"})
            continue
        if body.status == "approved" and service.missing_required_vetting(cur):
            skipped.append({"id": app_id, "reason": "needs vetting checks"})
            continue
        await platform_db.update_application_status(acct, app_id, body.status, reviewed_by=reviewer)
        updated.append(app_id)
    return {"updated": updated, "skipped": skipped}


class NotesRequest(BaseModel):
    notes: str = Field(default="", max_length=4000)


@router.patch("/{app_id:int}/notes")
async def set_notes(
    app_id: int,
    body: NotesRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    ok = await platform_db.set_application_notes(user["account_id"], app_id, body.notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"status": "saved"}


class VettingRequest(BaseModel):
    check: str = Field(..., min_length=2, max_length=30)
    done: bool = True


@router.patch("/{app_id:int}/vetting")
async def set_vetting(
    app_id: int,
    body: VettingRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    """Tick (or untick) one pre-hire check — PSP / MVR / Clearinghouse /
    drug / background.  Stamps who ran it + when.  Required checks gate
    the 'approved' transition (see ``set_status``)."""
    if body.check not in service.VETTING_CHECKS:
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
    return {"vetting": vetting, "required": list(service.REQUIRED_VETTING)}


@router.get("/{app_id:int}/packet.pdf")
async def download_application_packet(
    app_id: int,
    user: dict = Depends(require_permission("can_manage_applications")),
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

    # Hiring carrier (the sub-company the applicant applied to) — names the
    # DQ file's §391.51 carrier identity.  Falls back to the account name.
    carrier_name = carrier_mc = carrier_dot = ""
    if app.get("company_id"):
        try:
            co = await platform_db.get_company_in_account(user["account_id"], app["company_id"])
            if co:
                carrier_name = co.display_name or co.code
                carrier_mc, carrier_dot = co.mc_number, co.usdot_number
        except Exception:
            pass

    import datetime as _dt
    from features.applications.report import build_dq_packet_pdf
    try:
        buf = build_dq_packet_pdf(
            app, account_name=acct_name, signature_png=sig_png,
            generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            carrier_name=carrier_name, carrier_mc=carrier_mc, carrier_dot=carrier_dot,
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


@router.post("/{app_id:int}/convert")
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
        du = await get_current_db_user(user, platform_db)
        db_user_id = du.id if du else None
    except Exception:
        pass

    # Atomic claim: flip approved → hired CONDITIONALLY first.  Only one
    # concurrent convert wins; the loser gets 0 rows and a 409 — so a
    # double-click can't mint two driver invites for one applicant.
    claimed = await platform_db.update_application_status(
        user["account_id"], app_id, "hired", reviewed_by=db_user_id,
        expect_status="approved",
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="Applicant already being processed")

    # Now mint the invite.  If that fails, roll the claim back to 'approved'
    # so the applicant stays hireable (no stuck 'hired'-without-invite row).
    try:
        invite = await platform_db.create_invite(
            user["account_id"],
            created_by=db_user_id or 0,
            role=Role.DRIVER,
            hours=168,  # 7-day window for a new hire to onboard
            source_application_id=app_id,
        )
    except Exception:
        await platform_db.update_application_status(
            user["account_id"], app_id, "approved", reviewed_by=db_user_id,
        )
        logger.exception("convert: invite mint failed, rolled back app=%s", app_id)
        raise HTTPException(status_code=500, detail="Could not create the driver invite. Please try again.")
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


@router.get("/{app_id:int}/docs/{slot}")
async def get_application_doc(
    app_id: int,
    slot: str,
    user: dict = Depends(require_permission("can_manage_applications")),
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
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    acct = user["account_id"]
    items = await platform_db.list_application_notifications(acct, uid, unread_only=unread, limit=limit)
    unread_count = await platform_db.count_unread_application_notifications(acct, uid)
    return {"items": items, "unread_count": unread_count}


class MarkReadRequest(BaseModel):
    # None / omitted → mark ALL of the user's notifications read.
    ids: list[int] | None = None


@router.post("/notifications/read")
async def mark_my_notifications_read(
    body: MarkReadRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    marked = await platform_db.mark_application_notifications_read(
        user["account_id"], uid, ids=body.ids,
    )
    return {"marked": marked}


@router.get("/notify-prefs")
async def get_my_notify_prefs(
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    channels = await platform_db.get_application_notify_channels(uid)
    return {"channels": sorted(channels)}


class NotifyPrefsRequest(BaseModel):
    channels: list[str] = Field(default_factory=list)


@router.put("/notify-prefs")
async def set_my_notify_prefs(
    body: NotifyPrefsRequest,
    user: dict = Depends(require_permission("can_manage_applications")),
    platform_db=Depends(get_platform_db),
):
    uid = await _recipient_id(user, platform_db)
    channels = await platform_db.set_application_notify_channels(
        user["account_id"], uid, body.channels,
    )
    return {"channels": sorted(channels)}
