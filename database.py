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
organizations  — Samsara org API keys owned by an account
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
class Organization:
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
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                slug        TEXT    NOT NULL UNIQUE,
                tier        TEXT    NOT NULL DEFAULT 'free',
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS organizations (
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

            CREATE INDEX IF NOT EXISTS idx_users_telegram_id
                ON users(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_orgs_account_id
                ON organizations(account_id);
            CREATE INDEX IF NOT EXISTS idx_invites_code
                ON invites(code);
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

    def _row_to_org(self, row) -> Organization:
        return Organization(
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
    # ORGANIZATIONS
    # ══════════════════════════════════════════════════════════════

    async def add_organization(
        self, account_id: int, code: str,
        samsara_api_key: str, display_name: str = "",
        active_days: int = 30,
    ) -> Organization:
        """Add a Samsara org to an account."""
        now = self._now()
        code = code.strip().upper()
        cur = await self._db.execute(
            """INSERT INTO organizations
               (account_id, code, display_name, samsara_api_key, active_days, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, code, display_name, samsara_api_key, active_days, now),
        )
        await self._db.commit()
        return Organization(
            id=cur.lastrowid, account_id=account_id, code=code,
            display_name=display_name, samsara_api_key=samsara_api_key,
            active_days=active_days, is_active=True, created_at=now,
        )

    async def get_account_orgs(
        self, account_id: int, active_only: bool = True,
    ) -> list[Organization]:
        """List all orgs belonging to an account."""
        q = "SELECT * FROM organizations WHERE account_id = ?"
        params: list = [account_id]
        if active_only:
            q += " AND is_active = 1"
        q += " ORDER BY code"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [self._row_to_org(r) for r in rows]

    async def get_org_by_code(
        self, account_id: int, code: str,
    ) -> Optional[Organization]:
        cur = await self._db.execute(
            "SELECT * FROM organizations WHERE account_id = ? AND code = ?",
            (account_id, code.upper()),
        )
        row = await cur.fetchone()
        return self._row_to_org(row) if row else None

    async def remove_organization(self, org_id: int) -> bool:
        """Soft-delete an organization."""
        await self._db.execute(
            "UPDATE organizations SET is_active = 0 WHERE id = ?", (org_id,)
        )
        await self._db.commit()
        return True

    async def update_organization(self, org_id: int, **kwargs) -> bool:
        """Update org fields. Allowed: display_name, samsara_api_key, active_days, is_active."""
        allowed = {"display_name", "samsara_api_key", "active_days", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [org_id]
        await self._db.execute(
            f"UPDATE organizations SET {set_clause} WHERE id = ?", values,
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

    async def count_all_orgs(self, active_only: bool = True) -> int:
        """Count all orgs across all accounts."""
        q = "SELECT COUNT(*) FROM organizations"
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
        total_orgs = await self.count_all_orgs(active_only=False)
        active_orgs = await self.count_all_orgs(active_only=True)
        alert_subs = await self.get_all_alert_subscribers()

        # Per-account breakdown
        account_details = []
        for acct in active_accounts:
            users = await self.list_account_users(acct.id)
            orgs = await self.get_account_orgs(acct.id)
            account_details.append({
                "account": acct,
                "users": users,
                "orgs": orgs,
            })

        return {
            "total_accounts": len(accounts),
            "active_accounts": len(active_accounts),
            "inactive_accounts": len(inactive_accounts),
            "total_users": total_users,
            "active_users": active_users,
            "total_orgs": total_orgs,
            "active_orgs": active_orgs,
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


# ══════════════════════════════════════════════════════════════════
# SEED — migrate existing .env config into the database
# ══════════════════════════════════════════════════════════════════

async def seed_from_env(db: Database):
    """One-time migration: read current .env vars and populate database.

    Creates:
      • One account ("Semi Telematics" or from ACCOUNT_NAME env)
      • Organizations from SAMSARA_ORGS
      • Users from TELEGRAM_CHAT_IDS (all as owners)

    Safe to call multiple times — skips if data already exists.
    """
    from dotenv import load_dotenv
    load_dotenv()

    accounts = await db.list_accounts()
    if accounts:
        logger.info("Seed: database already has accounts, skipping.")
        return

    # Create default account
    account_name = os.getenv("ACCOUNT_NAME", "Semi Telematics")
    account = await db.create_account(account_name)
    logger.info(f"Seed: created account '{account.name}' (id={account.id})")

    # Add organizations
    org_raw = os.getenv("SAMSARA_ORGS", "")
    active_days = int(os.getenv("ACTIVE_VEHICLE_GPS_DAYS", "30"))

    # Known display names (migrated from old hardcoded dict)
    display_names = {
        "PTG": "Premier Trucking Group",
        "CFT": "Cargo Freight Trucking",
        "OSY": "OSY Group",
        "RMR": "RMR Transportation",
    }

    for entry in org_raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        code, key = entry.split(":", 1)
        code = code.strip().upper()
        key = key.strip()
        if code and key:
            dn = display_names.get(code, code)
            org = await db.add_organization(
                account.id, code, key,
                display_name=dn, active_days=active_days,
            )
            logger.info(f"Seed: added org {code} ({dn})")

    # Register existing chat IDs as owner users
    # Skip system owner IDs — they are platform admins, not customers
    sys_owner_raw = os.getenv("SYSTEM_OWNER_IDS", "")
    sys_owner_ids: set[int] = set()
    for sid in sys_owner_raw.split(","):
        sid = sid.strip()
        if sid:
            try:
                sys_owner_ids.add(int(sid))
            except ValueError:
                pass

    chat_ids_raw = os.getenv("TELEGRAM_CHAT_IDS", "")
    for cid in chat_ids_raw.split(","):
        cid = cid.strip()
        if not cid:
            continue
        try:
            tid = int(cid)
            if tid in sys_owner_ids:
                logger.info(f"Seed: skipping {tid} (system owner, not a customer)")
                continue
            user = await db.create_user(
                telegram_id=tid, account_id=account.id,
                role=Role.OWNER, department="management",
            )
            logger.info(f"Seed: registered user {tid} as owner (id={user.id})")
        except Exception as e:
            logger.warning(f"Seed: failed to add user {cid}: {e}")

    logger.info("Seed complete.")
