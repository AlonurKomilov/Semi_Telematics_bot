"""
Database layer — async SQLite with repository-pattern abstractions.

Future-proof design:
  • All SQL lives in this one file — swap SQLite → PostgreSQL by
    replacing the engine, not the callers.
  • Every public function uses plain dicts / dataclasses — no ORM
    leakage into bot.py or samsara_client.py.
  • Schema is versioned via `schema_version` pragma.
  • All writes go through explicit helper functions (easy to wrap
    in a transaction / connection-pool later with asyncpg).

Tables
------
accounts       — one per subscribing company
companies      — Samsara company API keys owned by an account
users          — Telegram users linked to an account + role
invites        — one-time join codes (expire 24 h)
"""

from __future__ import annotations

import aiosqlite
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Schema version ──────────────────────────────────────────────
SCHEMA_VERSION = 1

# ─── Roles ────────────────────────────────────────────────────────

class Role(str, Enum):
    """User roles — ordered from most to least privileged."""
    OWNER       = "owner"
    ADMIN       = "admin"
    FLEET_MGR   = "fleet_manager"
    DISPATCHER  = "dispatcher"
    DRIVER      = "driver"

    @classmethod
    def from_str(cls, s: str) -> "Role":
        s = s.strip().lower()
        for r in cls:
            if r.value == s:
                return r
        raise ValueError(f"Unknown role: {s}")


# ─── Tier (for future billing) ───────────────────────────────────

class Tier(str, Enum):
    FREE  = "free"
    PRO   = "pro"
    ENTERPRISE = "enterprise"


# ─── Data classes ─────────────────────────────────────────────────

@dataclass
class Account:
    id: int
    name: str
    slug: str
    tier: str
    is_active: bool
    created_at: str

@dataclass
class Company:
    id: int
    account_id: int
    code: str
    display_name: str
    samsara_api_key: str
    active_days: int
    is_active: bool
    created_at: str

@dataclass
class User:
    id: int
    telegram_id: int
    account_id: int
    role: Role
    department: str
    truck_num: Optional[str]   # for driver role
    alerts_on: bool
    is_active: bool
    created_at: str

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def is_admin_or_above(self) -> bool:
        return self.role in (Role.OWNER, Role.ADMIN)

@dataclass
class AuthorizedChat:
    id: int
    account_id: int
    chat_id: int             # Telegram group/channel ID (negative)
    chat_title: str
    added_by: int            # user.id who authorized
    is_active: bool
    created_at: str

@dataclass
class Invite:
    id: int
    code: str
    account_id: int
    role: str
    department: str
    truck_num: Optional[str]
    created_by: int          # user.id
    expires_at: str
    used_by: Optional[int]   # user.id who redeemed
    created_at: str

    @property
    def is_expired(self) -> bool:
        exp = datetime.fromisoformat(self.expires_at)
        return datetime.now(timezone.utc) > exp

    @property
    def is_used(self) -> bool:
        return self.used_by is not None


# ─── Database ─────────────────────────────────────────────────────

class Database:
    """Async SQLite wrapper with typed helpers.

    Usage:
        db = Database("data/bot.db")
        await db.initialize()
        ...
        await db.close()
    """

    def __init__(self, path: str = "data/bot.db"):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Open DB and create / migrate schema."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        logger.info(f"Database ready at {self.path}")

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    # ── Schema ────────────────────────────────────────────────────

    async def _create_tables(self):
        # Migration: rename old 'organizations' table → 'companies'
        try:
            cur = await self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='organizations'"
            )
            if await cur.fetchone():
                await self._db.execute("ALTER TABLE organizations RENAME TO companies")
                await self._db.execute("DROP INDEX IF EXISTS idx_orgs_account_id")
                await self._db.commit()
                logger.info("Migrated table organizations → companies")
        except Exception as e:
            logger.debug(f"Table migration check: {e}")

        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                slug        TEXT    NOT NULL UNIQUE,
                tier        TEXT    NOT NULL DEFAULT 'free',
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                code            TEXT    NOT NULL,
                display_name    TEXT    NOT NULL DEFAULT '',
                samsara_api_key TEXT    NOT NULL,
                active_days     INTEGER NOT NULL DEFAULT 30,
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL,
                UNIQUE(account_id, code)
            );

            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                role        TEXT    NOT NULL DEFAULT 'fleet_manager',
                department  TEXT    NOT NULL DEFAULT 'general',
                truck_num   TEXT,
                alerts_on   INTEGER NOT NULL DEFAULT 0,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                role        TEXT    NOT NULL DEFAULT 'fleet_manager',
                department  TEXT    NOT NULL DEFAULT 'general',
                truck_num   TEXT,
                created_by  INTEGER NOT NULL REFERENCES users(id),
                expires_at  TEXT    NOT NULL,
                used_by     INTEGER REFERENCES users(id),
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS authorized_chats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                chat_id     INTEGER NOT NULL,
                chat_title  TEXT    NOT NULL DEFAULT '',
                added_by    INTEGER NOT NULL REFERENCES users(id),
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL,
                UNIQUE(account_id, chat_id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_telegram_id
                ON users(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_companies_account_id
                ON companies(account_id);
            CREATE INDEX IF NOT EXISTS idx_invites_code
                ON invites(code);
            CREATE INDEX IF NOT EXISTS idx_authorized_chats_chat_id
                ON authorized_chats(chat_id);
        """)
        await self._db.commit()

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _make_slug(name: str) -> str:
        """Generate URL-safe slug from company name."""
        slug = name.lower().strip()
        slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
        slug = slug.replace(" ", "-")
        # append short hash to avoid collisions
        h = hashlib.md5(f"{slug}{time.time()}".encode()).hexdigest()[:6]
        return f"{slug}-{h}"

    @staticmethod
    def _generate_invite_code() -> str:
        """Human-friendly 8-char code: XXXX-XXXX."""
        raw = secrets.token_hex(4).upper()
        return f"{raw[:4]}-{raw[4:]}"

    def _row_to_account(self, row) -> Account:
        return Account(
            id=row["id"], name=row["name"], slug=row["slug"],
            tier=row["tier"], is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def _row_to_company(self, row) -> Company:
        return Company(
            id=row["id"], account_id=row["account_id"],
            code=row["code"], display_name=row["display_name"],
            samsara_api_key=row["samsara_api_key"],
            active_days=row["active_days"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def _row_to_user(self, row) -> User:
        return User(
            id=row["id"], telegram_id=row["telegram_id"],
            account_id=row["account_id"],
            role=Role.from_str(row["role"]),
            department=row["department"],
            truck_num=row["truck_num"],
            alerts_on=bool(row["alerts_on"]),
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def _row_to_invite(self, row) -> Invite:
        return Invite(
            id=row["id"], code=row["code"],
            account_id=row["account_id"],
            role=row["role"], department=row["department"],
            truck_num=row["truck_num"],
            created_by=row["created_by"],
            expires_at=row["expires_at"],
            used_by=row["used_by"],
            created_at=row["created_at"],
        )

    def _row_to_authorized_chat(self, row) -> AuthorizedChat:
        return AuthorizedChat(
            id=row["id"], account_id=row["account_id"],
            chat_id=row["chat_id"], chat_title=row["chat_title"],
            added_by=row["added_by"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    # ══════════════════════════════════════════════════════════════
    # ACCOUNTS
    # ══════════════════════════════════════════════════════════════

    async def create_account(self, name: str, tier: str = "free") -> Account:
        """Create a new subscriber account."""
        now = self._now()
        slug = self._make_slug(name)
        cur = await self._db.execute(
            "INSERT INTO accounts (name, slug, tier, created_at) VALUES (?, ?, ?, ?)",
            (name, slug, tier, now),
        )
        await self._db.commit()
        return Account(id=cur.lastrowid, name=name, slug=slug,
                       tier=tier, is_active=True, created_at=now)

    async def get_account(self, account_id: int) -> Optional[Account]:
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        )
        row = await cur.fetchone()
        return self._row_to_account(row) if row else None

    async def get_account_by_slug(self, slug: str) -> Optional[Account]:
        cur = await self._db.execute(
            "SELECT * FROM accounts WHERE slug = ?", (slug,)
        )
        row = await cur.fetchone()
        return self._row_to_account(row) if row else None

    async def list_accounts(self, active_only: bool = True) -> list[Account]:
        q = "SELECT * FROM accounts"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY name"
        cur = await self._db.execute(q)
        rows = await cur.fetchall()
        return [self._row_to_account(r) for r in rows]

    async def update_account(self, account_id: int, **kwargs) -> bool:
        """Update account fields. Allowed keys: name, tier, is_active."""
        allowed = {"name", "tier", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [account_id]
        await self._db.execute(
            f"UPDATE accounts SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()
        return True

    # ══════════════════════════════════════════════════════════════
    # COMPANIES
    # ══════════════════════════════════════════════════════════════

    async def add_company(
        self, account_id: int, code: str,
        samsara_api_key: str, display_name: str = "",
        active_days: int = 30,
    ) -> Company:
        """Add a Samsara company to an account."""
        now = self._now()
        code = code.strip().upper()
        cur = await self._db.execute(
            """INSERT INTO companies
               (account_id, code, display_name, samsara_api_key, active_days, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, code, display_name, samsara_api_key, active_days, now),
        )
        await self._db.commit()
        return Company(
            id=cur.lastrowid, account_id=account_id, code=code,
            display_name=display_name, samsara_api_key=samsara_api_key,
            active_days=active_days, is_active=True, created_at=now,
        )

    async def get_account_companies(
        self, account_id: int, active_only: bool = True,
    ) -> list[Company]:
        """List all companies belonging to an account."""
        q = "SELECT * FROM companies WHERE account_id = ?"
        params: list = [account_id]
        if active_only:
            q += " AND is_active = 1"
        q += " ORDER BY code"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [self._row_to_company(r) for r in rows]

    async def get_company_by_code(
        self, account_id: int, code: str,
    ) -> Optional[Company]:
        cur = await self._db.execute(
            "SELECT * FROM companies WHERE account_id = ? AND code = ?",
            (account_id, code.upper()),
        )
        row = await cur.fetchone()
        return self._row_to_company(row) if row else None

    async def remove_company(self, company_id: int) -> bool:
        """Soft-delete a company."""
        await self._db.execute(
            "UPDATE companies SET is_active = 0 WHERE id = ?", (company_id,)
        )
        await self._db.commit()
        return True

    async def update_company(self, company_id: int, **kwargs) -> bool:
        """Update company fields. Allowed: display_name, samsara_api_key, active_days, is_active."""
        allowed = {"display_name", "samsara_api_key", "active_days", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [company_id]
        await self._db.execute(
            f"UPDATE companies SET {set_clause} WHERE id = ?", values,
        )
        await self._db.commit()
        return True

    # ══════════════════════════════════════════════════════════════
    # USERS
    # ══════════════════════════════════════════════════════════════

    async def create_user(
        self, telegram_id: int, account_id: int,
        role: Role = Role.FLEET_MGR,
        department: str = "general",
        truck_num: Optional[str] = None,
    ) -> User:
        """Register a Telegram user to an account."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO users
               (telegram_id, account_id, role, department, truck_num, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (telegram_id, account_id, role.value, department, truck_num, now),
        )
        await self._db.commit()
        return User(
            id=cur.lastrowid, telegram_id=telegram_id,
            account_id=account_id, role=role,
            department=department, truck_num=truck_num,
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
        """Update user fields. Allowed: role, department, truck_num, alerts_on, is_active."""
        allowed = {"role", "department", "truck_num", "alerts_on", "is_active"}
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

    async def remove_user(self, user_id: int) -> bool:
        """Soft-delete a user."""
        await self._db.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (user_id,)
        )
        await self._db.commit()
        return True

    # ══════════════════════════════════════════════════════════════
    # INVITES
    # ══════════════════════════════════════════════════════════════

    async def create_invite(
        self, account_id: int, created_by: int,
        role: Role = Role.FLEET_MGR,
        department: str = "general",
        truck_num: Optional[str] = None,
        hours: int = 24,
    ) -> Invite:
        """Generate a one-time invite code."""
        now = datetime.now(timezone.utc)
        expires = datetime(
            now.year, now.month, now.day, now.hour, now.minute, now.second,
            tzinfo=timezone.utc,
        )
        from datetime import timedelta
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

    async def redeem_invite(self, code: str, telegram_id: int) -> Optional[User]:
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

    # ══════════════════════════════════════════════════════════════
    # AUTHORIZED CHATS (groups / channels)
    # ══════════════════════════════════════════════════════════════

    async def add_authorized_chat(
        self, account_id: int, chat_id: int, chat_title: str, added_by: int,
    ) -> AuthorizedChat:
        """Authorize a group/channel for this account."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO authorized_chats
               (account_id, chat_id, chat_title, added_by, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, chat_id) DO UPDATE
               SET is_active = 1, chat_title = excluded.chat_title""",
            (account_id, chat_id, chat_title, added_by, now),
        )
        await self._db.commit()
        return AuthorizedChat(
            id=cur.lastrowid, account_id=account_id, chat_id=chat_id,
            chat_title=chat_title, added_by=added_by,
            is_active=True, created_at=now,
        )

    async def remove_authorized_chat(self, account_id: int, chat_id: int) -> bool:
        """Deauthorize a group/channel."""
        await self._db.execute(
            "UPDATE authorized_chats SET is_active = 0 WHERE account_id = ? AND chat_id = ?",
            (account_id, chat_id),
        )
        await self._db.commit()
        return True

    async def get_authorized_chats(self, account_id: int) -> list[AuthorizedChat]:
        """List all authorized chats for an account."""
        cur = await self._db.execute(
            "SELECT * FROM authorized_chats WHERE account_id = ? AND is_active = 1 ORDER BY chat_title",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_authorized_chat(r) for r in rows]

    async def is_chat_authorized(self, chat_id: int) -> bool:
        """Check if a group/channel is authorized by any active account."""
        cur = await self._db.execute(
            """SELECT 1 FROM authorized_chats ac
               JOIN accounts a ON a.id = ac.account_id
               WHERE ac.chat_id = ? AND ac.is_active = 1 AND a.is_active = 1
               LIMIT 1""",
            (chat_id,),
        )
        row = await cur.fetchone()
        return row is not None

    async def get_chat_account_id(self, chat_id: int) -> Optional[int]:
        """Get the account_id that owns an authorized chat."""
        cur = await self._db.execute(
            """SELECT ac.account_id FROM authorized_chats ac
               JOIN accounts a ON a.id = ac.account_id
               WHERE ac.chat_id = ? AND ac.is_active = 1 AND a.is_active = 1
               LIMIT 1""",
            (chat_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

