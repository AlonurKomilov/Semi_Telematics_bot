"""Invites CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Role, Invite, User


class InvitesMixin:

    async def create_invite(
        self, account_id: int, created_by: int,
        role: Role = Role.FLEET,
        department: str = "general",
        truck_num: Optional[str] = None,
        hours: int = 24,
    ) -> Invite:
        """Generate a one-time invite code."""
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=hours)

        code = self._generate_invite_code()
        now_str = now.isoformat()
        exp_str = expires.isoformat()

        cur = await self._db.execute(
            """INSERT INTO invites
               (code, account_id, role, department, truck_num,
                created_by, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, account_id, role.value, department, truck_num,
             created_by, exp_str, now_str),
        )
        await self._db.commit()
        return Invite(
            id=cur.lastrowid, code=code, account_id=account_id,
            role=role.value, department=department, truck_num=truck_num,
            created_by=created_by, expires_at=exp_str,
            used_by=None, created_at=now_str,
            revoked_at=None,
        )

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
        invite's role/department/created_by for the audit log BEFORE
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
                department=invite.department,
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
        forensic audit log entry (role / department / created_by).
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
