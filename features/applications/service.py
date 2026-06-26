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


# ── New-application notification fan-out ────────────────────────────


async def notify_new_application(
    platform_db, account_id: int, application_id: int, reference: str,
    applicant_name: str,
) -> None:
    """Alert every account user holding ``can_manage_applications`` that a
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

    async def _can_manage(role) -> bool:
        key = getattr(role, "value", role)
        if key not in perm_cache:
            try:
                fs = await get_account_permissions(role, account_id)
                perm_cache[key] = bool(getattr(fs, "can_manage_applications", False))
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
    review_url = f"{review_base_url()}/workforce/applications"
    title = "New driver application"
    body = f"{applicant_name or 'A driver'} applied · {reference}"

    for u in users:
        try:
            if not await _can_manage(u.role):
                continue
            channels = await platform_db.get_application_notify_channels(u.id)

            if "dashboard" in channels:
                try:
                    await platform_db.create_application_notification(
                        account_id, u.id, application_id=application_id,
                        reference=reference, title=title, body=body,
                    )
                except Exception as e:
                    logger.debug("in-app notif for user %s failed: %s", u.id, e)

            if "email" in channels and getattr(u, "email", None):
                try:
                    from capabilities.email.application_emails import send_new_application_email
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
