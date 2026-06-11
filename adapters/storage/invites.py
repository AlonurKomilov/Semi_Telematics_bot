"""Invites CRUD mixin."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Role, Invite, User

logger = logging.getLogger(__name__)


class InvitesMixin:

    async def create_invite(
        self, account_id: int, created_by: int,
        role: Role = Role.FLEET,
        truck_num: Optional[str] = None,
        hours: int = 24,
        recipient_email: Optional[str] = None,
    ) -> Invite:
        """Generate a one-time invite code.

        ``recipient_email`` is the email-channel marker.  When set:
          - stamps ``sent_to_email`` on the row (encrypted via
            infra.crypto when ENCRYPTION_KEY is set)
          - the row's ``channel`` property returns 'email'
          - the caller (route handler) is expected to call
            ``send_invite_email`` immediately AFTER create_invite
            returns, then ``mark_invite_email_sent`` on success
        When unset, behaviour is unchanged — link-channel invite.
        """
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=hours)

        code = self._generate_invite_code()
        now_str = now.isoformat()
        exp_str = expires.isoformat()

        # Encrypt the recipient email at rest — infra.crypto.encrypt
        # is a no-op when ENCRYPTION_KEY is unset (dev mode), and
        # transparently prepends an ``enc::`` marker when it is, so
        # _row_to_invite can detect + decrypt on read.
        stored_email: Optional[str] = None
        if recipient_email:
            from infra.crypto import encrypt as _encrypt
            try:
                stored_email = _encrypt(recipient_email)
            except Exception as e:
                # ENCRYPTION_KEY misconfigured in this env — store
                # plaintext rather than blocking the invite create.
                # The audit trail captures the recipient regardless.
                # Warn-log loudly because silent plaintext storage is
                # the worst kind of degradation: the "encrypted at
                # rest" claim becomes un-falsifiable from the
                # operator's perspective.
                logger.warning(
                    "create_invite: failed to encrypt recipient_email "
                    "for account_id=%s (storing plaintext): %s",
                    account_id, e, exc_info=True,
                )
                stored_email = recipient_email

        cur = await self._db.execute(
            """INSERT INTO invites
               (code, account_id, role, truck_num,
                created_by, expires_at, created_at, sent_to_email)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, account_id, role.value, truck_num,
             created_by, exp_str, now_str, stored_email),
        )
        await self._db.commit()
        return Invite(
            id=cur.lastrowid, code=code, account_id=account_id,
            role=role.value, truck_num=truck_num,
            created_by=created_by, expires_at=exp_str,
            used_by=None, created_at=now_str,
            revoked_at=None,
            # Return the PLAINTEXT email in the dataclass — the
            # encryption is for at-rest storage only.  Callers that
            # need the recipient (route audit log, mailer) get it
            # without an extra decrypt round-trip.
            sent_to_email=recipient_email,
            email_sent_at=None,
            email_send_count=0,
        )

    async def mark_invite_email_sent(
        self, account_id: int, invite_id: int,
        recipient: Optional[str] = None,
    ) -> Optional[Invite]:
        """Stamp ``email_sent_at`` + increment ``email_send_count``
        after a successful SMTP handoff.

        ``recipient`` is optional.  Pass it on resend operations
        where the operator may have changed the recipient
        (currently NOT exposed — resend reuses the original — but
        the parameter is there for future "resend to a different
        address" flows so this storage API doesn't churn).

        Returns the updated Invite or None for not-found /
        cross-account / used / revoked (uniform with the other
        invite-mutation methods).  Same SELECT-then-UPDATE-with-
        guard shape and transaction wrap.
        """
        async with self.transaction():
            cur = await self._db.execute(
                """SELECT * FROM invites
                    WHERE id = ?
                      AND account_id = ?
                      AND used_by IS NULL
                      AND revoked_at IS NULL
                    LIMIT 1""",
                (invite_id, account_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            invite = self._row_to_invite(row)
            now_iso = datetime.now(timezone.utc).isoformat()
            new_count = (invite.email_send_count or 0) + 1
            # Update recipient when the caller passed a new one;
            # otherwise leave the existing ciphertext alone.
            if recipient is not None and recipient != invite.sent_to_email:
                from infra.crypto import encrypt as _encrypt
                try:
                    stored = _encrypt(recipient)
                except Exception as e:
                    logger.warning(
                        "mark_invite_email_sent: failed to encrypt "
                        "recipient for invite_id=%s (storing plaintext): %s",
                        invite_id, e, exc_info=True,
                    )
                    stored = recipient
                upd = await self._db.execute(
                    "UPDATE invites SET email_sent_at = ?, "
                    "  email_send_count = ?, sent_to_email = ? "
                    "WHERE id = ? AND account_id = ? "
                    "  AND used_by IS NULL AND revoked_at IS NULL",
                    (now_iso, new_count, stored, invite.id, account_id),
                )
                invite.sent_to_email = recipient
            else:
                upd = await self._db.execute(
                    "UPDATE invites SET email_sent_at = ?, "
                    "  email_send_count = ? "
                    "WHERE id = ? AND account_id = ? "
                    "  AND used_by IS NULL AND revoked_at IS NULL",
                    (now_iso, new_count, invite.id, account_id),
                )
            if upd.rowcount != 1:
                # Race-lost — uniform None return.
                return None
            invite.email_sent_at = now_iso
            invite.email_send_count = new_count
            return invite

    # ── Bounce / complaint state mutators (migration 097) ───────────

    async def set_invite_resend_email_id(
        self, account_id: int, invite_id: int, resend_email_id: str,
    ) -> bool:
        """Stamp the Resend HTTP API's per-send identifier on the row
        so the bounce-webhook handler can match events to invites
        without trusting recipient address (cross-account hijack vector
        documented in the design review).  Called immediately after
        ``send_invite_email_via_resend_api`` returns successfully.

        Returns True on update, False if the row was missing/cross-
        account.  Doesn't gate on used/revoked — the email_id is
        informational and harmless to record even if the operator
        later revokes the invite.
        """
        cur = await self._db.execute(
            "UPDATE invites SET resend_email_id = ? "
            "WHERE id = ? AND account_id = ?",
            (resend_email_id, invite_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount == 1

    async def find_invite_by_resend_email_id(
        self, resend_email_id: str,
    ) -> Optional[Invite]:
        """Lookup the invite a Resend webhook event belongs to.
        Returns None when no match — the bounce-webhook handler
        treats that as a no-op (returns 200 so Resend doesn't retry).

        No account scoping here because the webhook itself is
        unauthenticated and we look up SOLELY by the Resend-assigned
        identifier.  Any account_id-scoped logic happens AFTER the
        lookup using ``invite.account_id``.

        NOTE: this method is the ONLY supported invite-lookup path
        for webhook handlers.  Falling back to recipient-email
        search on miss is forbidden — it opens a cross-account
        hijack where account A bouncing alice@x.com flips a flag on
        account B's still-valid invite to the same address.
        """
        if not resend_email_id:
            return None
        cur = await self._db.execute(
            "SELECT * FROM invites WHERE resend_email_id = ? LIMIT 1",
            (resend_email_id,),
        )
        row = await cur.fetchone()
        return self._row_to_invite(row) if row else None

    async def mark_invite_email_bounced(
        self,
        account_id: int,
        invite_id: int,
        *,
        bounce_type: str,        # 'hard' | 'soft' | 'complaint'
        reason: str,
    ) -> Optional[Invite]:
        """Stamp bounce/complaint state on an invite.

        Semantics:
          - HARD bounce: flip email_bounced_at + email_bounce_type='hard'
            immediately.  Resend button gets replaced by 'Revoke &
            recreate' on the dashboard.
          - SOFT bounce: increment email_soft_bounce_count.  Flip the
            permanent bounce flag only when count >= 3 (most soft
            bounces are transient — greylisting, mailbox full, DNS —
            and self-clear within hours).  Operator sees an amber
            'Delivery issues (N)' badge before the 3rd attempt.
          - COMPLAINT: flip email_complained_at + email_bounce_type=
            'complaint'.  Does NOT auto-revoke (silent destruction)
            — operator sees a distinct 'Reported as spam' badge and
            chooses revoke vs ignore.

        Guards:
          - Returns None for unknown invite_id / cross-account
          - Returns None for USED or REVOKED invites — the bounce
            arrived after the invite was already settled and
            stamping it would mislead the operator
        Reason is encrypted at rest (relays echo recipient address);
        capped at 200 chars before encryption.

        All non-trivial state changes happen in a transaction so a
        concurrent revoke/extend can't race against the bounce stamp.
        """
        if bounce_type not in ("hard", "soft", "complaint"):
            raise ValueError(f"bounce_type must be hard|soft|complaint, got {bounce_type!r}")
        async with self.transaction():
            cur = await self._db.execute(
                "SELECT * FROM invites "
                "WHERE id = ? AND account_id = ? "
                "  AND used_by IS NULL AND revoked_at IS NULL "
                "LIMIT 1",
                (invite_id, account_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            invite = self._row_to_invite(row)
            now_iso = datetime.now(timezone.utc).isoformat()
            reason_clean = (reason or "")[:200]
            # Encrypt reason — recipient PII echo in relay text.
            from infra.crypto import encrypt as _encrypt
            try:
                stored_reason = _encrypt(reason_clean) if reason_clean else None
            except Exception as e:
                logger.warning(
                    "mark_invite_email_bounced: encrypt reason failed "
                    "for invite_id=%s (storing plaintext): %s",
                    invite_id, e, exc_info=True,
                )
                stored_reason = reason_clean

            if bounce_type == "soft":
                new_soft_count = (invite.email_soft_bounce_count or 0) + 1
                # Flip the permanent flag only when crossing the
                # 3-strike threshold.  Below it: just increment + log.
                if new_soft_count >= 3:
                    upd = await self._db.execute(
                        "UPDATE invites SET "
                        "  email_soft_bounce_count = ?, "
                        "  email_bounced_at = ?, "
                        "  email_bounce_type = 'soft', "
                        "  email_bounce_reason = ? "
                        "WHERE id = ? AND account_id = ? "
                        "  AND used_by IS NULL AND revoked_at IS NULL",
                        (new_soft_count, now_iso, stored_reason, invite_id, account_id),
                    )
                    invite.email_bounced_at = now_iso
                    invite.email_bounce_type = "soft"
                    invite.email_bounce_reason = reason_clean
                else:
                    upd = await self._db.execute(
                        "UPDATE invites SET "
                        "  email_soft_bounce_count = ?, "
                        "  email_bounce_reason = ? "
                        "WHERE id = ? AND account_id = ? "
                        "  AND used_by IS NULL AND revoked_at IS NULL",
                        (new_soft_count, stored_reason, invite_id, account_id),
                    )
                    invite.email_bounce_reason = reason_clean
                invite.email_soft_bounce_count = new_soft_count
            elif bounce_type == "complaint":
                upd = await self._db.execute(
                    "UPDATE invites SET "
                    "  email_complained_at = ?, "
                    "  email_bounce_type = 'complaint', "
                    "  email_bounce_reason = ? "
                    "WHERE id = ? AND account_id = ? "
                    "  AND used_by IS NULL AND revoked_at IS NULL",
                    (now_iso, stored_reason, invite_id, account_id),
                )
                invite.email_complained_at = now_iso
                invite.email_bounce_type = "complaint"
                invite.email_bounce_reason = reason_clean
            else:  # hard
                upd = await self._db.execute(
                    "UPDATE invites SET "
                    "  email_bounced_at = ?, "
                    "  email_bounce_type = 'hard', "
                    "  email_bounce_reason = ? "
                    "WHERE id = ? AND account_id = ? "
                    "  AND used_by IS NULL AND revoked_at IS NULL",
                    (now_iso, stored_reason, invite_id, account_id),
                )
                invite.email_bounced_at = now_iso
                invite.email_bounce_type = "hard"
                invite.email_bounce_reason = reason_clean

            if upd.rowcount != 1:
                # Race-lost (used/revoked between SELECT and UPDATE)
                return None
            return invite

    async def clear_invite_soft_bounce(
        self, account_id: int, invite_id: int,
    ) -> Optional[Invite]:
        """A subsequent ``email.delivered`` event clears soft-bounce
        bookkeeping so the operator sees the successful redelivery
        instead of a stale 'Bounced' badge.  Hard bounces and spam
        complaints stay sticky — a hard-then-delivered sequence is
        a relay anomaly, not a recovery.

        Critically, this method ALSO resets the soft-bounce counter
        on rows where ``email_bounce_type`` is NULL — those are
        intermediate states (1-2 soft strikes that haven't crossed
        the 3-strike threshold yet) where a successful delivery
        legitimately ends the failing-attempt run.  Without this,
        a row at count=2 stays at 2 forever; the next failure
        promotes count→3 and immediately flips to bounced — which
        misleadingly reports 'Bounced after 3 attempts' when really
        only 1 actually failed after a successful redelivery between.

        Returns the cleared invite (or unchanged invite when the
        bounce was hard/complaint).  None for used/revoked rows.
        """
        async with self.transaction():
            cur = await self._db.execute(
                "SELECT * FROM invites "
                "WHERE id = ? AND account_id = ? "
                "  AND used_by IS NULL AND revoked_at IS NULL "
                "LIMIT 1",
                (invite_id, account_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            invite = self._row_to_invite(row)
            # Sticky bounces — never cleared by a delivered event.
            if invite.email_bounce_type in ("hard", "complaint"):
                return invite
            # Cleared cases: type IS NULL (intermediate, count 1 or 2)
            # OR type == 'soft' (>=3 strikes had flipped the permanent
            # flag).  Both reset to clean state.  The UPDATE is
            # unconditional but harmless on an already-clean row
            # (all-NULL SET on all-NULL row, 0 SET on 0).
            await self._db.execute(
                "UPDATE invites SET "
                "  email_bounced_at = NULL, "
                "  email_bounce_type = NULL, "
                "  email_bounce_reason = NULL, "
                "  email_soft_bounce_count = 0 "
                "WHERE id = ? AND account_id = ? "
                "  AND used_by IS NULL AND revoked_at IS NULL",
                (invite_id, account_id),
            )
            invite.email_bounced_at = None
            invite.email_bounce_type = None
            invite.email_bounce_reason = None
            invite.email_soft_bounce_count = 0
            return invite

    async def prune_email_webhook_events(self, days: int = 14) -> int:
        """Daily cleanup of the idempotency table.

        Resend's longest documented retry window is 10 hours, so any
        svix_id older than ~14 days is guaranteed not to be a
        legitimate retry.  Without this cleanup the table grows
        unboundedly — at 10k invite emails/month with healthy
        delivery + occasional bounces, that's ~50k rows/year
        per deploy.  Small but unbounded; INSERT-OR-IGNORE on a
        multi-million-row table is measurably slower than on a
        bounded one.

        Returns the deleted row count for logging.  Idempotent —
        running it twice in close succession is harmless.
        """
        cur = await self._db.execute(
            "DELETE FROM email_webhook_events "
            "WHERE created_at < datetime('now', ?)",
            (f'-{int(days)} days',),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def mark_email_webhook_event_seen(
        self, svix_id: str, event_type: str,
        account_id: Optional[int] = None,
        invite_id: Optional[int] = None,
    ) -> bool:
        """Idempotency gate for Resend webhook retries (5s, 5min,
        30min, 2h, 5h, 10h schedule = up to 6 deliveries of the same
        event).  Returns True if this is the FIRST time we've seen
        ``svix_id``; False if it's a retry we already processed.

        Pattern mirrors Stripe's ``mark_stripe_event_processed`` in
        capabilities/billing/stripe_client.py — INSERT OR IGNORE on a
        primary-key table is the cheapest atomic check-and-set.
        """
        if not svix_id:
            return True  # no idempotency key → process (best-effort)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = await self._db.execute(
            "INSERT OR IGNORE INTO email_webhook_events "
            "(svix_id, event_type, account_id, invite_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (svix_id, event_type, account_id, invite_id, now_iso),
        )
        await self._db.commit()
        return cur.rowcount == 1

    async def get_invite(self, code: str) -> Optional[Invite]:
        """Look up an invite by code.

        Hides revoked invites by default so EVERY redemption surface
        — the bot's /join, the cmd_start deep-link, the email-signup
        endpoint at interfaces/api/auth.py, the mini-app — inherits the
        revoke-check without each call-site having to remember it.
        Pass ``include_revoked=True`` only from admin paths (the
        operator panel "Show all" toggle).
        """
        cur = await self._db.execute(
            "SELECT * FROM invites WHERE code = ? AND revoked_at IS NULL",
            (code.upper().strip(),),
        )
        row = await cur.fetchone()
        return self._row_to_invite(row) if row else None

    async def get_invite_by_id(
        self, account_id: int, invite_id: int,
    ) -> Optional[Invite]:
        """Look up an invite by primary key, scoped to an account.

        Used by the admin revoke flow so the endpoint can capture the
        invite's role/created_by for the audit log BEFORE
        flipping it to revoked.  Includes revoked rows because the
        operator-side might be re-checking a row they just revoked.
        Cross-account access is prevented by the ``account_id`` clause.
        """
        cur = await self._db.execute(
            "SELECT * FROM invites WHERE id = ? AND account_id = ?",
            (invite_id, account_id),
        )
        row = await cur.fetchone()
        return self._row_to_invite(row) if row else None

    async def redeem_invite(self, code: str, telegram_id: int,
                            display_name: str = "") -> Optional[User]:
        """Redeem an invite code → create user and mark invite used.

        Returns the new User or None if code is invalid/expired/used/
        revoked.

        The entire flow (read invite → check user → create user → mark
        invite used) is wrapped in a transaction so two concurrent
        redemptions of the same code can't both create users.  The
        winning transaction commits; the loser sees the marked-used
        invite on its retry and returns None.

        TOCTOU on revoke: the final UPDATE includes ``WHERE used_by IS
        NULL AND revoked_at IS NULL`` and we check the rowcount.  If a
        racing revoke flipped the row between our get_invite read and
        this UPDATE, the WHERE clause matches zero rows; we raise to
        trigger transaction rollback so the user creation is also
        undone.  Without this guard, the previous code path admitted
        the user even after the operator's revoke had committed.
        """
        async with self.transaction():
            invite = await self.get_invite(code)
            if not invite or invite.is_used or invite.is_expired:
                return None

            # Check user not already registered
            existing = await self.get_user_by_telegram_id(telegram_id)
            if existing:
                return None  # already has an account

            # Create user → mark invite used → commit, all in this
            # transaction.  The users.telegram_id UNIQUE constraint
            # serializes concurrent redemptions: a parallel transaction
            # racing the same code will block on the users INSERT and
            # then fail with UniqueViolation, rolling back its whole
            # block (including its UPDATE on invites).
            user = await self.create_user(
                telegram_id=telegram_id,
                account_id=invite.account_id,
                role=Role.from_str(invite.role),
                truck_num=invite.truck_num,
                display_name=display_name,
            )

            cur = await self._db.execute(
                # used_by IS NULL: defends against a concurrent redeem
                # that won the users-insert race against us (shouldn't
                # happen because of the UNIQUE telegram_id, but still).
                # revoked_at IS NULL: defends against a racing operator
                # revoke that committed after we snapshotted the invite
                # row.  If the operator wins the race, this UPDATE
                # matches 0 rows and we raise to roll the whole
                # transaction (incl. the user we just created) back.
                "UPDATE invites SET used_by = ? "
                "WHERE id = ? AND used_by IS NULL AND revoked_at IS NULL",
                (user.id, invite.id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"Invite {invite.id} race lost (revoked or "
                    f"redeemed in parallel after snapshot)"
                )
            return user

    async def list_invites(
        self, account_id: int,
        pending_only: bool = True,
        include_revoked: bool = False,
    ) -> list[Invite]:
        """List invites for an account.

        Filters are orthogonal:
          - ``pending_only`` hides USED rows (``used_by IS NULL``).
          - ``include_revoked`` is the only way to surface revoked rows
            — they're hidden by default on every redemption surface AND
            on this admin read so a stale dashboard panel can never
            offer a "Copy" button on a dead link.  The operator
            console's "Show all" toggle should drive BOTH params.
        """
        q = "SELECT * FROM invites WHERE account_id = ?"
        params: list = [account_id]
        if pending_only:
            q += " AND used_by IS NULL"
        if not include_revoked:
            q += " AND revoked_at IS NULL"
        q += " ORDER BY created_at DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [self._row_to_invite(r) for r in rows]

    async def revoke_invite(
        self, account_id: int, invite_id: int,
    ) -> Optional[Invite]:
        """Mark an unused invite as revoked.

        Returns the revoked Invite row so the caller can write a
        forensic audit log entry (role / created_by).
        Returns ``None`` when no row was flipped — covers:
          - invite_id not found in this account (cross-account attempt
            included; the WHERE clause silently swallows it)
          - already used (a user redeemed it before we got here)
          - already revoked (double-revoke, idempotent)

        Mirrors users.revoke_user_session (users.py:300-348): SELECT
        first to capture the row, then UPDATE-with-guard so an
        in-flight redemption that completes between our SELECT and
        UPDATE doesn't get clobbered.  The redemption-side guard on
        revoked_at (see ``redeem_invite``) closes the symmetric race.

        The SELECT + UPDATE run inside ``self.transaction()`` so both
        statements land on the same pinned connection.  Without the
        transaction wrap each ``self._db.execute`` call acquires a
        fresh pool connection (see pg_adapter pool semantics), opening
        a window where another worker can flip ``used_by`` between
        our snapshot and our UPDATE.  The rowcount guard still
        catches the race, but we'd be racing across two connections
        rather than under one row-level lock.

        Tenant isolation is enforced TWICE: the SELECT and the UPDATE
        both carry ``AND account_id = ?``.  The UPDATE clause is
        load-bearing defense-in-depth — if RLS is ever disabled at the
        connection-GUC layer (Tier-4 rollback path), the WHERE clause
        is the only thing standing between a mistyped invite_id and
        another tenant's row.
        """
        async with self.transaction():
            cur = await self._db.execute(
                """SELECT * FROM invites
                    WHERE id = ?
                      AND account_id = ?
                      AND used_by IS NULL
                      AND revoked_at IS NULL
                    LIMIT 1""",
                (invite_id, account_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            invite = self._row_to_invite(row)
            now_iso = datetime.now(timezone.utc).isoformat()
            upd = await self._db.execute(
                "UPDATE invites SET revoked_at = ? "
                "WHERE id = ? AND account_id = ? "
                "  AND used_by IS NULL AND revoked_at IS NULL",
                (now_iso, invite.id, account_id),
            )
            if upd.rowcount != 1:
                # Lost the race to a concurrent redeem.  Don't write
                # an audit log claiming we revoked a row that's now
                # owned by a redeeming user — the caller's None-
                # return handler surfaces this as "Invite not found"
                # (same response as a truly-missing row, deliberately
                # uniform to avoid leaking which branch).
                return None
            invite.revoked_at = now_iso
            return invite

    async def extend_invite(
        self, account_id: int, invite_id: int, hours: int = 24,
    ) -> Optional[Invite]:
        """Push an unused invite's ``expires_at`` MONOTONICALLY forward
        by ``hours`` from NOW.  Guarantees the new deadline is never
        earlier than what was already set.

        Returns the freshly-extended Invite row so the caller can
        write a forensic audit log entry with the new deadline.
        Returns ``None`` when no row was flipped:
          - invite_id not found in this account (cross-account or
            truly-missing — uniform branch as revoke_invite)
          - already used (extend a redeemed invite is meaningless;
            the user already joined)
          - already revoked (extend an operator-killed invite would
            silently un-revoke it; refuse and force the operator to
            create a new code if they really want to re-onboard)

        Anchoring semantics (the tricky bit):
          new_expires = max(existing.expires_at,  now + hours)
          ──────────────────────────────────────────────────────
          - Common operator flow ("this expired yesterday, give it
            another 24h"): existing is in the past, ``now + 24h``
            wins.  Anchored to the present so the link is valid for
            the next 24h, not 24h after a deadline that's already
            past.
          - Adversarial / mis-click flow ("extend a fresh 100h invite
            by 1h"): existing > now+1h, existing wins.  Otherwise
            extend could PULL THE EXPIRY BACK and stealth-revoke a
            link the operator believed they were lengthening.  The
            ``max`` makes the operation provably monotonic — the
            invite's reachable lifetime cannot shrink.

        Crucially: the invite ``code`` is unchanged.  Any chat-history
        copy of the old link is reactivated rather than orphaned, which
        is the entire point of extend-over-recreate (see the design
        notes in interfaces/api/routes/admin.py for the revoke flow's
        sibling rationale).

        Mirrors revoke_invite's SELECT-then-UPDATE-with-guard shape so
        a concurrent redeem that wins the race can't be silently
        un-redeemed.  Transaction wrap pins both statements to the
        same connection.

        Storage-layer hours validation: the route's Pydantic Field has
        ``ge=1, le=720``, but this is a public mixin method that any
        future caller (CLI, ARQ job, bot wizard) inherits — so we
        re-check here as defence-in-depth.  ``hours <= 0`` would
        produce a no-op or backwards-time deadline depending on the
        max() branch and would silently stealth-revoke without an
        ``revoked_at`` audit trail.
        """
        if hours < 1 or hours > 720:
            raise ValueError(f"hours must be between 1 and 720, got {hours}")
        async with self.transaction():
            cur = await self._db.execute(
                """SELECT * FROM invites
                    WHERE id = ?
                      AND account_id = ?
                      AND used_by IS NULL
                      AND revoked_at IS NULL
                    LIMIT 1""",
                (invite_id, account_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            invite = self._row_to_invite(row)
            now = datetime.now(timezone.utc)
            candidate = now + timedelta(hours=hours)
            # Monotonic-forward guarantee: never pull the deadline
            # backwards.  Parsing existing.expires_at can fail on a
            # malformed legacy row — treat that as "no defence
            # needed, just use candidate" (the surrounding storage
            # contract requires ISO-8601, but the mixin shouldn't
            # crash a revive op on garbage).
            try:
                existing = datetime.fromisoformat(invite.expires_at)
                new_expires_dt = max(existing, candidate)
            except (TypeError, ValueError):
                new_expires_dt = candidate
            new_expires = new_expires_dt.isoformat()
            upd = await self._db.execute(
                "UPDATE invites SET expires_at = ? "
                "WHERE id = ? AND account_id = ? "
                "  AND used_by IS NULL AND revoked_at IS NULL",
                (new_expires, invite.id, account_id),
            )
            if upd.rowcount != 1:
                # Race-lost — same uniform 'Invite not found' shape
                # as revoke.  Don't write an audit log for a no-op.
                return None
            invite.expires_at = new_expires
            return invite
