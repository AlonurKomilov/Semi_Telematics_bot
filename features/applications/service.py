"""Driver Applications — domain logic (pure; no HTTP layer).

The interface layer (``features/applications/router.py``) handles request
parsing, auth, and HTTP responses; this module holds the feature's domain
rules so the router stays thin.  Nothing here imports ``fastapi`` or
``interfaces.api`` — ``validate_application`` raises a plain ``ValueError``
that the router translates into a 422.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import secrets

logger = logging.getLogger("api.applications")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Defensive caps so a malicious payload can't be giant even within the
# body-size envelope.
MAX_STR = 500
MAX_TEXTAREA = 4000
MAX_ARRAY = 30

# Consents that authorize the FMCSA/FCRA background queries the carrier is
# legally required to obtain.  Keys mirror the public form's CONSENTS list.
# ``employment_verification`` is the 49 CFR §391.23 prior-employer records
# release (added 2026-06-22 with the full disclosure documents).
REQUIRED_CONSENTS = ("psp", "mvr", "clearinghouse", "fcra", "drug", "truthful",
                     "employment_verification")

# Every pre-hire check a recruiter can record.  ``REQUIRED_VETTING`` is the
# subset that must all be marked done before an applicant can be approved
# (the recruiter-side compliance gate); drug + background are optional.
VETTING_CHECKS = ("psp", "mvr", "clearinghouse", "drug", "background")
REQUIRED_VETTING = ("psp", "mvr", "clearinghouse")

# ── Application status state machine (the server is the authority) ───

# Every lifecycle state an application can hold.
VALID_STATUSES = frozenset({
    "submitted", "screening", "interview", "approved",
    "rejected", "withdrawn", "hired",
})

# Legal transitions for the manual status PATCH so the pipeline can't be
# jumped arbitrarily.  Two invariants matter most:
#   * 'hired' is NOT reachable here — it is set ONLY by the Hire action
#     (POST .../convert), which mints the driver invite.  Flipping to
#     'hired' via a plain status change would leave a "hired" record with
#     no driver.
#   * 'hired' is terminal — a real driver now exists, so they're managed in
#     Team Management, not by reverting the application.
# Forward + sideways moves among the active stages are allowed; a
# rejected/withdrawn candidate can be re-opened to 'screening'.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "submitted":  frozenset({"screening", "interview", "approved", "rejected", "withdrawn"}),
    "screening":  frozenset({"submitted", "interview", "approved", "rejected", "withdrawn"}),
    "interview":  frozenset({"screening", "approved", "rejected", "withdrawn"}),
    "approved":   frozenset({"screening", "interview", "rejected", "withdrawn"}),
    "rejected":   frozenset({"screening"}),
    "withdrawn":  frozenset({"screening"}),
    "hired":      frozenset(),
}


def cap_strings(obj):
    """Recursively trim strings to defensive lengths + strip NULs.

    Stored data is escaped by React on render, but we still cap length
    and strip control characters server-side so a hostile payload can't
    bloat the DB or smuggle NUL bytes.
    """
    if isinstance(obj, str):
        s = obj.replace("\x00", "")
        return s[:MAX_TEXTAREA]
    if isinstance(obj, list):
        return [cap_strings(x) for x in obj[:MAX_ARRAY]]
    if isinstance(obj, dict):
        return {k: cap_strings(v) for k, v in obj.items()}
    return obj


def validate_application(data: dict) -> None:
    """Server-side re-validation — never trust the client.

    Raises ``ValueError`` (message = the reason) on a structural /
    required-field failure; the router turns that into a 422.  Mirrors the
    form's client validators but is the authoritative gate.
    """
    if not isinstance(data, dict):
        raise ValueError("Malformed application")
    personal = data.get("personal")
    if not isinstance(personal, dict):
        raise ValueError("Missing personal section")
    for req in ("first", "last", "email"):
        v = personal.get(req)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Missing required field: {req}")
        if len(v) > MAX_STR:
            raise ValueError(f"Field too long: {req}")
    if not EMAIL_RE.match(personal["email"].strip()):
        raise ValueError("Invalid email")
    # Employment history must be a (bounded) list per §391.21.
    emp = data.get("employment")
    if emp is not None and (not isinstance(emp, list) or len(emp) > MAX_ARRAY):
        raise ValueError("Invalid employment history")
    # All legally load-bearing consents must be affirmatively given —
    # server-enforced (not just client-side) because the consents are the
    # legal basis for pulling a candidate's records; a direct API POST must
    # not be able to submit without them.
    consents = data.get("consents") or {}
    missing = [k for k in REQUIRED_CONSENTS if not consents.get(k)]
    if missing:
        raise ValueError(f"Required consent(s) not accepted: {', '.join(missing)}")


# A drawn signature is a base64 PNG, routinely 30-60 KB — three to
# fifteen times MAX_TEXTAREA.  It gets its own cap, at image scale, and
# is detached before ``cap_strings`` ever sees it (below).  The real gate
# is not length anyway: it is the magic-byte sniff + byte cap that every
# other uploaded image goes through.
MAX_SIG_DATA_URL = 3_000_000      # base64 chars (~2.2 MB decoded)
MAX_SIG_BYTES = 2 * 1024 * 1024   # decoded PNG


def pop_signature_data_url(data: dict) -> str:
    """Detach ``consents.sigDataUrl`` BEFORE the text caps run.

    ``cap_strings`` trims every string to MAX_TEXTAREA.  Applied to a
    data URL that chopped it mid-base64: 4,000 - len("data:image/png;
    base64,") = 3,978 characters, which is never a multiple of 4, so
    ``decode_data_url`` raised and returned None for EVERY drawn
    signature.  ``sigMode``/``sigDate`` are short and survived, so the
    record kept saying "signed, draw mode" while the image was gone —
    no error to the applicant, no log, and a §391.51 packet printing a
    blank signature rule.
    """
    consents = data.get("consents")
    if not isinstance(consents, dict):
        return ""
    raw = consents.pop("sigDataUrl", "")
    return raw[:MAX_SIG_DATA_URL] if isinstance(raw, str) else ""


def signature_bytes(consents: dict, sig_data_url: str) -> bytes | None:
    """The drawn signature's PNG bytes, or None when it was typed.

    Raises ``ValueError`` (→ 422) rather than letting a signature go
    missing.  Same reasoning the required-consents check states: the
    consents are the legal basis for pulling a candidate's records, and
    the signature is what makes them attributable, so a direct API POST
    must not be able to submit without one either.
    """
    from infra.file_safety import validate_upload

    consents = consents or {}
    if (consents.get("sigMode") or "type") != "draw":
        if not str(consents.get("sigName") or "").strip():
            raise ValueError("A signature is required")
        return None
    if not sig_data_url:
        raise ValueError("A drawn signature is required")
    raw = decode_data_url(sig_data_url)
    ok, mime, _ = validate_upload(raw or b"", max_bytes=MAX_SIG_BYTES)
    if not ok or mime != "image/png" or not raw:
        raise ValueError("Your signature could not be read \u2014 please draw it again")
    return raw


def gen_reference() -> str:
    return "APP-" + secrets.token_hex(3).upper()


def decode_data_url(data_url: str) -> bytes | None:
    """Decode a ``data:...;base64,XXXX`` URL → bytes (the signature canvas)."""
    try:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        return base64.b64decode(data_url, validate=True)
    except Exception:
        return None


def missing_required_vetting(app: dict) -> list[str]:
    vetting = app.get("vetting") or {}
    return [c for c in REQUIRED_VETTING if not (vetting.get(c) or {}).get("done")]


def review_base_url() -> str:
    """Base origin for the recruiter review link in notifications."""
    return (
        os.getenv("DASHBOARD_BASE_URL")
        or os.getenv("AUTH_BASE_URL")
        or "https://dash.4truck.us"
    ).rstrip("/")


def verification_targets(app: dict) -> list[dict]:
    """Prior employers §391.23 requires investigating.

    FMCSA-regulated employers whose employment overlaps the 3 years before
    now.  ``employer_index`` (the position in the stored employment list) is
    the stable key verification rows attach to.  Unparseable end dates are
    kept — over-including is the safe failure for a compliance list.
    """
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=3 * 365 + 1)
    out: list[dict] = []
    for i, j in enumerate(app.get("employment") or []):
        if not isinstance(j, dict):
            continue
        if str(j.get("fmcsa") or "").lower() != "yes":
            continue
        if not j.get("current"):
            try:
                to_dt = datetime.strptime(str(j.get("to") or "")[:7], "%Y-%m")
                if to_dt < cutoff:
                    continue
            except ValueError:
                pass
        out.append({
            "employer_index": i,
            "company": j.get("company") or "",
            "city": j.get("city") or "", "state": j.get("state") or "",
            "phone": j.get("phone") or "",
            "from": j.get("from") or "", "to": j.get("to") or "",
            "current": bool(j.get("current")),
            "position": j.get("position") or "",
            "contact_ok": j.get("contactOk") or "",
            "usdot": j.get("usdot") or "",
            "mc": j.get("mc") or "",
            # Registry contact email captured at pick time — prefills (never
            # locks) the recruiter's request address.
            "employer_email": j.get("employerEmail") or "",
        })
    return out


def apply_base_url() -> str:
    """Base origin of the PUBLIC apply form (the resume-link email target)."""
    base = os.getenv("APPLY_BASE_URL")
    if base:
        return base.rstrip("/")
    # Default deploys serve the form on apply.<apex> alongside dash.<apex>.
    return review_base_url().replace("://dash.", "://apply.")


# ── New-application notification fan-out ────────────────────────────


async def notify_new_application(
    platform_db, account_id: int, application_id: int, reference: str,
    applicant_name: str,
) -> None:
    """Tell every account user holding ``can_manage_applications`` that a
    new application landed.

    Targeting is by the PERMISSION (the SSOT, via ``get_account_permissions``
    so per-account overrides are honoured), never a hardcoded role.

    DELIVERY is the notifications capability's job, not this feature's: one
    ``notify_user`` call names the three personal channels and the capability
    decides what actually goes out — connection state, the category mute, the
    master switch, quiet hours, the delivery ledger.  Recruiting used to mail
    and DM people itself, which meant none of that applied to it and its
    emails carried no unsubscribe.  A one-time backfill
    (``migrate_seed_application_notification_channels``) seeded the
    connections its existing audience already had reach through, so the move
    keeps everyone it was reaching.

    Runs as a background task — wholly best-effort; a notice must never
    affect the submission that triggered it.
    """
    try:
        from capabilities.permissions.roles import get_user_permissions
        users = await platform_db.list_account_users(account_id)
    except Exception as e:
        logger.debug("notify_new_application: setup failed: %s", e)
        return

    # Resolve effective perms once per (role, tier) — get_USER_permissions,
    # not get_account_permissions: HR holds can_manage_applications only
    # through its senior tier, so resolving the base role alone silently
    # skipped every HR team lead who reviews applications.
    perm_cache: dict = {}

    async def _can_manage(role, is_manager: bool) -> bool:
        key = (getattr(role, "value", role), bool(is_manager))
        if key not in perm_cache:
            try:
                fs = await get_user_permissions(
                    role, account_id, is_manager=bool(is_manager))
                perm_cache[key] = bool(getattr(fs, "can_manage_applications", False))
            except Exception:
                perm_cache[key] = False
        return perm_cache[key]

    review_url = f"{review_base_url()}/workforce/applications"
    # The title is also the EMAIL SUBJECT.  Without the applicant in it,
    # a recruiter's inbox fills with a dozen identical subject lines and
    # triage has to happen by opening each one.
    title = f"New driver application — {applicant_name}" if applicant_name \
        else "New driver application"

    for u in users:
        try:
            if not await _can_manage(u.role, getattr(u, "is_manager", False)):
                continue
            from capabilities.notifications import (
                NotificationContent, notify_user,
            )
            from features.applications.notifications import APPLICATION_RECEIVED
            await notify_user(
                platform_db, account_id, u.id,
                NotificationContent(
                    # One semantic body, rendered three ways.  It carries
                    # the reference because that is the key a recruiter
                    # searches and an applicant quotes — and only the
                    # in-app row could show it as an object chip, so a
                    # chip-only reference was invisible in mail and chat.
                    title=title,
                    body=f"Reference {reference}",
                    category=APPLICATION_RECEIVED,
                    url=f"{review_url}?app={application_id}",
                    meta={"application_id": application_id,
                          "reference": reference,
                          # The call-to-action, declared once: the inbox
                          # row draws it as an inline button, email as a
                          # real button.  Relative by contract — the
                          # renderers absolutise it, and a stored notice
                          # must never carry an off-site redirect.
                          "action": {
                              "label": "Review application",
                              "url": f"/workforce/applications?app={application_id}",
                          }},
                ),
                # Every personal channel is offered; the capability
                # delivers only where this person is connected and hasn't
                # muted the category.  There is no feature-side channel
                # gate any more — that preference lives in the
                # notification matrix, one store for all of them.
                channels=["in_app", "email", "telegram_dm"],
                correlation_key=f"application:{application_id}:{u.id}",
            )
        except Exception as e:
            logger.warning(
                "application %s: notice failed for user %s: %s",
                application_id, getattr(u, "id", "?"), e)
