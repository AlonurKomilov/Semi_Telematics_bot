"""Users CRUD mixin — includes subscriber queries and system stats."""

from __future__ import annotations

from typing import Optional

from .models import Role, User


class UsersMixin:

    async def create_user(
        self, telegram_id: int, account_id: int,
        role: Role = Role.FLEET_MGR,
        department: str = "general",
        truck_num: Optional[str] = None,
        display_name: str = "",
    ) -> User:
        """Register a Telegram user to an account."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO users
               (telegram_id, account_id, role, department, truck_num, display_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, account_id, role.value, department, truck_num, display_name, now),
        )
        await self._db.commit()
        return User(
            id=cur.lastrowid, telegram_id=telegram_id,
            account_id=account_id, role=role,
            department=department, truck_num=truck_num,
            display_name=display_name,
            alerts_on=False, is_active=True, created_at=now,
        )

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Look up a user by their Telegram chat/user ID."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Look up a user by email address."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (email.lower().strip(),),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def get_user_by_email_in_account(
        self, email: str, account_id: int,
    ) -> Optional[User]:
        """Look up a user by email within a specific account."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE email = ? AND account_id = ? AND is_active = 1",
            (email.lower().strip(), account_id),
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def set_user_email_password(
        self, user_id: int, email: str, password_hash: str
    ) -> None:
        """Set email and password hash for an existing user."""
        await self._db.execute(
            "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
            (email.lower().strip(), password_hash, user_id),
        )
        await self._db.commit()

    async def create_user_with_email(
        self, email: str, password_hash: str, account_id: int,
        role: Role = Role.FLEET_MGR, department: str = "general",
        display_name: str = "",
    ) -> User:
        """Create a new user with email+password (no Telegram ID yet)."""
        now = self._now()
        import hashlib
        placeholder_tid = -abs(int(hashlib.sha256(email.encode()).hexdigest()[:15], 16))
        cur = await self._db.execute(
            """INSERT INTO users
               (telegram_id, account_id, role, department, display_name,
                email, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (placeholder_tid, account_id, role.value, department,
             display_name, email.lower().strip(), password_hash, now),
        )
        await self._db.commit()
        return User(
            id=cur.lastrowid, telegram_id=placeholder_tid,
            account_id=account_id, role=role,
            department=department, truck_num=None,
            display_name=display_name, email=email.lower().strip(),
            password_hash=password_hash,
            alerts_on=False, is_active=True, created_at=now,
        )

    async def link_telegram_to_user(self, user_id: int, telegram_id: int) -> None:
        """Link a real Telegram ID to an email-registered user."""
        await self._db.execute(
            "UPDATE users SET telegram_id = ? WHERE id = ?",
            (telegram_id, user_id),
        )
        await self._db.commit()

    async def get_user(self, user_id: int) -> Optional[User]:
        cur = await self._db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def list_account_users(self, account_id: int) -> list[User]:
        cur = await self._db.execute(
            "SELECT * FROM users WHERE account_id = ? AND is_active = 1 ORDER BY role, created_at",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def get_account_admins(self, account_id: int) -> list[User]:
        """Return owners and admins for an account (for error notifications)."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE account_id = ? AND is_active = 1 "
            "AND role IN ('owner', 'admin') ORDER BY role",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def update_user(self, user_id: int, **kwargs) -> bool:
        """Update user fields. Allowed: role, department, truck_num, alerts_on, is_active, alert_*."""
        allowed = {"role", "department", "truck_num", "alerts_on", "is_active",
                   "alert_faults", "alert_health", "alert_fuel", "alert_geofence",
                   "alert_events", "alert_parking",
                   "alert_camera",
                   "ai_fault", "ai_health", "ai_fuel", "ai_events", "ai_parking",
                   "quiet_start", "quiet_end", "timezone", "display_name",
                   "language"}
        updates = {}
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k == "role" and isinstance(v, Role):
                v = v.value
            updates[k] = v
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        await self._db.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", values,
        )
        await self._db.commit()
        return True

    async def toggle_alerts(self, telegram_id: int) -> bool:
        """Toggle alerts_on for a user. Returns new state."""
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            return False
        new_val = not user.alerts_on
        await self.update_user(user.id, alerts_on=new_val)
        return new_val

    async def get_alert_subscribers(self, account_id: int) -> list[User]:
        """Users with alerts enabled for an account."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE account_id = ? AND alerts_on = 1 AND is_active = 1",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def get_all_alert_subscribers(self) -> list[User]:
        """All users with alerts enabled (across all accounts)."""
        cur = await self._db.execute(
            "SELECT * FROM users WHERE alerts_on = 1 AND is_active = 1",
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def get_typed_alert_subscribers(
        self, account_id: int, alert_type: str,
    ) -> list[User]:
        """Users subscribed to a specific alert type for an account.

        alert_type: 'faults', 'health', 'fuel', 'geofence', or 'events'
        """
        col = f"alert_{alert_type}"
        if col not in ("alert_faults", "alert_health", "alert_fuel", "alert_geofence", "alert_events", "alert_parking", "alert_camera"):
            return []
        cur = await self._db.execute(
            f"SELECT * FROM users WHERE account_id = ? AND alerts_on = 1"
            f" AND {col} = 1 AND is_active = 1",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def get_all_typed_subscribers(self, alert_type: str) -> list[User]:
        """All users subscribed to a specific alert type (across all accounts)."""
        col = f"alert_{alert_type}"
        if col not in ("alert_faults", "alert_health", "alert_fuel", "alert_geofence", "alert_events", "alert_parking", "alert_camera"):
            return []
        cur = await self._db.execute(
            f"SELECT * FROM users WHERE alerts_on = 1 AND {col} = 1 AND is_active = 1",
        )
        rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def count_all_users(self, active_only: bool = True) -> int:
        """Count all users across all accounts."""
        q = "SELECT COUNT(*) FROM users"
        if active_only:
            q += " WHERE is_active = 1"
        cur = await self._db.execute(q)
        row = await cur.fetchone()
        return row[0] if row else 0

    async def count_all_companies(self, active_only: bool = True) -> int:
        """Count all companies across all accounts."""
        q = "SELECT COUNT(*) FROM companies"
        if active_only:
            q += " WHERE is_active = 1"
        cur = await self._db.execute(q)
        row = await cur.fetchone()
        return row[0] if row else 0

    async def get_system_stats(self) -> dict:
        """Return bot-wide stats for the system owner dashboard."""
        accounts = await self.list_accounts(active_only=False)
        active_accounts = [a for a in accounts if a.is_active]
        inactive_accounts = [a for a in accounts if not a.is_active]

        total_users = await self.count_all_users(active_only=False)
        active_users = await self.count_all_users(active_only=True)
        total_companies = await self.count_all_companies(active_only=False)
        active_companies = await self.count_all_companies(active_only=True)
        alert_subs = await self.get_all_alert_subscribers()

        # Per-account breakdown
        account_details = []
        for acct in active_accounts:
            users = await self.list_account_users(acct.id)
            companies = await self.get_account_companies(acct.id)
            account_details.append({
                "account": acct,
                "users": users,
                "companies": companies,
            })

        return {
            "total_accounts": len(accounts),
            "active_accounts": len(active_accounts),
            "inactive_accounts": len(inactive_accounts),
            "total_users": total_users,
            "active_users": active_users,
            "total_companies": total_companies,
            "active_companies": active_companies,
            "alert_subscribers": len(alert_subs),
            "account_details": account_details,
        }

    async def get_system_extended_stats(self) -> dict:
        """Return extended stats for sysowner: AI usage, alerts, DB size, roles."""
        stats: dict = {}

        # Users per role
        cur = await self._db.execute(
            "SELECT role, COUNT(*) FROM users WHERE is_active=1 GROUP BY role"
        )
        stats["roles"] = {r[0]: r[1] for r in await cur.fetchall()}

        # AI usage per account (last 30 days)
        cur = await self._db.execute(
            "SELECT a.name, u.model, COUNT(*) as calls, "
            "COALESCE(SUM(u.total_tokens),0) as tokens "
            "FROM ai_usage u JOIN accounts a ON a.id = u.account_id "
            "WHERE u.created_at > datetime('now', '-30 days') "
            "GROUP BY a.name, u.model ORDER BY tokens DESC"
        )
        stats["ai_usage"] = [
            {"account": r[0], "model": r[1], "calls": r[2], "tokens": r[3]}
            for r in await cur.fetchall()
        ]

        # AI totals
        cur = await self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens),0) FROM ai_usage"
        )
        row = await cur.fetchone()
        stats["ai_total_calls"] = row[0]
        stats["ai_total_tokens"] = row[1]

        # Alert stats (last 7 days)
        cur = await self._db.execute(
            "SELECT alert_type, COUNT(*) as total, "
            "SUM(CASE WHEN acknowledged_at IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) "
            "FROM alert_acknowledgments "
            "WHERE created_at > datetime('now', '-7 days') "
            "GROUP BY alert_type ORDER BY total DESC"
        )
        stats["alerts_7d"] = [
            {"type": r[0], "total": r[1], "acked": r[2], "expired": r[3]}
            for r in await cur.fetchall()
        ]

        # Total alerts all time
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM alert_acknowledgments"
        )
        stats["alerts_total"] = (await cur.fetchone())[0]

        # Active (unacked) alerts right now
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM alert_acknowledgments "
            "WHERE acknowledged_at IS NULL AND status = 'active'"
        )
        stats["alerts_active"] = (await cur.fetchone())[0]

        # Maintenance tasks
        cur = await self._db.execute(
            "SELECT status, COUNT(*) FROM maintenance_tasks GROUP BY status"
        )
        stats["maintenance"] = {r[0]: r[1] for r in await cur.fetchall()}

        # Auto-report subscriptions
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM digest_subscriptions"
        )
        stats["digest_subs"] = (await cur.fetchone())[0]

        # Audit log count
        cur = await self._db.execute("SELECT COUNT(*) FROM audit_log")
        stats["audit_entries"] = (await cur.fetchone())[0]

        # DB file size
        import os
        db_path = getattr(self, 'path', "data/bot.db")
        db_size = 0
        for suffix in ("", "-wal", "-shm"):
            path = f"{db_path}{suffix}"
            if os.path.exists(path):
                db_size += os.path.getsize(path)
        stats["db_size_mb"] = round(db_size / 1024 / 1024, 1)

        return stats

    async def remove_user(self, user_id: int) -> bool:
        """Soft-delete a user."""
        await self._db.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (user_id,)
        )
        await self._db.commit()
        return True
