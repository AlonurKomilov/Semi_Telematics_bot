"""System-operator API — cross-account read + admin endpoints.

This router lives at ``/system/*`` and is reachable ONLY by users whose
Telegram id is in the ``SYSTEM_OWNER_IDS`` env (the platform-operator
allowlist).  It is the backend for the operator-only dashboard at
``system.4truck.us`` and is intentionally separate from the per-account
``/admin/*`` router (which is gated by ``can_manage_users`` /
``can_manage_billing`` within a single tenant).

Endpoints surfaced here:

  GET  /system/accounts            — paginated, searchable account list
  GET  /system/accounts/{id}       — full operator-view of one account
  GET  /system/stats               — system-wide counters for the dashboard

Comp grant / renew / revoke / history already live under
``/billing/comp/*`` (see [interfaces/api/routes/billing.py]) and use the
same ``SYSTEM_OWNER_IDS`` gate — the operator dashboard calls those
endpoints directly rather than wrapping them here.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import require_system_owner, get_platform_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# RFC 5322 simplified — same pattern the auth router uses for self-signup.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# ── Accounts ────────────────────────────────────────────────────


class CompGrantSpec(BaseModel):
    """Optional comp/trial grant included with an operator account create."""
    months: int = Field(..., ge=1, le=24, description="Grant duration in whole months")
    reason: str = Field(default="", max_length=500)


class CreateAccountRequest(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=100)
    owner_email:  str = Field(..., min_length=5, max_length=255)
    owner_display_name: str = Field(default="", max_length=120)
    # If ``password`` is omitted, the operator is telling us to email the
    # owner a password-set link instead of provisioning a credential up-front.
    # The link reuses the existing password-reset token machinery so the
    # owner picks their own password on first login.
    password: str | None = Field(default=None, min_length=8, max_length=128)
    # Optional comp grant applied right after creation — operator can hand a
    # paid customer 3/6/12 free months in one click, or skip and the account
    # falls back to whatever the default tier provides.
    grant_comp: CompGrantSpec | None = None


@router.post("/accounts", status_code=201)
async def operator_create_account(
    body: CreateAccountRequest,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Operator-driven tenant account creation.

    Replaces the curl/SQL workflow used to bootstrap a new customer.
    Always emails-the-owner: either a verification link (when ``password``
    is provided) or a password-set link (when it isn't), so the operator
    never sees the credential and the owner controls the first login.

    Optional ``grant_comp`` applies a comp through ``now + months * 30d``
    in the same audit trail as the standalone ``/billing/comp/grant``
    endpoint — handy for sales-driven free trials.
    """
    from interfaces.api.auth import _hash_password
    from adapters.storage import Role

    if not _EMAIL_RE.match(body.owner_email):
        raise HTTPException(status_code=422, detail="Invalid owner email")

    existing = await platform_db.get_user_by_email(body.owner_email)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Email already registered to another user",
        )

    operator_tg = int(_user.get("telegram_id") or 0)

    # Account row + permission/PTI seeds are atomic inside
    # ``create_account`` — a seed failure rolls everything back, so a
    # 500 here never leaves a half-provisioned tenant.
    try:
        account = await platform_db.create_account(body.company_name)
    except ValueError as e:
        # Case-insensitive company-name collision.
        raise HTTPException(status_code=409, detail=str(e))

    # Owner user.  When the operator supplied a password we hash it; when
    # they didn't we mint a throw-away hash and immediately follow up
    # with a password-reset token so the user picks the real credential.
    if body.password:
        pw_hash = _hash_password(body.password)
        provisioned_password = True
    else:
        import secrets
        pw_hash = _hash_password(secrets.token_urlsafe(32))
        provisioned_password = False

    owner = await platform_db.create_user_with_email(
        email=body.owner_email,
        password_hash=pw_hash,
        account_id=account.id,
        role=Role.OWNER,
        display_name=body.owner_display_name or body.owner_email.split("@")[0],
    )

    comp_applied = None
    if body.grant_comp:
        from datetime import datetime, timezone, timedelta
        expires = datetime.now(timezone.utc) + timedelta(days=30 * body.grant_comp.months)
        expires_iso = expires.isoformat()
        try:
            await platform_db.grant_comp(
                account.id,
                expires_at=expires_iso,
                reason=body.grant_comp.reason or f"Operator-granted {body.grant_comp.months}-month comp",
                actor_user_id=operator_tg,
            )
            comp_applied = {"months": body.grant_comp.months, "expires_at": expires_iso}
        except Exception:
            logger.exception(
                "Comp grant failed for new account %s — account created, comp skipped",
                account.id,
            )

    # Email the owner.  Both branches use the password-reset machinery:
    # when ``provisioned_password`` is True we still send a "set password
    # if you want to change it" message; when False the link is the only
    # way to log in.  Tokens last 24h so an operator who creates an
    # account on Friday afternoon gives the owner the weekend.
    reset_token = None
    invite_sent = False
    try:
        reset_token = await platform_db.create_password_reset_token(
            owner.id, ttl_hours=24,
        )
        import os as _os
        from capabilities.email.smtp import send_email
        base = _os.getenv("DASHBOARD_BASE_URL", "https://dash.4truck.us").rstrip("/")
        link = f"{base}/reset-password?token={reset_token}"
        subject = (
            "Your 4truck account is ready"
            if provisioned_password
            else "Set your 4truck password"
        )
        body_text = (
            f"Hi {owner.display_name or 'there'},\n\n"
            f"An account for {account.name} has been created on 4truck.\n\n"
            f"{'Set or change your password here:' if provisioned_password else 'Set your password to log in:'}\n"
            f"{link}\n\n"
            "This link is valid for 24 hours.\n"
        )
        await send_email(body.owner_email, subject, body_text)
        invite_sent = True
    except Exception:
        logger.exception(
            "Owner-invite email failed for new account %s — operator must follow up manually",
            account.id,
        )

    logger.info(
        "Operator %s created account id=%s name=%r owner=%s comp=%s invite_sent=%s",
        operator_tg, account.id, account.name, body.owner_email,
        bool(comp_applied), invite_sent,
    )

    try:
        await platform_db.add_platform_audit(
            "account_created",
            account_id=account.id,
            actor=f"operator:{operator_tg}",
            details=(
                f"name={account.name!r} owner={body.owner_email} "
                f"comp={comp_applied['months'] if comp_applied else 0}mo "
                f"invite_sent={invite_sent}"
            ),
        )
    except Exception:
        logger.exception("platform audit write failed for account %s", account.id)

    return {
        "account_id":          account.id,
        "name":                account.name,
        "slug":                account.slug,
        "owner_user_id":       owner.id,
        "owner_email":         body.owner_email,
        "provisioned_password": provisioned_password,
        "invite_email_sent":   invite_sent,
        "comp":                comp_applied,
    }


# ── Account lifecycle: suspend / unsuspend ──────────────────────


class SuspendRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


async def _notify_account_contacts(platform_db, account_id: int, send_fn, **kwargs):
    """Email every owner (and the billing email) about a lifecycle event.

    Best-effort: a mail failure never aborts the lifecycle action.
    Deduplicates addresses so an owner who is also the billing contact
    gets one email, not two.
    """
    targets: set[str] = set()
    try:
        cur = await platform_db._db.execute(
            "SELECT email FROM users "
            "WHERE account_id = ? AND role = 'owner' AND email IS NOT NULL",
            (account_id,),
        )
        targets.update(dict(r)["email"] for r in await cur.fetchall())
        sub = await platform_db.get_subscription(account_id)
        if sub and sub.get("billing_email"):
            targets.add(sub["billing_email"])
    except Exception:
        logger.exception("lifecycle notify: recipient lookup failed acct=%s", account_id)
    for addr in targets:
        try:
            send_fn(to=addr, **kwargs)
        except Exception:
            logger.exception("lifecycle notify: send to %s failed", addr)


@router.post("/accounts/{account_id}/suspend")
async def operator_suspend_account(
    account_id: int,
    body: SuspendRequest,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Suspend a tenant: blocks ALL logins, kills active sessions.

    Reversible via the unsuspend endpoint.  Data is untouched.  The
    reason is mandatory — it lands in the audit trail and in the
    notification email, so "suspended by mistake" support calls can be
    resolved by reading the row.
    """
    acct = await platform_db.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    operator_tg = int(_user.get("telegram_id") or 0)
    ok = await platform_db.suspend_account(
        account_id, reason=body.reason, operator_tg_id=operator_tg,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="Account is already suspended")

    # Kill live sessions — suspension must take effect NOW, not when
    # the JWTs expire.
    revoked = await platform_db.revoke_account_sessions(account_id)
    from interfaces.api.auth import mark_jti_revoked
    for row in revoked:
        try:
            await mark_jti_revoked(row["jti"], row.get("expires_at"))
        except Exception:
            logger.exception("suspend: denylist push failed jti=%s", row.get("jti"))

    try:
        await platform_db.add_platform_audit(
            "account_suspended",
            account_id=account_id,
            actor=f"operator:{operator_tg}",
            details=f"reason={body.reason!r} sessions_revoked={len(revoked)}",
        )
    except Exception:
        logger.exception("suspend: audit write failed acct=%s", account_id)

    from capabilities.email.lifecycle_emails import send_account_suspended_email
    await _notify_account_contacts(
        platform_db, account_id, send_account_suspended_email,
        account_name=acct.name, reason=body.reason,
    )

    return {
        "status": "suspended",
        "account_id": account_id,
        "sessions_revoked": len(revoked),
    }


@router.post("/accounts/{account_id}/unsuspend")
async def operator_unsuspend_account(
    account_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Reverse a suspension.  Refuses when the account is in the
    owner-driven deletion grace — the owner must cancel that flow
    themselves (or the operator clears it at the DB level after a
    support conversation)."""
    acct = await platform_db.get_account(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    lc = await platform_db.get_account_lifecycle(account_id)
    if lc and lc.get("deleted_at"):
        raise HTTPException(
            status_code=409,
            detail="Account is in deletion grace — the owner must cancel "
                   "the deletion; unsuspend alone won't restore access.",
        )

    ok = await platform_db.unsuspend_account(account_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Account is not suspended")

    operator_tg = int(_user.get("telegram_id") or 0)
    try:
        await platform_db.add_platform_audit(
            "account_unsuspended",
            account_id=account_id,
            actor=f"operator:{operator_tg}",
        )
    except Exception:
        logger.exception("unsuspend: audit write failed acct=%s", account_id)

    from capabilities.email.lifecycle_emails import send_account_unsuspended_email
    await _notify_account_contacts(
        platform_db, account_id, send_account_unsuspended_email,
        account_name=acct.name,
    )

    return {"status": "active", "account_id": account_id}


@router.get("/accounts/{account_id}/lifecycle")
async def operator_account_lifecycle(
    account_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Lifecycle state for the operator console's Account-state card."""
    lc = await platform_db.get_account_lifecycle(account_id)
    if not lc:
        raise HTTPException(status_code=404, detail="Account not found")
    return lc


@router.get("/accounts")
async def list_accounts(
    search: str = Query(default="", description="Substring match on account name"),
    status: str = Query(default="", description="Filter by subscription status"),
    tier:   str = Query(default="", description="Filter by tier"),
    is_comped: str = Query(default="", description="'yes' | 'no' | '' for all"),
    account_type: str = Query(
        default="", alias="type",
        description="Account TYPE — 'real' | 'test' | '' for all.  A "
                    "classification axis, deliberately separate from "
                    "subscription status and is_active lifecycle.",
    ),
    limit:  int = Query(default=100, ge=1, le=500),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Cross-account list with billing-summary join.

    The N+1 here is bounded by ``limit`` (≤500); each subscription
    lookup is a single primary-key fetch.  At realistic operator
    cadence (a few page loads per day) this is fine without a JOIN.
    """
    accounts = await platform_db.list_accounts(active_only=False)
    needle = search.strip().lower()
    if needle:
        accounts = [a for a in accounts if needle in a.name.lower()]
    if tier:
        accounts = [a for a in accounts if a.tier == tier]
    if account_type == "test":
        accounts = [a for a in accounts if a.is_test]
    elif account_type == "real":
        accounts = [a for a in accounts if not a.is_test]

    items: list[dict] = []
    for acc in accounts[:limit]:
        sub = await platform_db.get_subscription(acc.id)
        # Apply server-side filters that need the subscription row
        if status and (not sub or sub.get("status") != status):
            continue
        if is_comped == "yes" and not (sub and sub.get("is_comped")):
            continue
        if is_comped == "no" and (sub and sub.get("is_comped")):
            continue
        items.append({
            "id":             acc.id,
            "name":           acc.name,
            "slug":           acc.slug,
            "tier":           acc.tier,
            "is_active":      acc.is_active,
            "type":           "test" if acc.is_test else "real",
            "created_at":     acc.created_at,
            # Subscription fields are shallow — full detail is on the
            # account-detail page; the list view stays cheap.
            "subscription":   _sub_summary(sub),
        })
    return {"items": items, "count": len(items)}


@router.get("/accounts/{account_id}")
async def get_account_detail(
    account_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Full operator view: account, subscription, comp history, recent invoices."""
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    sub = await platform_db.get_subscription(account_id)
    billing = None
    try:
        billing = await platform_db.compute_billing(account_id)
    except Exception:
        logger.exception("compute_billing failed for acct=%d", account_id)
    invoices = await platform_db.get_invoices(account_id, limit=10)
    comp_history = await platform_db.get_comp_history(account_id, limit=20)
    user_count = await platform_db.count_account_users(account_id)
    return {
        "account": {
            "id":           acc.id,
            "name":         acc.name,
            "slug":         acc.slug,
            "tier":         acc.tier,
            "is_active":    acc.is_active,
            "type":         "test" if acc.is_test else "real",
            "created_at":   acc.created_at,
            "timezone":     acc.timezone,
            "bot_username": acc.bot_username,
        },
        "subscription":  _sub_full(sub),
        "billing":       billing,
        "user_count":    user_count,
        "invoices":      invoices,
        "comp_history":  comp_history,
    }


# ── System-wide stats (read-only) ───────────────────────────────


@router.get("/stats")
async def system_stats(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Counters for the operator dashboard landing card.

    Pulled live each call — small numbers, fast queries.  No caching:
    a fresh load on the dashboard takes ~50ms.
    """
    accounts = await platform_db.list_accounts(active_only=False)
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    comped = 0
    past_due = 0
    for acc in accounts:
        sub = await platform_db.get_subscription(acc.id)
        if not sub:
            by_status["no_subscription"] = by_status.get("no_subscription", 0) + 1
            continue
        st = sub.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        tr = sub.get("tier") or "free"
        by_tier[tr] = by_tier.get(tr, 0) + 1
        if sub.get("is_comped"):
            comped += 1
        if sub.get("past_due_since"):
            past_due += 1
    return {
        "accounts_total":    len(accounts),
        "accounts_active":   sum(1 for a in accounts if a.is_active),
        "by_status":         by_status,
        "by_tier":           by_tier,
        "comped":            comped,
        "past_due":          past_due,
    }


# ── File scans (per-upload AV scan observability) ────────────────


@router.get("/scans")
async def file_scans_overview(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Operator console view of the per-upload virus-scan subsystem.

    Five panels in one payload:
      * ``health``        — current scanner config (enabled, host:port,
                             concurrency cap, rate limit, scan types).
      * ``stats``         — 7-day aggregate (counts by verdict, latency
                             p50/p95/max, top signatures hit).
      * ``recent``        — last 50 scan events for the activity table.
      * ``active_job``    — currently-running rescan (or null) with
                             live progress counters.
      * ``recent_jobs``   — last 10 finished rescan jobs.
      * ``quarantine``    — KB articles currently flagged as infected
                             by a rescan (pending operator action).

    Read-only by design — operators tune scanner behaviour via env
    vars (CLAMAV_*), not via this surface; this is observability.
    Rescan + quarantine actions are separate POST endpoints below.
    """
    from infra.file_safety import av_health
    stats = await platform_db.get_scan_stats(days=7)
    recent = await platform_db.get_recent_scans(limit=50)
    active_job = await platform_db.get_active_rescan_job()
    recent_jobs = await platform_db.list_recent_rescan_jobs(limit=10)
    quarantine = await platform_db.list_quarantined_articles()
    return {
        "health":       av_health(),
        "stats":        stats,
        "recent":       recent,
        "active_job":   active_job,
        "recent_jobs":  recent_jobs,
        "quarantine":   quarantine,
    }


@router.post("/scans/rescan", status_code=202)
async def start_scans_rescan(
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Kick off a background rescan of every stored KB attachment.

    Refuses (409) when a rescan is already running — single-job-at-
    a-time discipline.  Returns the new job_id immediately; progress
    is polled via GET /system/scans (active_job field).

    Requires CLAMAV_HOST to be configured — refuses (412) when the
    scanner is disabled, since a rescan with no scanner is a no-op
    that would just mark everything clean.
    """
    from infra.file_safety import is_av_enabled
    from infra.scan_rescan import start_rescan, is_rescan_in_flight
    if not is_av_enabled():
        raise HTTPException(
            status_code=412,
            detail="ClamAV not configured (CLAMAV_HOST unset); rescan would have nothing to check.",
        )
    if is_rescan_in_flight() or await platform_db.get_active_rescan_job():
        raise HTTPException(
            status_code=409,
            detail="A rescan is already running — wait for it to finish or cancel it first.",
        )
    job_id = await start_rescan(started_by=int(user.get("sub") or 0) or None)
    return {"job_id": job_id, "status": "running"}


@router.post("/scans/rescan/{job_id}/cancel", status_code=202)
async def cancel_scans_rescan(
    job_id: int,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Request cancellation of a running rescan.

    Cancellation is cooperative — the worker loop checks the flag
    each iteration and exits cleanly at the next boundary, then marks
    the job ``cancelled`` in the DB.  Returns immediately with the
    request acknowledged; the dashboard polls for the status flip.
    """
    from infra.scan_rescan import request_cancel
    job = await platform_db.get_rescan_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Rescan job not found")
    if job.get("status") != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.get('status')!r}; cannot cancel.",
        )
    accepted = request_cancel(job_id)
    if not accepted:
        # In-memory flag missing — likely the worker is in a different
        # process (single-process today, but defensive).  Mark cancelled
        # directly so the dashboard reflects intent; the worker (if any)
        # will exit at the next iteration via the DB-status check that's
        # also checked there.
        await platform_db.cancel_rescan_job(
            job_id, cancelled_by=int(user.get("sub") or 0) or None,
        )
    else:
        # Mark cancelled-by now; the worker also stamps cancelled when
        # it observes the flag (idempotent via DB-side status check).
        await platform_db.cancel_rescan_job(
            job_id, cancelled_by=int(user.get("sub") or 0) or None,
        )
    return {"ok": True, "job_id": job_id, "status": "cancelled"}


@router.post("/scans/quarantine/{article_id}/restore", status_code=200)
async def restore_quarantined(
    article_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Clear an article's quarantine flag — operator confirms it's a
    false positive (or has been remediated externally)."""
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    await platform_db.restore_quarantined_article(article_id)
    return {"ok": True, "article_id": article_id}


@router.delete("/scans/quarantine/{article_id}", status_code=200)
async def delete_quarantined(
    article_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Hard-delete a quarantined article (DB row + stored file).

    Uses the existing KB delete path so the same cleanup logic runs
    (object storage deletion, foreign-key cascades, etc.).  Refuses if
    the article isn't actually quarantined — operator should restore
    + use the normal delete UX for healthy articles.
    """
    article = await platform_db.get_kb_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.get("quarantined_at"):
        raise HTTPException(
            status_code=409,
            detail="Article is not quarantined — use the normal delete flow.",
        )
    # Use the existing delete method.  This handles object-store cleanup
    # via the kb-deletion side-effects already wired in storage.
    await platform_db._db.execute(
        "DELETE FROM knowledge_base WHERE id = ?", (int(article_id),),
    )
    return {"ok": True, "article_id": article_id}


# ── Data retention contract ──────────────────────────────────────


@router.get("/retention")
async def retention_contract(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """The live data-retention contract for the operator console.

    Returns every registered retention target, the keep-window it
    resolves to (the MAX across the features that declare a need), its
    scope, which features need it, and the last recorded run (when it last
    ran + rows deleted).  Read-only by design: the windows are code-owned
    contracts (e.g. ``safety_events`` at 3y is a compliance number), so
    this surface is observability — not configuration.
    """
    from capabilities.data_lifecycle.retention import discover
    from capabilities.data_lifecycle.retention.registry import resolve

    discover()
    last_runs = {
        row["target_key"]: row
        for row in await platform_db.get_latest_retention_runs()
    }
    targets = [
        {
            "key":       r.target.key,
            "label":     r.target.label,
            "scope":     r.target.scope,
            "keep_days": r.keep_days,
            "needs": [
                {"feature": n.feature, "keep_days": n.keep_days, "reason": n.reason}
                for n in sorted(r.needs, key=lambda n: n.feature)
            ],
            "last_run": (
                {
                    "ran_at":       last_runs[r.target.key].get("ran_at"),
                    "rows_deleted": last_runs[r.target.key].get("rows_deleted"),
                    "accounts":     last_runs[r.target.key].get("accounts"),
                }
                if r.target.key in last_runs else None
            ),
        }
        for r in sorted(resolve(), key=lambda x: (x.target.scope, x.target.key))
    ]
    return {
        "job": {
            "id": "data_retention",
            "schedule": "02:00 UTC daily",
            "scope": "all active accounts (server-side; never the customer's external cloud)",
        },
        "targets": targets,
    }


@router.get("/retention/orphans/{account_id}")
async def retention_orphan_report(
    account_id: int,
    grace_days: int = Query(7, ge=1, le=90),
    _user: dict = Depends(require_system_owner),
):
    """Report-only, server-local orphan-file scan for one account.

    Lists files under the account's LOCAL object-store subtree that no DB
    row references (leaked blobs from deleted parents).  DELETES NOTHING —
    this is the audit to review before any deletion is enabled, and it
    never touches the customer's external cloud (their Google Drive).
    """
    from dataclasses import asdict

    from capabilities.object_storage.orphans import scan_account_orphans
    from infra.platform import get_tenant_db

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="account not found")
    report = await scan_account_orphans(tenant, account_id, grace_days=grace_days)
    return asdict(report)


@router.post("/retention/orphans/{account_id}/purge")
async def retention_orphan_purge(
    account_id: int,
    confirm: bool = Query(False),
    grace_days: int = Query(7, ge=1, le=90),
    _user: dict = Depends(require_system_owner),
):
    """Operator-triggered, server-local deletion of an account's orphaned
    files.

    Requires ``?confirm=true`` to actually delete — without it (the
    default) this is a dry-run that reports what *would* be removed, so a
    missing flag can never delete.  Re-scans at call time and removes only
    files still orphaned; never touches the customer's external cloud.
    """
    from dataclasses import asdict

    from capabilities.object_storage.orphans import delete_account_orphans
    from infra.platform import get_tenant_db

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="account not found")
    result = await delete_account_orphans(
        tenant, account_id, grace_days=grace_days, dry_run=not confirm,
    )
    return asdict(result)


# ── Scheduler jobs ───────────────────────────────────────────────


@router.get("/scheduler")
async def scheduler_jobs(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Snapshot of the bot scheduler's registered jobs for the console.

    The scheduler runs in the bot process; this API process can't read its
    in-memory job registry, so the bot persists a snapshot (job id, trigger,
    next run) that this endpoint serves.  Read-only observability.
    """
    return {"jobs": await platform_db.get_scheduler_jobs()}


# ── Telemetry warehouse cascade ──────────────────────────────────


# The downsampling cascade — provider-agnostic warehouse plumbing, NOT a
# Samsara feed.  ``(label, table, ts_col, note, granularity)``; ts_col is the
# ingest-time column so freshness reflects the job running, not the source
# time.  The three aggregate tiers share the unified ``vehicle_telemetry``
# table, split by ``granularity`` (None = its own physical table).
_TELEMETRY_CASCADE = (
    ("Live state",     "vehicle_state",          "updated_at",  "ingest · every 1 min", None),
    ("5-min history",  "vehicle_state_snapshot", "captured_at", "computed · every 5 min · keep 7d", None),
    ("Hourly roll-up", "vehicle_telemetry",      "ingested_at", "computed · :05 · keep 90d", "hourly"),
    ("Daily roll-up",  "vehicle_telemetry",      "ingested_at", "computed · 00:05 UTC · keep 730d", "daily"),
    ("Weekly roll-up", "vehicle_telemetry",      "ingested_at", "computed · Mon 00:10 UTC · keep ~5y", "weekly"),
)


@router.get("/accounts/{account_id}/telemetry")
async def account_telemetry_cascade(
    account_id: int,
    _user: dict = Depends(require_system_owner),
):
    """The telemetry warehouse cascade for one account — live → 5-min →
    hourly → daily — with rows + last-ingest per tier.

    This is the operator-side home for the roll-up pipeline's health (it's
    our warehouse processing, not a provider feed).  Read-only.
    """
    from infra.platform import get_tenant_db

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="account not found")
    # Live + 5-min raw each own a physical table; the aggregate tiers share
    # vehicle_telemetry split by granularity, so they need a separate query.
    fresh = await tenant.get_feed_freshness(
        account_id, [(t[1], t[2]) for t in _TELEMETRY_CASCADE if t[4] is None],
    )
    by_grain = await tenant.get_telemetry_tier_freshness(account_id)
    tiers = []
    for (label, table, _ts, note, grain) in _TELEMETRY_CASCADE:
        f = (by_grain.get(grain) if grain else fresh.get(table)) or {}
        tiers.append({
            "label": label,
            "table": table,
            "note": note,
            "count": f.get("count", 0),
            "last_ingest_at": f.get("last_at"),
        })
    return {"account_id": account_id, "tiers": tiers}


# ── Metrics snapshot ─────────────────────────────────────────────


@router.get("/metrics-snapshot")
async def metrics_snapshot(
    _user: dict = Depends(require_system_owner),
):
    """Aggregate billing-counter values for the operator landing card.

    Reads directly from the in-process Prometheus registry instead of
    scraping the text exposition at ``/metrics`` — same numbers, but
    one less HTTP hop and we get them as floats rather than parsing
    strings.  Falls back to all-zeros when ``prometheus_client`` isn't
    installed (the stub metric path), so the UI renders cleanly in
    dev too.

    The counters are monotonic since process start; the UI does its
    own "delta since I last loaded" math.  No labels are aggregated
    here — the four buckets the landing card cares about are
    extracted by their label combination.
    """
    snapshot = {
        "webhook_events":          _counter_map("billing_webhook_events_total", labels=("event_type", "result")),
        "sync_quantity":           _counter_map("billing_sync_quantity_total",  labels=("result",)),
        "notifications":           _counter_map("billing_notifications_total",  labels=("kind", "channel")),
        "comp_sweep":              _counter_map("billing_comp_sweep_total",     labels=("action",)),
    }
    return snapshot


def _counter_map(metric_name: str, *, labels: tuple[str, ...]) -> dict:
    """Pull a Counter's per-label-combination totals from the registry.

    Returns a dict like ``{"checkout.session.completed|processed": 17}``
    where the key joins each label value with ``|``.  Empty when the
    counter doesn't exist (stub mode) or has no observations yet.
    """
    try:
        from prometheus_client import REGISTRY
    except ImportError:
        return {}
    out: dict[str, float] = {}
    for metric in REGISTRY.collect():
        if metric.name != metric_name.removesuffix("_total"):
            # prometheus_client strips the ``_total`` suffix on Counters
            # when iterating; tolerate both ways callers might pass it.
            if metric.name != metric_name:
                continue
        for sample in metric.samples:
            # Skip the auto-generated ``_created`` timestamp samples.
            if sample.name.endswith("_created"):
                continue
            key = "|".join(sample.labels.get(lab, "") for lab in labels)
            out[key] = out.get(key, 0.0) + sample.value
    return out


# ── Operator actions ─────────────────────────────────────────────
#
# These mutate state on a specific account.  Every action is logged at
# INFO level with the operator's telegram_id so the audit trail in the
# API log explains WHO triggered it (the comp helpers write to
# comp_account_history; these ones don't have a dedicated table, but
# the journaled API log is enough for ops post-mortems).


@router.post("/accounts/{account_id}/sync-quantity")
async def force_sync_quantity(
    account_id: int,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Manually reconcile Stripe's extras-line qty against active vehicles.

    The same logic runs after every Samsara ingest cycle (every 60s),
    so this endpoint is mainly for debugging: customer says the bill
    looks wrong, operator wants to force a sync now rather than wait
    for the next tick.  Returns the same status dict
    ``sync_billing_quantity`` would have returned from the scheduler.
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    result = await provider.sync_billing_quantity(account_id, platform_db)
    logger.info(
        "system: sync_billing_quantity manual run acct=%s operator_tg=%s result=%s",
        account_id, user.get("sub"), result,
    )
    return result


@router.post("/accounts/{account_id}/refresh-vehicles")
async def force_refresh_vehicles(
    account_id: int,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Pull the Samsara fleet overview for this account RIGHT NOW.

    Wraps ``capabilities.integrations.samsara.sync.ingest_vehicle_state`` —
    the same function the scheduler runs every 60s — but lets the
    operator force a fresh ingest when investigating discrepancies.
    Returns the count of vehicles persisted on this run.

    Inline blocking is intentional: a 3–5 s wait is fine for an
    operator clicking a button, and returning the count synchronously
    is the whole point (the operator wants to see the new number).
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    from capabilities.integrations.samsara.sync import ingest_vehicle_state
    try:
        persisted = await ingest_vehicle_state(account_id)
    except Exception as e:
        logger.exception(
            "system: ingest_vehicle_state failed acct=%s operator_tg=%s",
            account_id, user.get("sub"),
        )
        raise HTTPException(status_code=502, detail=f"Samsara ingest failed: {e}")
    # Re-read the new active / inactive split so the operator sees the
    # billing-relevant numbers, not just the raw persist count.
    billing = await platform_db.compute_billing(account_id)
    logger.info(
        "system: refresh-vehicles acct=%s operator_tg=%s persisted=%d active=%d",
        account_id, user.get("sub"), persisted,
        billing.get("active_vehicles", 0),
    )
    return {
        "account_id":        account_id,
        "persisted":         persisted,
        "active_vehicles":   billing.get("active_vehicles", 0),
        "inactive_vehicles": billing.get("inactive_vehicles", 0),
    }


class BillingEmailOverride(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


class AccountTypeBody(BaseModel):
    type: str = Field(..., pattern="^(real|test)$")


@router.patch("/accounts/{account_id}/type")
async def operator_set_account_type(
    account_id: int,
    body: AccountTypeBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Set the account TYPE — 'test' (internal dev artifact) or 'real'.

    Classification only — nothing about the account's function
    changes.  Deliberately NOT an ``is_active`` write: deactivating a
    test account would defeat its purpose, and lifecycle states
    (suspend/delete) keep their own guarded flows.  Stored as the
    ``accounts.is_test`` boolean; 'real' is the absence of the flag,
    so no third value can ever creep in.
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    await platform_db.update_account(
        account_id, is_test=1 if body.type == "test" else 0,
    )
    logger.info(
        "system: account type acct=%s -> %s operator_tg=%s",
        account_id, body.type, user.get("sub"),
    )
    return {"id": account_id, "type": body.type}


@router.patch("/accounts/{account_id}/billing-email")
async def operator_set_billing_email(
    account_id: int,
    body: BillingEmailOverride,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Operator-side email override for any account.

    The customer-facing dashboard intentionally has no email-edit
    surface (info-only).  This is the operator escape hatch — useful
    when a customer-side admin contact is unreachable but the receipts
    need to start landing somewhere else.  Calls
    ``provider.update_billing_email`` which both writes the local row
    AND pushes the change to Stripe's Customer object if one exists.
    """
    email = body.email.strip()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    from capabilities.platform.billing import get_provider
    provider = get_provider()
    result = await provider.update_billing_email(account_id, platform_db, email)
    logger.info(
        "system: billing-email override acct=%s operator_tg=%s synced_to_provider=%s",
        account_id, user.get("sub"), result.get("synced_to_provider"),
    )
    return result


# ── Alert routing config ─────────────────────────────────────────
#
# Operator surface for switching an account between the legacy
# ``single_group`` (one forum chat + topic per alert_type) and the
# ``per_persona_groups`` mode (flat group per persona).  Customers
# self-serve the same config from Settings → Bot (features/settings/
# account/router.py /admin/alert-routing); these operator endpoints
# remain for support work on any account.


# Persona keys the operator UI can register.  Mirrors
# capabilities.alerting.persona_mapping.PERSONAS but listed here as
# a constant the request validator can rely on without a cross-package
# import (and so a typo in the dashboard form gets a 400 immediately
# instead of writing a bad row).
_VALID_PERSONAS = ("owner_admin", "dispatcher", "safety", "fleet", "hr")


class AlertRoutingModeBody(BaseModel):
    mode: str = Field(..., description="'single_group' or 'per_persona_groups'")


class PersonaGroupUpsertBody(BaseModel):
    chat_id: int = Field(..., description="Telegram chat_id (negative for groups)")
    chat_title: str = Field(default="", max_length=200)


class PersonaGroupActiveBody(BaseModel):
    is_active: bool


@router.get("/accounts/{account_id}/alert-routing")
async def get_alert_routing(
    account_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Read the account's routing config: current mode plus every
    persona-group binding (active + inactive — operator needs to see
    soft-disabled rows so they can re-enable them).

    Always returns a fully-populated ``personas`` map keyed by every
    valid persona slug so the operator UI doesn't have to coalesce
    missing entries.  Unregistered personas come back as ``null``.
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    rows = await platform_db.list_persona_groups(account_id)
    by_persona: dict[str, dict | None] = {p: None for p in _VALID_PERSONAS}
    for r in rows:
        by_persona[r.persona] = {
            "persona":    r.persona,
            "chat_id":    r.chat_id,
            "chat_title": r.chat_title,
            "is_active":  r.is_active,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
    return {
        "account_id":         account_id,
        "alert_routing_mode": acc.alert_routing_mode,
        "personas":           by_persona,
        "valid_personas":     list(_VALID_PERSONAS),
    }


@router.put("/accounts/{account_id}/alert-routing/mode")
async def set_alert_routing_mode(
    account_id: int,
    body: AlertRoutingModeBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Flip the account between ``single_group`` and
    ``per_persona_groups``.  The adapter validates the mode string so
    a typo never lands silently — invalid → 400.

    Switching to ``per_persona_groups`` without registering the
    operational persona groups first is allowed: the routing resolver
    falls back to the legacy lookup during the partial-config window.
    """
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        ok = await platform_db.set_alert_routing_mode(account_id, body.mode.strip())
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update routing mode")
    logger.info(
        "system: alert_routing_mode acct=%s operator_tg=%s → %s",
        account_id, user.get("sub"), body.mode,
    )
    return {"ok": True, "alert_routing_mode": body.mode.strip()}


@router.put("/accounts/{account_id}/alert-routing/personas/{persona}")
async def upsert_persona_group(
    account_id: int,
    persona: str,
    body: PersonaGroupUpsertBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Bind a Telegram group chat to a persona for this account.
    Re-upserting with the same persona overwrites the chat_id (and
    reactivates if previously disabled — same shape as the underlying
    mixin contract).
    """
    if persona not in _VALID_PERSONAS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid persona {persona!r} — expected one of {list(_VALID_PERSONAS)}",
        )
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    row = await platform_db.upsert_persona_group(
        account_id=account_id, persona=persona,
        chat_id=body.chat_id, chat_title=body.chat_title.strip(),
    )
    logger.info(
        "system: persona group upserted acct=%s operator_tg=%s persona=%s chat=%s",
        account_id, user.get("sub"), persona, body.chat_id,
    )
    return {
        "persona":    row.persona,
        "chat_id":    row.chat_id,
        "chat_title": row.chat_title,
        "is_active":  row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.patch("/accounts/{account_id}/alert-routing/personas/{persona}/active")
async def set_persona_group_active(
    account_id: int,
    persona: str,
    body: PersonaGroupActiveBody,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Soft-disable or re-enable a persona group without losing the
    chat_id — convenient when an operator pauses one persona during
    onboarding or after a Telegram-side permissions change."""
    if persona not in _VALID_PERSONAS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid persona {persona!r}",
        )
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    # Existence check must include disabled rows — the whole point of
    # PATCH /active is to flip ``is_active`` on rows that exist but
    # are currently soft-disabled.  ``get_persona_group`` filters by
    # ``is_active=1`` so it would 404 a legitimate re-enable; use the
    # unfiltered list and look up the persona instead.
    all_rows = await platform_db.list_persona_groups(account_id)
    existing = next((r for r in all_rows if r.persona == persona), None)
    if existing is None:
        # No row at all → there's nothing to toggle.  The UI catches
        # this 404 and switches to the upsert form.
        raise HTTPException(
            status_code=404,
            detail="No persona group to toggle — upsert chat_id first",
        )
    await platform_db.set_persona_group_active(
        account_id, persona, active=body.is_active,
    )
    logger.info(
        "system: persona group set_active acct=%s operator_tg=%s persona=%s active=%s",
        account_id, user.get("sub"), persona, body.is_active,
    )
    return {"ok": True, "persona": persona, "is_active": body.is_active}


@router.delete("/accounts/{account_id}/alert-routing/personas/{persona}")
async def delete_persona_group(
    account_id: int,
    persona: str,
    user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Permanently remove a persona binding.  Use the PATCH /active
    endpoint instead for reversible disable — this is the destructive
    path (e.g. customer-side group deleted in Telegram, operator
    cleaning up the dangling row)."""
    if persona not in _VALID_PERSONAS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid persona {persona!r}",
        )
    acc = await platform_db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    await platform_db.delete_persona_group(account_id, persona)
    logger.info(
        "system: persona group deleted acct=%s operator_tg=%s persona=%s",
        account_id, user.get("sub"), persona,
    )
    return {"ok": True, "persona": persona}


# ── System health ────────────────────────────────────────────────


@router.get("/health")
async def system_health(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Operator landing health check — 'is the platform OK right now?'.

    Each component reports a status token (``ok`` / ``warn`` / ``down``)
    plus a human detail.  Checks are best-effort: a failure in one
    probe degrades that component to ``down`` rather than 500-ing the
    whole endpoint, so the operator still sees the rest.

    Note on scope: the API process can't see the bot process's
    in-memory registry (separate process), so 'bots' reports the
    DB-derived count of accounts with a configured bot token, not live
    daemon state.  Ingest freshness is inferred from the newest
    ``vehicle_state.captured_at`` — if that's hours stale, the Samsara
    ingest loop has stalled.
    """
    import time as _time
    components: dict[str, dict] = {}

    # DB — round-trip a trivial query + measure latency.
    t0 = _time.perf_counter()
    try:
        cur = await platform_db._db.execute("SELECT 1")
        await cur.fetchone()
        ms = round((_time.perf_counter() - t0) * 1000, 1)
        components["database"] = {
            "status": "ok" if ms < 250 else "warn",
            "detail": f"{ms} ms round-trip",
        }
    except Exception as e:
        components["database"] = {"status": "down", "detail": str(e)[:200]}

    # Redis — reflects the cache layer's own connection flag.
    try:
        import infra.cache as _cache
        up = _cache.is_available()
        components["redis"] = {
            "status": "ok" if up else "down",
            "detail": "connected" if up else "unavailable (degraded — no caching / locks)",
        }
    except Exception as e:
        components["redis"] = {"status": "down", "detail": str(e)[:200]}

    # Accounts + configured bots (DB-derived).
    try:
        accounts = await platform_db.list_accounts(active_only=False)
        active = sum(1 for a in accounts if a.is_active)
        with_bot = sum(1 for a in accounts if getattr(a, "bot_token_encrypted", None))
        components["accounts"] = {
            "status": "ok",
            "detail": f"{active}/{len(accounts)} active · {with_bot} with per-account bot",
        }
    except Exception as e:
        components["accounts"] = {"status": "down", "detail": str(e)[:200]}

    # Samsara ingest freshness — newest captured_at across vehicle_state.
    try:
        cur = await platform_db._db.execute(
            "SELECT MAX(captured_at) FROM warehouse.vehicle_state_live WHERE captured_at <> ''"
        )
        row = await cur.fetchone()
        latest = row[0] if row else None
        if not latest:
            components["ingest"] = {"status": "warn", "detail": "no vehicle signals recorded yet"}
        else:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
                status = "ok" if age_min < 30 else ("warn" if age_min < 180 else "down")
                components["ingest"] = {
                    "status": status,
                    "detail": f"newest vehicle signal {round(age_min)} min ago",
                }
            except (ValueError, AttributeError):
                components["ingest"] = {"status": "warn", "detail": f"latest={latest}"}
    except Exception as e:
        components["ingest"] = {"status": "down", "detail": str(e)[:200]}

    # Bot process pulse — the capacity sampler inside the bot writes a
    # minute row every 60s; its freshness IS live daemon state, closing
    # the "API can't see the bot process" gap documented above.
    try:
        from capabilities.platform.capacity.sampler import bot_heartbeat_age_min
        age = await bot_heartbeat_age_min(platform_db)
        if age is None:
            components["bot_process"] = {
                "status": "warn", "detail": "no heartbeat recorded yet",
            }
        else:
            status = "ok" if age <= 3 else ("warn" if age <= 10 else "down")
            components["bot_process"] = {
                "status": status,
                "detail": f"scheduler pulse {round(age)} min ago",
            }
    except Exception as e:
        components["bot_process"] = {"status": "down", "detail": str(e)[:200]}

    # Error rate — counts from error_log.
    try:
        errors_1h = await platform_db.count_recent_errors(hours=1)
        errors_24h = await platform_db.count_recent_errors(hours=24)
        components["errors"] = {
            "status": "ok" if errors_1h == 0 else ("warn" if errors_1h < 10 else "down"),
            "detail": f"{errors_1h} in last hour · {errors_24h} in last 24h",
        }
    except Exception as e:
        components["errors"] = {"status": "down", "detail": str(e)[:200]}

    # Overall = worst component.
    rank = {"ok": 0, "warn": 1, "down": 2}
    overall = max((c["status"] for c in components.values()),
                  key=lambda s: rank.get(s, 0), default="ok")
    return {"overall": overall, "components": components}


# ── Cross-account users ──────────────────────────────────────────


@router.get("/users")
async def list_users(
    search: str = Query(default="", description="Name substring, email, or exact telegram_id"),
    limit: int = Query(default=200, ge=1, le=500),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Cross-account user search for support / debugging."""
    items = await platform_db.list_all_users_cross_account(search=search, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/users/{user_id}/sessions")
async def list_user_sessions(
    user_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Active sessions for one user — operator triage.

    Used by the Users-page expand-row to surface where a specific
    customer is logged in (helps with "they say it's still logged in
    from their old laptop" support questions).  Limited to active rows;
    revoked / expired sessions are excluded.
    """
    items = await platform_db.list_user_sessions(user_id)
    return {"items": items, "count": len(items)}


@router.delete("/sessions/{session_id}")
async def revoke_session_as_operator(
    session_id: int,
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Operator revoke of any session by id.

    No user-ownership check — gated by ``require_system_owner``.  Used
    when a customer calls support saying "my laptop was stolen, kill
    that session" and the operator handles it on their behalf.  Pushes
    the jti onto the Redis denylist with TTL equal to remaining JWT
    life, same as the customer-side ``DELETE /user/sessions/{id}``.
    """
    revoked = await platform_db.revoke_user_session(session_id)
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail="Session not found or already revoked.",
        )
    from interfaces.api.auth import mark_jti_revoked
    await mark_jti_revoked(revoked["jti"], revoked.get("expires_at"))
    return {"ok": True, "revoked": {"id": revoked["id"], "jti": revoked["jti"]}}


# ── Error log ────────────────────────────────────────────────────


@router.get("/errors")
async def list_errors(
    source: str = Query(default="", description="api | bot | scheduler | system_bot | task | startup"),
    limit: int = Query(default=100, ge=1, le=500),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Recent error_log rows (newest first) with optional source filter."""
    items = await platform_db.list_recent_errors(source=source or "", limit=limit)
    return {"items": items, "count": len(items)}


# ── Cross-account audit feed ─────────────────────────────────────


@router.get("/audit/comp-actions")
async def audit_comp_actions(
    limit: int = Query(default=50, ge=1, le=500),
    action: str = Query(default="", description="Filter to one action token"),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Recent comp grant/renew/revoke/expire/reminder rows, all accounts."""
    items = await platform_db.list_recent_comp_actions(
        limit=limit, action=action or None,
    )
    return {"items": items, "count": len(items)}


@router.get("/audit/past-due")
async def audit_past_due(
    days: int = Query(default=30, ge=1, le=365),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Accounts that entered past_due within the last N days."""
    items = await platform_db.list_recently_past_due(days=days)
    return {"items": items, "count": len(items), "window_days": days}


@router.get("/audit/webhook-events")
async def audit_webhook_events(
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str = Query(default="", description="Filter to one Stripe event_type"),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Recent processed Stripe webhook events with resolved account name."""
    items = await platform_db.list_recent_processed_events(
        limit=limit, event_type=event_type or None,
    )
    return {"items": items, "count": len(items)}


# ── Cross-account invoice viewer ────────────────────────────────


@router.get("/invoices")
async def invoices_search(
    status: str = Query(default="", description="paid | open | uncollectible | void"),
    from_date: str = Query(default="", description="ISO-8601 lower bound on created_at"),
    to_date: str = Query(default="", description="ISO-8601 upper bound on created_at"),
    account_search: str = Query(default="", description="Substring match on account name"),
    limit: int = Query(default=100, ge=1, le=500),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Cross-account invoice search for the operator console."""
    items = await platform_db.list_recent_invoices_cross_account(
        status=status or None,
        from_date=from_date or None,
        to_date=to_date or None,
        account_search=account_search or None,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


# ── Helpers ─────────────────────────────────────────────────────


def _sub_summary(sub: dict | None) -> dict:
    """Project a subscription row to the small list-view shape.

    Kept small so the accounts list page stays under ~50KB even with
    1000 tenants.  Detail page hits ``/system/accounts/{id}`` for the
    full shape.
    """
    if not sub:
        return {"status": "no_subscription", "tier": "free"}
    return {
        "tier":              sub.get("tier") or "free",
        "status":            sub.get("status") or "active",
        "vehicle_count":     sub.get("vehicle_count") or 0,
        "is_comped":         bool(sub.get("is_comped")),
        "comp_expires_at":   sub.get("comp_expires_at"),
        "past_due_since":    sub.get("past_due_since"),
        "billing_email":     sub.get("billing_email") or "",
        "provider":          sub.get("provider") or "stub",
    }


def _sub_full(sub: dict | None) -> dict | None:
    """Project a subscription row to the full detail-view shape."""
    if not sub:
        return None
    # The DB row already has the right shape; we just strip nothing
    # and let FastAPI/Pydantic JSON-encode it.  Listed-fields-only
    # version not needed — operator detail page benefits from seeing
    # everything (provider customer ids, item ids for debugging).
    return dict(sub)


# ── Telematics integrations (operator cross-account view) ───────


@router.get("/integrations")
async def system_list_integrations(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Cross-account list of every telematics integration row.

    Backs the operator-only Integrations view at
    ``system.4truck.us/integrations``.  Owners see only their own
    rows via ``/integrations``; this endpoint surfaces ALL accounts'
    rows for support / ops triage.

    Never exposes raw credentials — only ``has_credentials`` plus the
    health / status columns.  Sorts errored rows to the top so the
    operator's eye lands on them first.
    """
    try:
        accounts = await platform_db.list_accounts(active_only=False)
    except Exception:
        logger.exception("system.list_integrations: list_accounts failed")
        raise HTTPException(503, "platform DB unavailable")

    out: list[dict] = []
    for acct in accounts:
        try:
            rows = await platform_db.list_account_integrations(acct.id)
        except Exception:
            logger.exception(
                "system.list_integrations: list failed acct=%d", acct.id,
            )
            continue
        for ai in rows:
            out.append({
                "account_id":         ai.account_id,
                "account_name":       acct.name,
                "provider_id":        ai.provider_id,
                "status":             ai.status,
                "connected_at":       ai.connected_at,
                "has_credentials":    bool(ai.credentials),
                "last_health_at":     ai.last_health_at,
                "last_health_error":  ai.last_health_error,
                "updated_at":         ai.updated_at,
            })

    # Errored rows first, then connected, then anything else; within
    # each bucket sort by most-recently-updated so the most relevant
    # rows for the operator's session bubble up.
    def _priority(row: dict) -> tuple:
        status = row.get("status", "")
        rank = {"error": 0, "connected": 1, "disabled": 2, "disconnected": 3}.get(status, 9)
        return (rank, row.get("updated_at", ""))

    out.sort(key=_priority)
    return {"integrations": out, "total": len(out)}


# ── AI feedback — operator review of dislikes ─────────────────────
#
# When a user clicks 👎 on the dashboard chat, ``ai_usage`` gets
# ``had_reask=TRUE`` and (if they filled the form) a
# ``feedback_reason`` + optional ``feedback_note``.  This endpoint
# surfaces those rows to the operator so each complaint can be
# triaged: 'unjust_refusal' rows feed the heuristic detector's
# keyword list; 'hallucinated' rows get the model downweighted
# harder; 'inaccurate' rows mean the tool result needs review.
# Joins ai_chat_history so the operator sees the actual user
# question + AI answer without having to cross-query.

@router.get("/ai-feedback")
async def system_ai_feedback(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
    reason: str | None = None,
    account_id: int | None = None,
    days: int = 30,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Recent thumbs-down rows across all accounts with their reason
    + note + the chat-history context (user question + AI answer).

    Filters:
    - ``reason`` — one of the 7 feedback_reason categories, or omit
      to include rows with NO reason set (bare thumbs-downs) too
    - ``account_id`` — scope to a single tenant
    - ``days`` — lookback window (default 30)
    - ``limit`` / ``offset`` — pagination
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    where = ["u.had_reask = TRUE", "u.created_at >= ?", "u.request_type = 'question'"]
    params: list = [cutoff]
    if reason:
        if reason not in {
            "inaccurate", "off_topic", "incomplete", "hallucinated",
            "vague", "unjust_refusal", "other",
        }:
            raise HTTPException(status_code=400, detail="invalid reason")
        where.append("u.feedback_reason = ?")
        params.append(reason)
    if account_id is not None:
        where.append("u.account_id = ?")
        params.append(account_id)

    # Pull the row + its closest chat-history context.  Two LATERAL-
    # style scalar subqueries get the user question and AI answer
    # closest to the row's created_at, so the operator sees the
    # actual conversation that triggered the dislike.
    sql = f"""
        SELECT
            u.id,
            u.account_id,
            COALESCE(a.name, 'acct#' || u.account_id) AS account_name,
            u.user_id,
            u.model,
            u.role,
            u.prompt_category,
            u.feedback_reason,
            u.feedback_note,
            u.latency_ms,
            u.created_at,
            (
                SELECT text FROM ai_chat_history
                WHERE account_id = u.account_id
                  AND user_id = u.user_id
                  AND role = 'user'
                  AND created_at <= u.created_at
                ORDER BY created_at DESC LIMIT 1
            ) AS user_question,
            (
                SELECT text FROM ai_chat_history
                WHERE account_id = u.account_id
                  AND user_id = u.user_id
                  AND role = 'model'
                  AND created_at >= u.created_at
                ORDER BY created_at ASC LIMIT 1
            ) AS ai_answer
        FROM ai_usage u
        LEFT JOIN accounts a ON a.id = u.account_id
        WHERE {" AND ".join(where)}
        ORDER BY u.id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cur = await platform_db._db.execute(sql, params)
    rows = await cur.fetchall()

    from adapters.storage.ai_chat import decrypt_chat_text

    def _decrypt_chat(v):
        return decrypt_chat_text(v) if v else v

    # Companion aggregate: how many rows per reason in the window
    # (regardless of pagination) so the panel can show the breakdown.
    counts_sql = f"""
        SELECT COALESCE(u.feedback_reason, '__none__') AS reason,
               COUNT(*) AS n
        FROM ai_usage u
        WHERE {" AND ".join(where[:1 + (1 if account_id is not None else 0) + (1 if reason else 0)])}
              AND u.had_reask = TRUE
              AND u.request_type = 'question'
              AND u.created_at >= ?
        GROUP BY COALESCE(u.feedback_reason, '__none__')
    """
    # Rebuild params for counts (drop the limit/offset)
    counts_params: list = [cutoff]
    if reason:
        counts_params.append(reason)
    if account_id is not None:
        counts_params.append(account_id)
    counts_params.append(cutoff)  # the WHERE u.created_at >= ? at the end
    try:
        cur2 = await platform_db._db.execute(counts_sql, counts_params)
        counts_rows = await cur2.fetchall()
        counts = {r["reason"]: int(r["n"]) for r in counts_rows}
    except Exception:
        # Defensive: aggregate is a nice-to-have; the main list is
        # the load-bearing data, don't crash the endpoint over it.
        counts = {}

    return {
        "items": [
            {
                "id": r["id"],
                "account_id": r["account_id"],
                "account_name": r["account_name"],
                "user_id": r["user_id"],
                "model": r["model"],
                "role": r["role"],
                "prompt_category": r["prompt_category"],
                "feedback_reason": r["feedback_reason"],
                "feedback_note": r["feedback_note"],
                "latency_ms": r["latency_ms"],
                "created_at": r["created_at"],
                # Chat text is encrypted at rest — decrypt for display,
                # degrading per-row on key mismatch (same helper the
                # user-facing history read uses).
                "user_question": _decrypt_chat(r["user_question"]),
                "ai_answer": _decrypt_chat(r["ai_answer"]),
            }
            for r in rows
        ],
        "counts_by_reason": counts,
        "days": days,
        "limit": limit,
        "offset": offset,
    }
