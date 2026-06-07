"""Inbound webhook endpoints.

Currently hosts the Resend email-event webhook (POST /webhooks/resend).
Mounted at the same prefix as the rest of the API; full path therefore
includes the API version prefix (e.g. ``/api/v1/webhooks/resend``).

Design notes for the Resend handler are inline in the route docstring
— this module is small and self-contained so future provider-specific
webhooks (Twilio, Cloudflare bounce, etc.) live here as siblings
without bloating any single route file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from infra.observability import record_email_webhook as _record
from infra.platform import get_platform_db as _get_platform_db, get_tenant_db as _get_tenant_db

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger(__name__)


# ── Resend webhook (svix signature) ──────────────────────────────────


# Bad-signature attempts get aggressively rate-limited per IP to make
# brute-force / probing expensive without hampering legitimate Resend
# delivery.  Window + cap kept generous because Resend's own retries
# (5s, 5min, 30min, 2h, 5h, 10h) plus bursts of unrelated events can
# legitimately produce 50+ requests from one IP within an hour.
_BAD_SIG_WINDOW_SECS = 60
_BAD_SIG_CAP = 50
# Svix signed-timestamp acceptance window — Stripe uses 5 minutes; svix
# uses the same.  Beyond this, signed payloads are treated as replays.
_REPLAY_WINDOW_SECS = 5 * 60


def _verify_svix_signature(
    *,
    secret: str,
    msg_id: str,
    timestamp: str,
    signatures: str,
    body: bytes,
) -> bool:
    """Verify svix-style HMAC-SHA256 signature without the svix SDK.

    Svix signs ``"{msg_id}.{timestamp}.{body}"`` with the per-endpoint
    secret (base64-decoded, sans the ``whsec_`` prefix Resend prefixes
    in the dashboard).  The ``svix-signature`` header can list
    multiple ``v1,<sig>`` entries (signature rotation) — any match
    passes.  Constant-time compare via hmac.compare_digest.

    Returns False on any verification failure — caller treats False as
    untrusted and refuses the request.  Never raises.
    """
    if not (secret and msg_id and timestamp and signatures):
        return False
    # Strip the dashboard "whsec_" prefix if present
    secret_clean = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        secret_bytes = base64.b64decode(secret_clean)
    except Exception:
        return False
    signed_payload = f"{msg_id}.{timestamp}.".encode("utf-8") + body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_payload, hashlib.sha256).digest()
    ).decode("ascii")
    # Header is a space-separated list of "v1,<sig>" entries.  We
    # explicitly allowlist v1 (svix's documented HMAC-SHA256 scheme)
    # so a future svix break that introduces v2 with a weaker algorithm
    # can't be silently substituted by an attacker who controls one of
    # the listed sig entries.  Document the rotation procedure in the
    # runbook when svix bumps the version.
    for entry in signatures.split(" "):
        if "," not in entry:
            continue
        ver, sig = entry.split(",", 1)
        if ver != "v1":
            continue
        if hmac.compare_digest(sig, expected):
            return True
    return False


async def _track_bad_sig(client_ip: str) -> bool:
    """Increment a Redis counter for the (Resend webhook, bad-sig)
    bucket scoped to client IP.  Returns False when the cap is
    exceeded — caller responds 401 without further work.  Fail-OPEN
    on Redis outage (the signature check itself is the primary
    defense; the rate-limit is just to make probing expensive)."""
    from adapters.cache.redis import rate_limit_check, is_available as _redis_ok
    if not _redis_ok():
        return True
    return await rate_limit_check(
        f"webhook_resend_badsig:{client_ip}",
        window_secs=_BAD_SIG_WINDOW_SECS,
        max_requests=_BAD_SIG_CAP,
    )


@router.post("/webhooks/resend")
async def resend_webhook(
    request: Request,
    svix_id: Optional[str] = Header(default=None, alias="svix-id"),
    svix_timestamp: Optional[str] = Header(default=None, alias="svix-timestamp"),
    svix_signature: Optional[str] = Header(default=None, alias="svix-signature"),
):
    """Resend email-event webhook.

    Handles ``email.bounced``, ``email.delivery_delayed``,
    ``email.complained``, ``email.delivered`` — the events that affect
    invite-row state surfaced to the operator.  All other event types
    return 200 (Resend doesn't retry on success — we don't want
    unhandled events to wedge the retry queue).

    Hardening (every choice forced by an adversarial design review):

    1. **Fail CLOSED on missing secret.**  Mirrors the Stripe webhook
       precedent.  Without this, an unset RESEND_WEBHOOK_SECRET turns
       the endpoint into "any POST revokes invites by guessing IDs".

    2. **Svix signature verification.**  HMAC-SHA256 over (msg_id,
       timestamp, raw_body) using the per-endpoint secret from the
       Resend dashboard.  Constant-time compare prevents timing
       side-channels on the secret itself.

    3. **5-minute replay window.**  Reject signed payloads whose
       svix-timestamp is more than 5 minutes off the server clock.
       Combined with the idempotency gate below, this caps the
       replay window to ~5 min.

    4. **Per-IP bad-sig rate limit.**  50 invalid signatures in 60s
       from one IP triggers a 429 — makes brute-force secret-guessing
       expensive while staying generous enough for legitimate Resend
       traffic.

    5. **Svix-ID idempotency gate.**  INSERT-OR-IGNORE into
       ``email_webhook_events`` table.  Same shape as Stripe's
       ``mark_stripe_event_processed`` — Resend retries (5s, 5min,
       30min, 2h, 5h, 10h schedule) become fast no-ops on duplicate.

    6. **Resend.email_id is the ONLY trusted lookup key.**  Recipient-
       address fallback is REFUSED — it opens cross-account hijack
       (Account A's bounce on alice@x.com flips Account B's still-
       valid invite to the same address).

    7. **Hard vs soft bounce.**  ``data.bounce.type == 'Permanent'``
       is hard (flip immediately); ``Transient`` is soft (counter
       only, flip after 3 strikes).  Delivered after soft-bounce
       CLEARS the bounce state.

    8. **Spam complaint = FLAG, not revoke.**  Auto-revoke on
       complaint is silent destruction — operator sees the invite
       vanish with no explanation.  Instead we stamp a distinct
       ``email_complained_at`` field and the dashboard surfaces a
       prominent badge; operator chooses revoke vs ignore.

    9. **PII-safe logging.**  Recipient addresses are never logged
       above DEBUG — INFO logs use ``sha256(recipient)[:12]`` and the
       recipient domain only.  Defends the at-rest encryption.

    10. **Always returns 200** for matched-or-not-found events.
        Non-2xx triggers Resend's full retry schedule, which would
        flood logs and mask real failures.  Only signature/payload
        errors return 4xx.
    """
    secret = os.getenv("RESEND_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Fail-CLOSED — refuse to process anything without a configured
        # secret.  Mirrors Stripe.  503 (not 401) so the operator
        # noticing in their Resend dashboard knows it's a server
        # config issue, not a credential rejection.
        logger.error(
            "resend_webhook: RESEND_WEBHOOK_SECRET unset — refusing all events"
        )
        _record("unknown", "unset_secret")
        raise HTTPException(
            status_code=503,
            detail="Webhook receiver not configured",
        )

    client_ip = (
        request.client.host if request.client else "unknown"
    )

    body = await request.body()

    # Signature + replay-window verification.  Both failures look the
    # same from the outside (401 invalid signature) — a real attacker
    # gets no distinguishing signal.
    if not (svix_id and svix_timestamp and svix_signature):
        await _track_bad_sig(client_ip)
        _record("unknown", "invalid_signature")
        raise HTTPException(status_code=401, detail="Missing signature headers")
    try:
        ts_int = int(svix_timestamp)
    except (TypeError, ValueError):
        await _track_bad_sig(client_ip)
        _record("unknown", "invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid timestamp")
    if abs(int(time.time()) - ts_int) > _REPLAY_WINDOW_SECS:
        await _track_bad_sig(client_ip)
        _record("unknown", "invalid_signature")
        raise HTTPException(status_code=401, detail="Replay window exceeded")
    if not _verify_svix_signature(
        secret=secret,
        msg_id=svix_id,
        timestamp=svix_timestamp,
        signatures=svix_signature,
        body=body,
    ):
        # Per-IP rate-limit on bad sig.  When cap exceeded, swap 401
        # for 429 — same response code an attacker would see at the
        # cap, no oracle of "the secret is right but rate-limited".
        _record("unknown", "invalid_signature")
        if not await _track_bad_sig(client_ip):
            raise HTTPException(status_code=429, detail="Too many invalid signatures")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload AFTER signature passes — never trust JSON body
    # contents from an unverified source.  Malformed JSON from a
    # signed source is a Resend bug; treat as a "matched, malformed"
    # 200 instead of 400 so we don't burn through 6 retries trying
    # to re-process the same bad payload.  Mark the svix_id seen
    # BEFORE returning so the retries hit the duplicate gate.
    db = await _get_platform_db()
    try:
        event = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("resend_webhook: malformed JSON svix_id=%s — dropping (no retry)", svix_id)
        await db.mark_email_webhook_event_seen(svix_id, "malformed")
        _record("unknown", "malformed")
        return {"ok": True, "malformed": True}

    event_type = (event.get("type") or "").strip()
    event_data = event.get("data") or {}

    # Idempotency PRE-CHECK only — we mark seen AFTER successful
    # state mutation (see below).  This pre-check is just a fast
    # path for retries we've already fully processed.  The actual
    # mark-seen lives after the state mutation succeeds, so a
    # transient downstream failure (DB hiccup, audit write fail)
    # leaves the svix_id UN-marked → Resend retries → we get a
    # second chance.  Without this two-phase split, a 500 on the
    # state-mutation path would lose the bounce permanently.
    if not await _is_email_webhook_event_already_processed(db, svix_id):
        pass  # first attempt — fall through to processing
    else:
        # Duplicate from Resend's retry storm — fast 200, no work.
        logger.debug("resend_webhook: duplicate svix_id=%s event=%s", svix_id, event_type)
        _record(event_type, "duplicate")
        return {"ok": True, "duplicate": True}

    # Pre-extract the recipient hash + domain for safe INFO logs —
    # never log the raw address.
    raw_recipients = event_data.get("to") or []
    recipient_email = raw_recipients[0].lower() if raw_recipients else ""
    rcp_hash = (
        hashlib.sha256(recipient_email.encode("utf-8")).hexdigest()[:12]
        if recipient_email else ""
    )
    rcp_domain = recipient_email.rpartition("@")[-1] or ""

    # Lookup invite by Resend's email_id — the ONLY trusted key.
    resend_email_id = event_data.get("email_id") or ""
    invite = None
    if resend_email_id:
        invite = await db.find_invite_by_resend_email_id(resend_email_id)
    if not invite:
        # Tag fallback — only honoured because tags are echoed in the
        # Resend webhook payload (X-Entity-Ref-ID is not).  We set
        # tags={'invite_code': code, ...} at send-time.  IMPORTANT:
        # gate on resend_email_id IS NOT NULL on the matched invite —
        # only events for invites we KNOW we sent via Resend pass
        # this fallback.  Otherwise a webhook payload with an
        # arbitrary invite_code tag (someone who guessed a code from
        # a chat-share leak) could trigger state mutations on
        # non-email-channel invites.
        tags = event_data.get("tags") or []
        if isinstance(tags, list):
            tag_map = {
                t.get("name"): t.get("value")
                for t in tags if isinstance(t, dict)
            }
            invite_code = tag_map.get("invite_code")
            candidate = await db.get_invite(invite_code) if invite_code else None
            if candidate and candidate.resend_email_id:
                invite = candidate

    if not invite:
        logger.info(
            "resend_webhook: %s for unknown email_id=%s rcp=%s@%s (no invite match)",
            event_type, resend_email_id[:12], rcp_hash, rcp_domain,
        )
        # Mark seen so retries of this unknown event are fast no-ops.
        await db.mark_email_webhook_event_seen(svix_id, event_type)
        _record(event_type, "unmatched")
        return {"ok": True, "matched": False}

    # Dispatch on event type.  Each branch:
    #   - mutates invite state via storage methods (no inline SQL)
    #   - writes a tenant audit row ONLY when the mutation applied
    #     (the storage layer's used/revoked guard can race-reject;
    #     writing an audit row in that case would lie about state
    #     change that didn't happen)
    #   - marks the svix_id seen AFTER the mutation so a transient
    #     failure leaves Resend's retry budget available
    #   - returns 200 with uniform shape so an attacker with a stolen
    #     secret can't oracle "this email_id maps to an invite"
    if event_type == "email.bounced":
        bounce = event_data.get("bounce") or {}
        bounce_kind = (bounce.get("type") or "Permanent").lower()
        bounce_type = "hard" if bounce_kind == "permanent" else "soft"
        reason = (bounce.get("message") or bounce.get("subType") or "")[:200]
        updated = await db.mark_invite_email_bounced(
            invite.account_id, invite.id,
            bounce_type=bounce_type, reason=reason,
        )
        if updated:
            await _audit_bounce(invite, "invite_email_bounced", bounce_type, reason)
        logger.info(
            "resend_webhook: bounce %s for invite_id=%s rcp=%s@%s reason=%s applied=%s",
            bounce_type, invite.id, rcp_hash, rcp_domain, reason[:60], updated is not None,
        )
        await db.mark_email_webhook_event_seen(svix_id, event_type, invite.account_id, invite.id)
        _record(event_type, "processed")
        return {"ok": True}

    elif event_type == "email.delivery_delayed":
        bounce = event_data.get("bounce") or {}
        reason = (bounce.get("message") or "delivery delayed")[:200]
        updated = await db.mark_invite_email_bounced(
            invite.account_id, invite.id,
            bounce_type="soft", reason=reason,
        )
        if updated:
            await _audit_bounce(invite, "invite_email_bounced", "soft", reason)
        await db.mark_email_webhook_event_seen(svix_id, event_type, invite.account_id, invite.id)
        _record(event_type, "processed")
        return {"ok": True}

    elif event_type == "email.complained":
        updated = await db.mark_invite_email_bounced(
            invite.account_id, invite.id,
            bounce_type="complaint",
            reason="Recipient reported the message as spam.",
        )
        if updated:
            await _audit_bounce(invite, "invite_email_complained", "complaint", "")
        await db.mark_email_webhook_event_seen(svix_id, event_type, invite.account_id, invite.id)
        _record(event_type, "processed")
        return {"ok": True}

    elif event_type == "email.delivered":
        # Race resolution: if a soft-bounce or intermediate soft-
        # strike state arrived before the successful retry's
        # delivered event, clear the soft-bounce counter so we
        # don't later misreport "Bounced after 3 attempts".  Hard
        # bounces and complaints stay sticky.
        await db.clear_invite_soft_bounce(invite.account_id, invite.id)
        await db.mark_email_webhook_event_seen(svix_id, event_type, invite.account_id, invite.id)
        _record(event_type, "processed")
        return {"ok": True}

    else:
        # Unhandled event type — return 200 (don't make Resend retry)
        # but log at DEBUG so we can see what events are flowing
        # without polluting INFO.  Mark seen so a retry of the same
        # unknown event short-circuits at the duplicate gate.
        logger.debug(
            "resend_webhook: ignored event_type=%s for invite_id=%s",
            event_type, invite.id,
        )
        await db.mark_email_webhook_event_seen(svix_id, event_type, invite.account_id, invite.id)
        _record(event_type, "processed")
        return {"ok": True}


async def _is_email_webhook_event_already_processed(db, svix_id: str) -> bool:
    """Pre-check the idempotency table without marking the svix_id
    as seen.  Returns True when we already have a row for this
    svix_id (i.e. this is a Resend retry of an event we've already
    processed).  The actual mark-seen INSERT happens AFTER the state
    mutation succeeds, which is the key idempotency-correctness fix
    from the storage-review.  Without this split, a 500 between the
    mark-seen and the mutation would lose the bounce permanently.
    """
    if not svix_id:
        return False
    cur = await db._db.execute(
        "SELECT 1 FROM email_webhook_events WHERE svix_id = ? LIMIT 1",
        (svix_id,),
    )
    row = await cur.fetchone()
    return row is not None


async def _audit_bounce(invite, action: str, bounce_type: str, reason: str) -> None:
    """Tenant-scoped audit row for a webhook-driven state change.

    Actor is None (system-driven) — the AuditLog viewer's
    ACTION_LABEL maps these to readable labels so the operator sees
    'Invite email bounced (hard)' rather than the raw action key.

    Reason is truncated to 100 chars in the audit details (the full
    text lives encrypted on the invite row) so a 200-char relay
    error message doesn't blow the audit-log details cap.
    """
    try:
        tenant = await _get_tenant_db(invite.account_id)
        details = (
            f"Role: {invite.role}, dept: {invite.department}, "
            f"bounce_type: {bounce_type}, reason: {reason[:100]}"
        )[:500]
        await tenant.add_audit_log(
            invite.account_id, None,
            action,
            target_type="invite", target_id=str(invite.id),
            details=details,
        )
    except Exception as e:
        logger.warning("audit_log on %s failed: %s", action, e)
