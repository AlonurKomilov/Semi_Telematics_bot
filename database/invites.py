"""Invites CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import Role, Invite, User


class InvitesMixin:

    async def create_invite(
        self, account_id: int, created_by: int,
        role: Role = Role.FLEET_MGR,
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
        )

    async def get_invite(self, code: str) -> Optional[Invite]:
        cur = await self._db.execute(
            "SELECT * FROM invites WHERE code = ?", (code.upper().strip(),)
        )
        row = await cur.fetchone()
        return self._row_to_invite(row) if row else None

    async def redeem_invite(self, code: str, telegram_id: int,
                            display_name: str = "") -> Optional[User]:
        """Redeem an invite code → create user and mark invite used.

        Returns the new User or None if code is invalid/expired/used.
        """
        invite = await self.get_invite(code)
        if not invite or invite.is_used or invite.is_expired:
            return None

        # Check user not already registered
        existing = await self.get_user_by_telegram_id(telegram_id)
        if existing:
            return None  # already has an account

        # Create user
        user = await self.create_user(
            telegram_id=telegram_id,
            account_id=invite.account_id,
            role=Role.from_str(invite.role),
            department=invite.department,
            truck_num=invite.truck_num,
            display_name=display_name,
        )

        # Mark invite as used
        await self._db.execute(
            "UPDATE invites SET used_by = ? WHERE id = ?",
            (user.id, invite.id),
        )
        await self._db.commit()
        return user

    async def list_invites(
        self, account_id: int, pending_only: bool = True,
    ) -> list[Invite]:
        """List invites for an account."""
        q = "SELECT * FROM invites WHERE account_id = ?"
        params: list = [account_id]
        if pending_only:
            q += " AND used_by IS NULL"
        q += " ORDER BY created_at DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [self._row_to_invite(r) for r in rows]
