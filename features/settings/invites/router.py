"""Settings · Invites — create / resend / revoke / extend account invitations.

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
from capabilities.permissions.roles import (
    validate_role_change, role_rank, ASSIGNABLE_ROLES_PATTERN,
)
from .service import _INVITE_EMAIL_RE, _invite_email_rate_check, invite_authorized

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["settings"])


# ── Invites ───────────────────────────────────────────────────

# Plain RFC-5321-shaped regex — sufficient for transactional email
# validation without dragging the ``email-validator`` package into
# requirements.txt.  Matches the existing convention at
# interfaces/api/auth.py:_EMAIL_RE (single source of truth would be
# nice; deferred to a tidy-up pass).
class InviteCreate(BaseModel):
    role: str = Field("fleet", pattern=ASSIGNABLE_ROLES_PATTERN)
    truck_num: Optional[str] = None
    hours: int = Field(24, ge=1, le=720)
    # Email-channel: when present, the server generates the invite
    # AND attempts to send it via SMTP in the same request.  When
    # absent, behaviour is unchanged (link-channel only).  Plain
    # string with regex validation in the route handler (not pydantic
    # EmailStr — that would require the email-validator package which
    # is NOT in requirements.txt and would crash app boot).
    recipient_email: Optional[str] = None




@router.get("/invite/check-recipient")
async def check_invite_recipient(
    email: str,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
):
    """Pre-create duplicate-recipient check.

    The dashboard calls this (debounced) as the operator types in
    the Email-channel recipient input.  When the email matches an
    active user in the SAME account, returns the matched user's
    display name + role so the operator gets an inline warning
    before submitting ("alice@example.com is already a member —
    Alice Smith, Driver").

    Scoped strictly to the operator's account — does NOT reveal
    cross-account existence (tenant isolation: the existence of
    alice@example.com at a competing fleet is not the operator's
    business to know).

    Rate-limited modestly (10/min per actor) to prevent the
    endpoint from becoming an in-account email-existence oracle
    via brute-force enumeration.  The recipient_email path of
    invite-create already runs ``_invite_email_rate_check`` at
    5/min so even a successful enumeration is capped at the same
    rate.
    """
    from adapters.cache.redis import rate_limit_check, is_available as _redis_ok
    if _redis_ok():
        ok = await rate_limit_check(
            f"check_recipient:{user['account_id']}:{user['sub']}",
            window_secs=60, max_requests=10,
        )
        if not ok:
            raise HTTPException(status_code=429, detail="Too many lookups")
    norm = (email or "").strip().lower()
    if not norm or not _INVITE_EMAIL_RE.match(norm):
        # Don't validate format here — the dashboard input has its
        # own format check.  Just return "no match" for anything
        # that wouldn't pass the create-time validator either.
        return {"exists": False}
    matched = await platform_db.get_user_by_email_in_account(
        norm, user["account_id"],
    )
    if not matched:
        return {"exists": False}
    return {
        "exists": True,
        "display_name": matched.display_name or "",
        "role": matched.role.value if hasattr(matched.role, "value") else str(matched.role),
    }


@router.post("/invite")
async def create_invite(
    body: InviteCreate,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Generate a new invite code; optionally send it via email."""
    # Invite-target authorization: a manager's sub-team whitelist (rank-
    # independent) OR the standard rank gate — see invites.service.
    ok, reason = invite_authorized(
        user["role"], bool(user.get("is_manager")), body.role,
    )
    if not ok:
        if reason.startswith("manager_invite_restricted:"):
            allowed = reason.split(":", 1)[1].replace(",", ", ")
            raise HTTPException(
                status_code=403,
                detail=f"Your manager role may only invite: {allowed}",
            )
        raise HTTPException(
            status_code=403,
            detail="Cannot create invite for role equal to or above your own",
        )

    # Resolve DB user.id from telegram_id (JWT sub) — FK requires users.id
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # ── Email-channel pre-flight checks ──────────────────────────
    # All checks run BEFORE create_invite so a 503/422/429 doesn't
    # leave an orphan link-channel row no one will use.  When the
    # operator chose email and pre-flight refuses, the invite isn't
    # created — they get a clean error instead of "created but..."
    recipient = (body.recipient_email or "").strip().lower() or None
    if recipient:
        # 1. Format validation
        if not _INVITE_EMAIL_RE.match(recipient):
            raise HTTPException(status_code=422, detail="Invalid recipient email")
        if "," in recipient or ";" in recipient:
            raise HTTPException(
                status_code=422,
                detail="One recipient per invite — create separate invites for each person",
            )
        # 2. SMTP must be configured.  Refuse 503 BEFORE create so
        #    we don't ship "sent_to_email NOT NULL + email_sent_at
        #    NULL" rows that lie about being email-channel.
        from capabilities.email.smtp import is_email_configured
        from capabilities.email.resend_transport import is_resend_api_enabled
        # Either provider satisfies the gate.  Resend-only deploys
        # (no SMTP relay) used to hit a misleading 503 about SMTP
        # being unconfigured; the OR honours both providers and the
        # error message names both paths so the operator can
        # self-diagnose.
        if not is_email_configured() and not is_resend_api_enabled():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Email channel not configured — set MAIL_PROVIDER=resend + "
                    "RESEND_API_KEY (preferred, bounce-tracked) or SMTP_HOST + SMTP_FROM"
                ),
            )
        # 3. Rate limit — fail CLOSED on Redis outage.  Passes
        #    recipient so the per-recipient buckets engage.
        if not await _invite_email_rate_check(
            user["account_id"], user["sub"], recipient=recipient,
        ):
            raise HTTPException(
                status_code=429,
                detail="Too many invite emails — wait a moment and try again",
            )

    invite = await platform_db.create_invite(
        account_id=user["account_id"],
        created_by=db_user.id,
        role=Role(body.role),
        truck_num=body.truck_num,
        hours=body.hours,
        recipient_email=recipient,
    )

    # Email send happens AFTER the invite row commits — if SMTP
    # hands off successfully we mark email_sent_at; if it fails the
    # invite still exists with sent_to_email populated + null
    # timestamp, and the operator sees "created but email failed"
    # in the response.  The link in their clipboard works either
    # way (fallback path).
    email_status = None
    if recipient:
        try:
            from capabilities.email.auth_emails import send_invite_email_async
            account = await platform_db.get_account(user["account_id"])
            account_name = (account.name if account else "your team") or "your team"
            sent, resend_email_id = await send_invite_email_async(
                to=recipient,
                code=invite.code,
                account_id=user["account_id"],
                invite_id=invite.id,
                account_name=account_name,
                role_label=body.role.capitalize(),
                inviter_display_name=db_user.display_name or "Your inviter",
                expires_at=invite.expires_at,
                truck_num=body.truck_num,
            )
        except Exception as e:
            logger.warning("send_invite_email raised: %s", e, exc_info=True)
            sent = False
            resend_email_id = None
        if sent:
            updated = await platform_db.mark_invite_email_sent(
                user["account_id"], invite.id,
            )
            if updated is not None:
                invite = updated
            # Persist the Resend per-send identifier so the bounce
            # webhook handler can match events back to this invite
            # without trusting recipient-address (cross-account
            # hijack vector — see design vet).  SMTP-routed sends
            # have resend_email_id=None; bounce webhooks for SMTP
            # sends never reach us anyway.
            if resend_email_id:
                await platform_db.set_invite_resend_email_id(
                    user["account_id"], invite.id, resend_email_id,
                )
                invite.resend_email_id = resend_email_id
            email_status = "sent"
        else:
            email_status = "queued_failed"  # invite exists, email didn't

    # Audit log — single row per create.  Recipient stays out of
    # the free-text ``details`` field (the audit-log details column
    # has no length cap and recipient emails can be 254 chars +
    # operator-supplied wrapper text); we use a short marker and
    # rely on the invite row's sent_to_email for the actual address.
    detail_extras = ""
    if recipient:
        # Cross-domain heuristic: flag (warn-but-allow) when the
        # recipient's domain doesn't match the inviter's own email
        # domain.  Doesn't BLOCK — fleet onboarding legitimately
        # sends to drivers at various employer / personal addresses
        # — but does flag in the audit log so an operator (or an
        # ops sweep query) can spot a HR-tier user blasting
        # 4truck-branded mail to unrelated external domains.  This
        # is the cheap version of the design's full recipient-
        # domain allowlist (deferred — needs per-account config).
        recipient_domain = recipient.rpartition("@")[-1]
        inviter_email_domain = (
            (db_user.email or "").lower().rpartition("@")[-1]
            if getattr(db_user, "email", None)
            else ""
        )
        cross_domain_marker = ""
        if (
            recipient_domain
            and inviter_email_domain
            and recipient_domain != inviter_email_domain
        ):
            cross_domain_marker = ", cross_domain: true"
        detail_extras = (
            f", channel: email"
            f", email_status: {email_status}"
            f"{cross_domain_marker}"
        )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_create",
        target_type="invite", target_id=str(invite.id),
        details=(
            f"Role: {body.role}{detail_extras}"
        )[:500],  # hard-cap defends against future operator-supplied text
    )
    return {
        "id": invite.id,
        "code": invite.code,
        "role": invite.role,
        "truck_num": invite.truck_num,
        "expires_at": invite.expires_at,
        "channel": invite.channel,
        "email_status": email_status,
        "sent_to_email": invite.sent_to_email,
    }


class InviteResendEmail(BaseModel):
    pass  # body intentionally empty for v1 — resend reuses original recipient


@router.post("/invites/{invite_id}/resend-email")
async def resend_invite_email_endpoint(
    invite_id: int,
    _body: InviteResendEmail,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Resend the invite-email to the same recipient.

    Refuses on used / revoked (uniform 404) and on expired
    (409 — extend it first, then resend).  Reuses the original
    recipient — no new-recipient flow in v1 because that changes
    who can redeem and needs its own audit shape.
    """
    # Pre-flight order: SMTP check is cheap; load invite next so the
    # recipient address is known BEFORE the rate-check (per-recipient
    # bucket needs it).  This re-orders from the earlier shape where
    # rate-check ran first — the design vet flagged this so the
    # per-recipient bucket can engage on resend too.
    from capabilities.email.smtp import is_email_configured
    if not is_email_configured():
        raise HTTPException(
            status_code=503,
            detail="Email channel not configured on this deployment",
        )

    invite = await platform_db.get_invite_by_id(user["account_id"], invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.is_used or invite.is_revoked:
        # Uniform 404 — same response as truly-missing, no
        # side-channel for which branch.
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.is_expired:
        # 409: the operator needs to extend first.  Distinguished
        # status code lets the dashboard render a one-button
        # "Extend + resend" flow.
        raise HTTPException(
            status_code=409,
            detail="Invite expired — extend it first, then resend",
        )
    if not invite.sent_to_email:
        # The invite was issued as link-channel.  No address on file.
        # v1 doesn't support upgrading link → email after the fact
        # (would need a new-recipient input + its own audit shape);
        # operator can revoke + recreate with email channel.
        raise HTTPException(
            status_code=409,
            detail="This invite was not issued via email — revoke and recreate with the email channel",
        )
    if invite.is_bounced:
        # Resending to a bounced address just bounces again.  The
        # operator must revoke + recreate with a corrected address —
        # the dashboard surfaces a "Revoke & recreate" affordance.
        raise HTTPException(
            status_code=409,
            detail="Recipient bounced — revoke and recreate with a corrected address",
        )

    # Per-recipient rate-check fires AFTER the invite load so the
    # recipient hash is available.
    if not await _invite_email_rate_check(
        user["account_id"], user["sub"], recipient=invite.sent_to_email,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many invite emails — wait a moment and try again",
        )

    # Rank check mirrors revoke/extend — HR can't keep an
    # Admin-tier invite alive past the Owner's intent.
    caller_rank = role_rank(user["role"])
    invite_rank = role_rank(invite.role)
    if invite_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot resend an invite for a role equal to or above your own",
        )

    db_user = await get_current_db_user(user, platform_db)
    account = await platform_db.get_account(user["account_id"])
    account_name = (account.name if account else "your team") or "your team"
    try:
        from capabilities.email.auth_emails import send_invite_email_async
        sent, resend_email_id = await send_invite_email_async(
            to=invite.sent_to_email,
            code=invite.code,
            account_id=user["account_id"],
            invite_id=invite.id,
            account_name=account_name,
            role_label=invite.role.capitalize(),
            inviter_display_name=(
                (db_user.display_name if db_user else "Your inviter")
                or "Your inviter"
            ),
            expires_at=invite.expires_at,
            truck_num=invite.truck_num,
        )
    except Exception as e:
        logger.warning("send_invite_email raised on resend: %s", e, exc_info=True)
        sent = False
        resend_email_id = None

    if sent:
        updated = await platform_db.mark_invite_email_sent(
            user["account_id"], invite_id,
        )
        if updated is not None:
            invite = updated
        # Each resend gets a fresh Resend email_id (it's per-send,
        # not per-invite).  Overwrite — the most recent send is the
        # one the operator wants tracked for bounce events.
        if resend_email_id:
            await platform_db.set_invite_resend_email_id(
                user["account_id"], invite_id, resend_email_id,
            )
            invite.resend_email_id = resend_email_id
        outcome = "sent"
    else:
        outcome = "queued_failed"

    # Audit row uses ``invite_email_resent`` action so the audit-log
    # viewer renders "Invite email resent" (label added below).
    # ``attempt`` counter = the new email_send_count value, useful
    # forensic signal ("they resent this 4 times").
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_email_resent",
        target_type="invite", target_id=str(invite_id),
        details=(
            f"Role: {invite.role}, "
            f"attempt: {invite.email_send_count}, outcome: {outcome}"
        )[:500],
    )
    if not sent:
        # The send failed but the audit row is written so the
        # operator can see WHY.  Surface a 502 so the dashboard
        # toasts an error, not a green success.
        raise HTTPException(
            status_code=502,
            detail="Email send failed — link is still valid; try again later",
        )
    return {
        "ok": True,
        "email_send_count": invite.email_send_count,
        "email_sent_at": invite.email_sent_at,
    }


@router.get("/invites")
async def list_invites(
    pending_only: bool = Query(True),
    include_revoked: bool = Query(False),
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
):
    """List invite codes for the account.

    Two orthogonal filters:
      - ``pending_only`` (default True) — hide USED rows.
      - ``include_revoked`` (default False) — keep revoked rows hidden
        unless the operator explicitly asks ("Show all" toggle on the
        dashboard drives both flags).
    """
    invites = await platform_db.list_invites(
        user["account_id"],
        pending_only=pending_only,
        include_revoked=include_revoked,
    )
    return {
        "invites": [
            {
                "id": inv.id,
                "code": inv.code,
                "role": inv.role,
                "truck_num": inv.truck_num,
                "expires_at": inv.expires_at,
                "used_by": inv.used_by,
                "is_used": inv.is_used,
                "is_expired": inv.is_expired,
                "is_revoked": inv.is_revoked,
                "revoked_at": inv.revoked_at,
                "created_by": inv.created_by,
                # Email-channel fields (migration 090).  Optional in
                # the InviteInfo TS type for deploy-lag tolerance.
                "channel": inv.channel,
                "sent_to_email": inv.sent_to_email,
                "email_sent_at": inv.email_sent_at,
                "email_send_count": inv.email_send_count,
                # Bounce / complaint fields (migration 097).
                "email_bounced_at": inv.email_bounced_at,
                "email_bounce_type": inv.email_bounce_type,
                "email_bounce_reason": inv.email_bounce_reason,
                "email_soft_bounce_count": inv.email_soft_bounce_count,
                "email_complained_at": inv.email_complained_at,
            }
            for inv in invites
        ],
        "count": len(invites),
    }


@router.delete("/invites/{invite_id}")
async def revoke_invite_endpoint(
    invite_id: int,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Revoke (soft-delete) an unused invite.

    Same permission gate as create (``can_invite``); the caller-vs-
    invite rank check mirrors the rule on ``create_invite`` so an HR
    user (rank 0, can_invite=True) can't undo an Owner's onboarding of
    an Admin by revoking it.  Caller's rank MUST be strictly greater
    than the invited role's rank.

    Rate-limited at 30 revokes per actor per minute — generous for
    legitimate flush-out flows (e.g. an Owner deletes all old test
    invites), tight enough to cap an attacker's audit-log amplification
    if a token is ever leaked.

    Audit log captures role/created_by from the row BEFORE
    the UPDATE so an auditor reading the log after a revoke can
    reconstruct what was killed even when the operator's default view
    (revoked rows hidden) doesn't show it.
    """
    # Rate limit first — cheap reject for any flood.  Keyed by
    # (account_id, actor_telegram_id) so two different admins on the
    # same account get their own budget.  Revoke + extend SHARE the
    # bucket (key prefix ``invite_mutate``) because they're sibling
    # write-paths against the same row set, and the audit-log-
    # amplification cap that motivated the rate limit applies to
    # both endpoints equally — keeping disjoint keys would silently
    # double the cap a compromised admin token can flood with.
    from adapters.cache.redis import rate_limit_check
    rl_key = f"invite_mutate:{user['account_id']}:{user['sub']}"
    if not await rate_limit_check(rl_key, window_secs=60, max_requests=30):
        raise HTTPException(
            status_code=429,
            detail="Too many invite changes — wait a moment and try again",
        )

    # Fetch the row before the UPDATE so we have role/
    # created_by available for the audit log details — and so we can
    # rank-check the invite's role against the caller's role.  Returns
    # None if the row doesn't exist OR belongs to a different account
    # (cross-account scoping is enforced inside the SELECT WHERE).
    invite = await platform_db.get_invite_by_id(user["account_id"], invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    # Rank check — mirror create_invite (admin.py around line 529-532).
    # An HR / lower-ranked actor must not be able to revoke an invite
    # whose role outranks them.  Same comparison as create.
    caller_rank = role_rank(user["role"])
    invite_rank = role_rank(invite.role)
    if invite_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot revoke an invite for a role equal to or above your own",
        )

    revoked = await platform_db.revoke_invite(user["account_id"], invite_id)
    if not revoked:
        # The row existed at the SELECT above but the UPDATE matched 0
        # rows — either a concurrent redeem won the race, or the row
        # was already revoked by another tab.  Either way, treat as
        # "nothing to revoke" with the same 404 so we don't leak which
        # branch (the side-channel that would distinguish them is
        # operationally useless and confusing in the audit log).
        raise HTTPException(status_code=404, detail="Invite not found")

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_revoke",
        target_type="invite", target_id=str(invite_id),
        details=f"Role: {revoked.role}, created_by: {revoked.created_by}",
    )
    return {"ok": True, "revoked_at": revoked.revoked_at}


class InviteExtend(BaseModel):
    hours: int = Field(24, ge=1, le=720)


@router.post("/invites/{invite_id}/extend")
async def extend_invite_endpoint(
    invite_id: int,
    body: InviteExtend,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Push an unused invite's expiry forward without changing the
    code.  Operator flow this serves: "the invite I sent last week
    expired before they clicked — give it another 24 hours."

    Sibling of revoke, same guards (can_invite gate + rank check +
    rate limit + 404 uniformity).  Keeping the SAME code is the
    whole point — any copy of the original link in chat history
    works again, no re-share needed.

    Refuses to extend revoked invites: that would silently un-revoke
    an operator's prior kill decision.  If the operator really wants
    to bring a revoked code back, they create a new one (and reading
    the audit log makes the intent explicit).
    """
    from adapters.cache.redis import rate_limit_check
    # Shared bucket with revoke — see ``revoke_invite_endpoint``
    # rate-limit docstring for the audit-log-amplification rationale.
    rl_key = f"invite_mutate:{user['account_id']}:{user['sub']}"
    if not await rate_limit_check(rl_key, window_secs=60, max_requests=30):
        raise HTTPException(
            status_code=429,
            detail="Too many invite changes — wait a moment and try again",
        )

    invite = await platform_db.get_invite_by_id(user["account_id"], invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    # Same rank gate as revoke — HR (rank 0, can_invite=True) must
    # not be able to keep an Admin-tier invite alive past the deadline
    # the Owner set when issuing it.
    caller_rank = role_rank(user["role"])
    invite_rank = role_rank(invite.role)
    if invite_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot extend an invite for a role equal to or above your own",
        )

    extended = await platform_db.extend_invite(
        user["account_id"], invite_id, hours=body.hours,
    )
    if not extended:
        # Used / revoked / race-lost — uniform 404 like revoke, no
        # side-channel for which branch.
        raise HTTPException(status_code=404, detail="Invite not found")

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_extend",
        target_type="invite", target_id=str(invite_id),
        # created_by mirrors revoke's audit details — keeps the
        # forensic question "who created this and who keeps extending
        # it?" answerable from the audit log alone.
        details=f"Role: {extended.role}, created_by: {extended.created_by}, hours: {body.hours}, new_expires_at: {extended.expires_at}",
    )
    return {"ok": True, "expires_at": extended.expires_at}
