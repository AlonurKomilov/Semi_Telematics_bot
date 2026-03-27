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
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from encryption import encrypt as _enc, decrypt as _dec

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
    # Per-type alert preferences (all default ON when alerts_on is True)
    display_name: str = ""
    alert_faults: bool = True
    alert_health: bool = True
    alert_fuel: bool = True
    alert_geofence: bool = True
    ai_fault: bool = False      # Proactive AI on fault alerts
    ai_health: bool = False     # Proactive AI on health alerts
    ai_fuel: bool = False       # Proactive AI on fuel alerts
    alert_events: bool = True   # Safety event alerts
    ai_events: bool = False     # Proactive AI on event alerts
    alert_camera: bool = True   # Camera check alerts
    quiet_start: Optional[int] = None   # DND start hour (0-23)
    quiet_end: Optional[int] = None     # DND end hour (0-23)
    timezone: str = "America/New_York"
    language: str = "en"                # UI language (en/es/ru/uk/fr)

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def is_admin_or_above(self) -> bool:
        return self.role in (Role.OWNER, Role.ADMIN)

    def wants_alert(self, alert_type: str) -> bool:
        """Check if user wants a specific alert type.

        alert_type: 'faults', 'health', 'fuel', 'geofence', or 'events'
        """
        if not self.alerts_on:
            return False
        return getattr(self, f"alert_{alert_type}", True)

    def is_in_quiet_hours(self) -> bool:
        """Check if the user is currently in their DND quiet hours.

        Returns False if quiet hours are not configured.
        """
        if self.quiet_start is None or self.quiet_end is None:
            return False
        try:
            from zoneinfo import ZoneInfo
            user_tz = ZoneInfo(self.timezone)
            local_hour = datetime.now(timezone.utc).astimezone(user_tz).hour
            start, end = self.quiet_start, self.quiet_end
            if start <= end:
                return start <= local_hour < end
            else:
                # Wraps midnight, e.g., 22:00 - 06:00
                return local_hour >= start or local_hour < end
        except Exception:
            return False

    @property
    def label(self) -> str:
        """Human-readable name for UI display, falls back to telegram_id."""
        return self.display_name or str(self.telegram_id)

    @property
    def linked_label(self) -> str:
        """Clickable name linking to the user's Telegram profile."""
        name = self.display_name or str(self.telegram_id)
        return f"<a href='tg://user?id={self.telegram_id}'>{name}</a>"

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
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA synchronous=NORMAL")
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

            CREATE TABLE IF NOT EXISTS maintenance_tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                company_code    TEXT    NOT NULL DEFAULT '',
                vehicle_id      TEXT    NOT NULL DEFAULT '',
                vehicle_name    TEXT    NOT NULL DEFAULT '',
                task_type       TEXT    NOT NULL DEFAULT 'custom',
                description     TEXT    NOT NULL DEFAULT '',
                due_date        TEXT,
                due_miles       REAL,
                status          TEXT    NOT NULL DEFAULT 'pending',
                created_by      INTEGER NOT NULL,
                created_at      TEXT    NOT NULL,
                completed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS fuel_entries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                company_code    TEXT    NOT NULL DEFAULT '',
                vehicle_id      TEXT    NOT NULL DEFAULT '',
                vehicle_name    TEXT    NOT NULL DEFAULT '',
                gallons         REAL    NOT NULL DEFAULT 0,
                price_per_gallon REAL   NOT NULL DEFAULT 0,
                total_cost      REAL    NOT NULL DEFAULT 0,
                odometer_miles  REAL    NOT NULL DEFAULT 0,
                date            TEXT    NOT NULL,
                created_by      INTEGER NOT NULL,
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS digest_subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                frequency   TEXT    NOT NULL DEFAULT 'daily',
                send_hour   INTEGER NOT NULL DEFAULT 7,
                timezone    TEXT    NOT NULL DEFAULT 'UTC',
                report_type TEXT    NOT NULL DEFAULT 'faults',
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL,
                UNIQUE(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_telegram_id
                ON users(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_companies_account_id
                ON companies(account_id);
            CREATE INDEX IF NOT EXISTS idx_invites_code
                ON invites(code);
            CREATE INDEX IF NOT EXISTS idx_authorized_chats_chat_id
                ON authorized_chats(chat_id);
            CREATE INDEX IF NOT EXISTS idx_maintenance_account
                ON maintenance_tasks(account_id, status);
            CREATE INDEX IF NOT EXISTS idx_fuel_entries_account
                ON fuel_entries(account_id, vehicle_name);
            CREATE INDEX IF NOT EXISTS idx_digest_subs_active
                ON digest_subscriptions(is_active, send_hour);

            CREATE TABLE IF NOT EXISTS account_settings (
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                key         TEXT    NOT NULL,
                value       TEXT    NOT NULL DEFAULT '',
                updated_at  TEXT    NOT NULL,
                PRIMARY KEY (account_id, key)
            );

            CREATE TABLE IF NOT EXISTS alert_acknowledgments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                alert_type      TEXT    NOT NULL DEFAULT 'fault',
                vehicle_id      TEXT    NOT NULL DEFAULT '',
                vehicle_name    TEXT    NOT NULL DEFAULT '',
                alert_key       TEXT    NOT NULL DEFAULT '',
                message_id      INTEGER NOT NULL DEFAULT 0,
                chat_id         INTEGER NOT NULL DEFAULT 0,
                sent_to         INTEGER NOT NULL DEFAULT 0,
                acknowledged_by INTEGER,
                acknowledged_at TEXT,
                escalation_level INTEGER NOT NULL DEFAULT 0,
                next_escalation TEXT,
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                user_id     INTEGER,
                action      TEXT    NOT NULL,
                target_type TEXT    NOT NULL DEFAULT '',
                target_id   TEXT    NOT NULL DEFAULT '',
                details     TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_usage (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                user_id         INTEGER NOT NULL DEFAULT 0,
                model           TEXT    NOT NULL DEFAULT '',
                request_type    TEXT    NOT NULL DEFAULT '',
                prompt_tokens   INTEGER NOT NULL DEFAULT 0,
                reply_tokens    INTEGER NOT NULL DEFAULT 0,
                total_tokens    INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ai_usage_account
                ON ai_usage(account_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_alert_ack_pending
                ON alert_acknowledgments(acknowledged_at, next_escalation);
            CREATE INDEX IF NOT EXISTS idx_audit_account
                ON audit_log(account_id, created_at);

            CREATE TABLE IF NOT EXISTS dnd_alert_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL REFERENCES accounts(id),
                telegram_id     INTEGER NOT NULL,
                alert_type      TEXT    NOT NULL DEFAULT 'fault',
                vehicle_name    TEXT    NOT NULL DEFAULT '',
                alert_text      TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL,
                delivered        INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_dnd_queue_pending
                ON dnd_alert_queue(telegram_id, delivered);

            CREATE TABLE IF NOT EXISTS alert_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id        INTEGER NOT NULL REFERENCES accounts(id),
                alert_type        TEXT    NOT NULL,
                vehicle_id        TEXT    NOT NULL,
                vehicle_name      TEXT    NOT NULL DEFAULT '',
                chat_id           INTEGER NOT NULL,
                message_id        INTEGER NOT NULL DEFAULT 0,
                occurrence_count  INTEGER NOT NULL DEFAULT 1,
                first_seen        TEXT    NOT NULL,
                last_seen         TEXT    NOT NULL,
                last_detail       TEXT    NOT NULL DEFAULT '',
                status            TEXT    NOT NULL DEFAULT 'active'
            );
            CREATE INDEX IF NOT EXISTS idx_alert_history_active
                ON alert_history(account_id, alert_type, vehicle_id, chat_id, status);
        """)
        await self._db.commit()

        # ── Migration: add per-type alert preference columns ─────
        await self._migrate_alert_prefs()
        await self._migrate_user_quiet_hours()
        await self._migrate_digest_timezone()
        await self._migrate_digest_report_type()
        await self._migrate_user_display_name()
        await self._migrate_alert_ack_status()
        await self._migrate_user_language()
        await self._migrate_camera_checks_table()

    async def _migrate_alert_prefs(self):
        """Add alert_faults/health/fuel/geofence columns if missing."""
        new_cols = [
            ("alert_faults", "INTEGER NOT NULL DEFAULT 1"),
            ("alert_health", "INTEGER NOT NULL DEFAULT 1"),
            ("alert_fuel", "INTEGER NOT NULL DEFAULT 1"),
            ("alert_geofence", "INTEGER NOT NULL DEFAULT 1"),
            ("ai_fault", "INTEGER NOT NULL DEFAULT 0"),
            ("ai_health", "INTEGER NOT NULL DEFAULT 0"),
            ("ai_fuel", "INTEGER NOT NULL DEFAULT 0"),
            ("alert_events", "INTEGER NOT NULL DEFAULT 1"),
            ("ai_events", "INTEGER NOT NULL DEFAULT 0"),
            ("alert_camera", "INTEGER NOT NULL DEFAULT 1"),
        ]
        for col_name, col_def in new_cols:
            try:
                await self._db.execute(
                    f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
                )
                await self._db.commit()
                logger.info(f"Added column users.{col_name}")
            except Exception:
                pass  # column already exists

    async def _migrate_user_quiet_hours(self):
        """Add quiet_start, quiet_end, timezone columns to users."""
        new_cols = [
            ("quiet_start", "INTEGER"),           # hour 0-23, NULL = no DND
            ("quiet_end", "INTEGER"),             # hour 0-23
            ("timezone", "TEXT NOT NULL DEFAULT 'America/New_York'"),
        ]
        for col_name, col_def in new_cols:
            try:
                await self._db.execute(
                    f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
                )
                await self._db.commit()
                logger.info(f"Added column users.{col_name}")
            except Exception:
                pass

    async def _migrate_digest_timezone(self):
        """Ensure digest_subscriptions has timezone column."""
        try:
            await self._db.execute(
                "ALTER TABLE digest_subscriptions ADD COLUMN timezone TEXT NOT NULL DEFAULT 'America/New_York'"
            )
            await self._db.commit()
            logger.info("Added column digest_subscriptions.timezone")
        except Exception:
            pass

    async def _migrate_digest_report_type(self):
        """Ensure digest_subscriptions has report_type column."""
        try:
            await self._db.execute(
                "ALTER TABLE digest_subscriptions ADD COLUMN report_type TEXT NOT NULL DEFAULT 'faults'"
            )
            await self._db.commit()
            logger.info("Added column digest_subscriptions.report_type")
        except Exception:
            pass

    async def _migrate_user_display_name(self):
        """Add display_name column to users table."""
        try:
            await self._db.execute(
                "ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()
            logger.info("Added column users.display_name")
        except Exception:
            pass

    async def _migrate_alert_ack_status(self):
        """Add status column to alert_acknowledgments table."""
        try:
            await self._db.execute(
                "ALTER TABLE alert_acknowledgments ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
            await self._db.commit()
            logger.info("Added column alert_acknowledgments.status")
        except Exception:
            pass  # column already exists

    async def _migrate_user_language(self):
        """Add language column to users table."""
        try:
            await self._db.execute(
                "ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'en'"
            )
            await self._db.commit()
            logger.info("Added column users.language")
        except Exception:
            pass  # column already exists

    async def _migrate_camera_checks_table(self):
        """Create camera_checks table for history tracking."""
        try:
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS camera_checks (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id    INTEGER NOT NULL REFERENCES accounts(id),
                    vehicle_id    TEXT    NOT NULL DEFAULT '',
                    vehicle_name  TEXT    NOT NULL DEFAULT '',
                    camera_type   TEXT    NOT NULL DEFAULT 'forward',
                    status        TEXT    NOT NULL DEFAULT 'OK',
                    obstruction   TEXT    NOT NULL DEFAULT 'none',
                    alignment     TEXT    NOT NULL DEFAULT 'centered',
                    quality       TEXT    NOT NULL DEFAULT 'good',
                    summary       TEXT    NOT NULL DEFAULT '',
                    checked_at    TEXT    NOT NULL
                )
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_camera_checks_account
                    ON camera_checks(account_id, checked_at DESC)
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_camera_checks_vehicle
                    ON camera_checks(account_id, vehicle_id, checked_at DESC)
            """)
            await self._db.commit()
        except Exception:
            pass  # already exists

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
        # append short random suffix to avoid collisions
        h = secrets.token_hex(3)
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
            samsara_api_key=_dec(row["samsara_api_key"]),
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
            display_name=row["display_name"] if "display_name" in row.keys() else "",
            alert_faults=bool(row["alert_faults"]) if "alert_faults" in row.keys() else True,
            alert_health=bool(row["alert_health"]) if "alert_health" in row.keys() else True,
            alert_fuel=bool(row["alert_fuel"]) if "alert_fuel" in row.keys() else True,
            alert_geofence=bool(row["alert_geofence"]) if "alert_geofence" in row.keys() else True,
            ai_fault=bool(row["ai_fault"]) if "ai_fault" in row.keys() else False,
            ai_health=bool(row["ai_health"]) if "ai_health" in row.keys() else False,
            ai_fuel=bool(row["ai_fuel"]) if "ai_fuel" in row.keys() else False,
            alert_events=bool(row["alert_events"]) if "alert_events" in row.keys() else True,
            ai_events=bool(row["ai_events"]) if "ai_events" in row.keys() else False,
            alert_camera=bool(row["alert_camera"]) if "alert_camera" in row.keys() else True,
            quiet_start=row["quiet_start"] if "quiet_start" in row.keys() else None,
            quiet_end=row["quiet_end"] if "quiet_end" in row.keys() else None,
            timezone=row["timezone"] if "timezone" in row.keys() else "America/New_York",
            language=row["language"] if "language" in row.keys() else "en",
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
        encrypted_key = _enc(samsara_api_key)
        cur = await self._db.execute(
            """INSERT INTO companies
               (account_id, code, display_name, samsara_api_key, active_days, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, code, display_name, encrypted_key, active_days, now),
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
        if "samsara_api_key" in updates:
            updates["samsara_api_key"] = _enc(updates["samsara_api_key"])
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
                   "alert_events",
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
        if col not in ("alert_faults", "alert_health", "alert_fuel", "alert_geofence", "alert_events"):
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
        if col not in ("alert_faults", "alert_health", "alert_fuel", "alert_geofence", "alert_events", "alert_camera"):
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

    # ══════════════════════════════════════════════════════════════
    # MAINTENANCE TASKS
    # ══════════════════════════════════════════════════════════════

    async def add_maintenance_task(
        self, account_id: int, company_code: str,
        vehicle_name: str, task_type: str, description: str,
        due_date: Optional[str] = None, due_miles: Optional[float] = None,
        created_by: int = 0, vehicle_id: str = "",
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO maintenance_tasks
               (account_id, company_code, vehicle_id, vehicle_name,
                task_type, description, due_date, due_miles, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             task_type, description, due_date, due_miles, created_by, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_maintenance_tasks(
        self, account_id: int, status: Optional[str] = None,
        vehicle_name: Optional[str] = None,
    ) -> list[dict]:
        q = "SELECT * FROM maintenance_tasks WHERE account_id = ?"
        params: list = [account_id]
        if status:
            q += " AND status = ?"
            params.append(status)
        if vehicle_name:
            q += " AND vehicle_name = ?"
            params.append(vehicle_name)
        q += " ORDER BY CASE status WHEN 'overdue' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, created_at DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_maintenance_status(self, task_id: int, status: str) -> bool:
        completed_at = self._now() if status == "done" else None
        await self._db.execute(
            "UPDATE maintenance_tasks SET status = ?, completed_at = ? WHERE id = ?",
            (status, completed_at, task_id),
        )
        await self._db.commit()
        return True

    async def get_overdue_tasks(self, account_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks WHERE account_id = ? AND status = 'overdue'",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_tasks_by_date(self) -> list[dict]:
        """Get all pending tasks with a due_date in the past (across all accounts)."""
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < ?",
            (now,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # FUEL ENTRIES
    # ══════════════════════════════════════════════════════════════

    async def add_fuel_entry(
        self, account_id: int, company_code: str,
        vehicle_name: str, gallons: float, price_per_gallon: float,
        odometer_miles: float, date: str,
        created_by: int = 0, vehicle_id: str = "",
    ) -> int:
        now = self._now()
        total_cost = round(gallons * price_per_gallon, 2)
        cur = await self._db.execute(
            """INSERT INTO fuel_entries
               (account_id, company_code, vehicle_id, vehicle_name,
                gallons, price_per_gallon, total_cost, odometer_miles,
                date, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             gallons, price_per_gallon, total_cost, odometer_miles,
             date, created_by, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_fuel_entries(
        self, account_id: int, vehicle_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        q = "SELECT * FROM fuel_entries WHERE account_id = ?"
        params: list = [account_id]
        if vehicle_name:
            q += " AND vehicle_name = ?"
            params.append(vehicle_name)
        q += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_fuel_summary(self, account_id: int) -> list[dict]:
        """Per-vehicle fuel summary: total gallons, total cost, avg price, entry count."""
        cur = await self._db.execute(
            """SELECT vehicle_name, company_code,
                      COUNT(*) as entries,
                      SUM(gallons) as total_gallons,
                      SUM(total_cost) as total_cost,
                      AVG(price_per_gallon) as avg_price,
                      MIN(odometer_miles) as first_odo,
                      MAX(odometer_miles) as last_odo
               FROM fuel_entries
               WHERE account_id = ?
               GROUP BY vehicle_name
               ORDER BY total_cost DESC""",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # DIGEST SUBSCRIPTIONS
    # ══════════════════════════════════════════════════════════════

    async def subscribe_digest(
        self, user_id: int, frequency: str = "daily", send_hour: int = 7,
    ) -> None:
        now = self._now()
        await self._db.execute(
            """INSERT INTO digest_subscriptions (user_id, frequency, send_hour, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE
               SET frequency = excluded.frequency,
                   send_hour = excluded.send_hour,
                   is_active = 1""",
            (user_id, frequency, send_hour, now),
        )
        await self._db.commit()

    async def unsubscribe_digest(self, user_id: int) -> None:
        await self._db.execute(
            "UPDATE digest_subscriptions SET is_active = 0 WHERE user_id = ?",
            (user_id,),
        )
        await self._db.commit()

    async def get_digest_subscription(self, user_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM digest_subscriptions WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_digest_subscribers(self, send_hour: int, frequency: str = "daily") -> list[dict]:
        """Get all active subscribers for a given hour and frequency."""
        cur = await self._db.execute(
            """SELECT ds.*, u.telegram_id, u.account_id, u.role, u.truck_num
               FROM digest_subscriptions ds
               JOIN users u ON u.id = ds.user_id
               WHERE ds.is_active = 1 AND ds.send_hour = ? AND ds.frequency = ?
               AND u.is_active = 1""",
            (send_hour, frequency),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # ACCOUNT SETTINGS (key-value per account)
    # ══════════════════════════════════════════════════════════════

    async def get_account_setting(self, account_id: int, key: str,
                                  default: str = "") -> str:
        """Get a single setting value for an account."""
        cur = await self._db.execute(
            "SELECT value FROM account_settings "
            "WHERE account_id = ? AND key = ?",
            (account_id, key),
        )
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_account_setting(self, account_id: int, key: str,
                                  value: str):
        """Set a single setting value for an account (upsert)."""
        await self._db.execute(
            "INSERT INTO account_settings (account_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, key) DO UPDATE SET value = ?, updated_at = ?",
            (account_id, key, value, self._now(), value, self._now()),
        )
        await self._db.commit()

    # ══════════════════════════════════════════════════════════════
    # ALERT ACKNOWLEDGMENTS
    # ══════════════════════════════════════════════════════════════

    async def create_alert_ack(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str, alert_key: str,
        message_id: int, chat_id: int, sent_to: int,
        next_escalation: Optional[str] = None,
    ) -> int:
        """Record a sent alert that needs acknowledgment."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO alert_acknowledgments
               (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
                message_id, chat_id, sent_to, next_escalation, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
             message_id, chat_id, sent_to, next_escalation, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_active_vehicle_acks(
        self, account_id: int, vehicle_id: str, sent_to: int,
    ) -> list[dict]:
        """Get active (unacked) alert acks for a vehicle/subscriber pair."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND vehicle_id = ? AND sent_to = ? "
            "AND acknowledged_at IS NULL AND status = 'active'",
            (account_id, vehicle_id, sent_to),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def supersede_alert_ack(self, ack_id: int):
        """Mark an alert ack as superseded (replaced by a newer alert)."""
        await self._db.execute(
            "UPDATE alert_acknowledgments SET status = 'superseded', "
            "next_escalation = NULL WHERE id = ?",
            (ack_id,),
        )
        await self._db.commit()

    async def acknowledge_alert(self, ack_id: int, user_id: int) -> bool:
        """Mark an alert as acknowledged — also acks all rows with the same alert_key."""
        now = self._now()
        # Get the alert_key for this alert
        cur = await self._db.execute(
            "SELECT alert_key, account_id FROM alert_acknowledgments WHERE id = ?",
            (ack_id,),
        )
        row = await cur.fetchone()
        if not row:
            return False
        alert_key = row["alert_key"]
        account_id = row["account_id"]
        # Ack all rows with the same alert_key (shared acknowledgment)
        await self._db.execute(
            "UPDATE alert_acknowledgments SET acknowledged_by = ?, acknowledged_at = ?, "
            "status = 'acknowledged', next_escalation = NULL "
            "WHERE alert_key = ? AND account_id = ? AND acknowledged_at IS NULL",
            (user_id, now, alert_key, account_id),
        )
        await self._db.commit()
        return True

    async def get_unacked_alerts(self, before: Optional[str] = None) -> list[dict]:
        """Get unacknowledged alerts that need escalation.

        If `before` is given, only return alerts with next_escalation < before.
        """
        q = ("SELECT * FROM alert_acknowledgments "
             "WHERE acknowledged_at IS NULL AND next_escalation IS NOT NULL "
             "AND status = 'active'")
        params: list = []
        if before:
            q += " AND next_escalation <= ?"
            params.append(before)
        q += " ORDER BY created_at"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_alert_escalation(self, ack_id: int,
                                       escalation_level: int,
                                       next_escalation: Optional[str],
                                       message_id: Optional[int] = None):
        """Update escalation level, next escalation time, and optionally message_id."""
        if message_id is not None:
            await self._db.execute(
                "UPDATE alert_acknowledgments SET escalation_level = ?, "
                "next_escalation = ?, message_id = ? WHERE id = ?",
                (escalation_level, next_escalation, message_id, ack_id),
            )
        else:
            await self._db.execute(
                "UPDATE alert_acknowledgments SET escalation_level = ?, "
                "next_escalation = ? WHERE id = ?",
                (escalation_level, next_escalation, ack_id),
            )
        await self._db.commit()

    async def expire_stale_alerts(self, older_than: str) -> list[dict]:
        """Auto-expire unacked alerts older than the given ISO timestamp.

        Returns the list of expired alert rows (for audit logging).
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE acknowledged_at IS NULL AND status = 'active' AND created_at <= ?",
            (older_than,),
        )
        rows = await cur.fetchall()
        expired = [dict(r) for r in rows]
        if expired:
            ids = [r["id"] for r in expired]
            placeholders = ",".join("?" for _ in ids)
            await self._db.execute(
                f"UPDATE alert_acknowledgments SET status = 'expired', "
                f"next_escalation = NULL WHERE id IN ({placeholders})",
                ids,
            )
            await self._db.commit()
        return expired

    async def get_alert_history(
        self, account_id: int, limit: int = 50,
    ) -> list[dict]:
        """Get alert acknowledgment history for an account (all statuses)."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments WHERE account_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_alerts(self, account_id: int) -> list[dict]:
        """Get all active (unacknowledged, not expired) alerts for an account."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND status = 'active' "
            "ORDER BY created_at DESC",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def snooze_alert(self, ack_id: int, snooze_until: str) -> bool:
        """Snooze an alert — postpone its next escalation without acking."""
        cur = await self._db.execute(
            "UPDATE alert_acknowledgments SET next_escalation = ? "
            "WHERE id = ? AND acknowledged_at IS NULL AND status = 'active'",
            (snooze_until, ack_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def get_alert_ack_by_id(self, ack_id: int) -> dict | None:
        """Fetch an alert acknowledgment row by its ID."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments WHERE id = ?", (ack_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    # ══════════════════════════════════════════════════════════════
    # DND ALERT QUEUE
    # ══════════════════════════════════════════════════════════════

    async def queue_dnd_alert(
        self, account_id: int, telegram_id: int,
        alert_type: str, vehicle_name: str, alert_text: str,
    ) -> int:
        """Queue a non-critical alert suppressed by DND for later delivery."""
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO dnd_alert_queue "
            "(account_id, telegram_id, alert_type, vehicle_name, alert_text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, telegram_id, alert_type, vehicle_name, alert_text, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_pending_dnd_alerts(self, telegram_id: int) -> list[dict]:
        """Get all undelivered DND-queued alerts for a user."""
        cur = await self._db.execute(
            "SELECT * FROM dnd_alert_queue "
            "WHERE telegram_id = ? AND delivered = 0 "
            "ORDER BY created_at",
            (telegram_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_dnd_alerts_delivered(self, telegram_id: int) -> int:
        """Mark all pending DND alerts as delivered for a user. Returns count."""
        cur = await self._db.execute(
            "UPDATE dnd_alert_queue SET delivered = 1 "
            "WHERE telegram_id = ? AND delivered = 0",
            (telegram_id,),
        )
        await self._db.commit()
        return cur.rowcount

    async def is_vehicle_in_maintenance(
        self, account_id: int, vehicle_name: str,
    ) -> bool:
        """Check if a vehicle has any pending/overdue maintenance tasks."""
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM maintenance_tasks "
            "WHERE account_id = ? AND vehicle_name = ? "
            "AND status IN ('pending', 'overdue')",
            (account_id, vehicle_name),
        )
        row = await cur.fetchone()
        return row[0] > 0

    # ── Alert History (consolidation) ────────────────────────────

    async def get_active_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str, chat_id: int,
    ) -> Optional[dict]:
        """Get the active alert history record for a vehicle+type+chat."""
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND chat_id = ? AND status = 'active'",
            (account_id, alert_type, vehicle_id, chat_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str,
        chat_id: int, message_id: int,
        last_detail: str = "",
    ) -> dict:
        """Create or update an alert history record.

        If an active record exists: increment count, update last_seen
        and message_id. Otherwise create a new record.
        Returns the record dict.
        """
        now = self._now()
        existing = await self.get_active_alert_history(
            account_id, alert_type, vehicle_id, chat_id,
        )
        if existing:
            await self._db.execute(
                "UPDATE alert_history SET "
                "occurrence_count = occurrence_count + 1, "
                "last_seen = ?, message_id = ?, last_detail = ? "
                "WHERE id = ?",
                (now, message_id, last_detail, existing["id"]),
            )
            await self._db.commit()
            existing["occurrence_count"] += 1
            existing["last_seen"] = now
            existing["message_id"] = message_id
            existing["last_detail"] = last_detail
            return existing
        else:
            cur = await self._db.execute(
                "INSERT INTO alert_history "
                "(account_id, alert_type, vehicle_id, vehicle_name, "
                "chat_id, message_id, occurrence_count, "
                "first_seen, last_seen, last_detail, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'active')",
                (account_id, alert_type, vehicle_id, vehicle_name,
                 chat_id, message_id, now, now, last_detail),
            )
            await self._db.commit()
            return {
                "id": cur.lastrowid,
                "account_id": account_id,
                "alert_type": alert_type,
                "vehicle_id": vehicle_id,
                "vehicle_name": vehicle_name,
                "chat_id": chat_id,
                "message_id": message_id,
                "occurrence_count": 1,
                "first_seen": now,
                "last_seen": now,
                "last_detail": last_detail,
                "status": "active",
            }

    async def auto_resolve_alerts_by_vehicle(
        self, account_id: int, alert_type: str, vehicle_id: str,
    ) -> list[dict]:
        """Auto-resolve all active unacked alerts for a vehicle/type.

        Called from check loops when a vehicle's condition clears.
        Returns resolved rows (with message_id/chat_id for cleanup).
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "AND acknowledged_at IS NULL AND status = 'active'",
            (account_id, alert_type, vehicle_id),
        )
        rows = await cur.fetchall()
        resolved = [dict(r) for r in rows]
        if resolved:
            now = self._now()
            ids = [r["id"] for r in resolved]
            placeholders = ",".join("?" for _ in ids)
            await self._db.execute(
                f"UPDATE alert_acknowledgments SET acknowledged_by = 0, "
                f"acknowledged_at = ?, status = 'acknowledged', "
                f"next_escalation = NULL WHERE id IN ({placeholders})",
                [now] + ids,
            )
            await self._db.commit()
        return resolved

    async def clear_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str,
    ) -> list[dict]:
        """Mark active alert history records as cleared for a vehicle.

        Returns the cleared records (with message_id/chat_id for deletion).
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND status = 'active'",
            (account_id, alert_type, vehicle_id),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            await self._db.execute(
                "UPDATE alert_history SET status = 'cleared' "
                "WHERE account_id = ? AND alert_type = ? "
                "AND vehicle_id = ? AND status = 'active'",
                (account_id, alert_type, vehicle_id),
            )
            await self._db.commit()
        return rows

    # ══════════════════════════════════════════════════════════════
    # AUDIT LOG
    # ══════════════════════════════════════════════════════════════

    async def add_audit_log(
        self, account_id: int, user_id: Optional[int],
        action: str, target_type: str = "", target_id: str = "",
        details: str = "",
    ) -> int:
        """Record an action in the audit log."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO audit_log
               (account_id, user_id, action, target_type, target_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, action, target_type, target_id, details, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_audit_log(self, account_id: int, limit: int = 50) -> list[dict]:
        """Get recent audit log entries for an account."""
        cur = await self._db.execute(
            "SELECT * FROM audit_log WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # AI USAGE TRACKING
    # ══════════════════════════════════════════════════════════════

    async def log_ai_usage(
        self, account_id: int, user_id: int, model: str,
        request_type: str, prompt_tokens: int = 0,
        reply_tokens: int = 0, total_tokens: int = 0,
    ) -> int:
        """Log an AI API call with token counts."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO ai_usage
               (account_id, user_id, model, request_type,
                prompt_tokens, reply_tokens, total_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, model, request_type,
             prompt_tokens, reply_tokens, total_tokens, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_ai_usage_stats(self, account_id: int, days: int = 30) -> dict:
        """Get AI usage stats for an account over the past N days.

        Returns dict with total_requests, total_tokens, by_type breakdown,
        and by_model breakdown.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """SELECT request_type, model,
                      COUNT(*) as cnt,
                      SUM(prompt_tokens) as sum_prompt,
                      SUM(reply_tokens) as sum_reply,
                      SUM(total_tokens) as sum_total
               FROM ai_usage
               WHERE account_id = ? AND created_at >= ?
               GROUP BY request_type, model""",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        by_type: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        total_requests = 0
        total_tokens = 0
        for r in rows:
            rt = r["request_type"]
            m = r["model"]
            cnt = r["cnt"]
            tok = r["sum_total"] or 0
            total_requests += cnt
            total_tokens += tok
            # Aggregate by type
            if rt not in by_type:
                by_type[rt] = {"requests": 0, "tokens": 0}
            by_type[rt]["requests"] += cnt
            by_type[rt]["tokens"] += tok
            # Aggregate by model
            if m not in by_model:
                by_model[m] = {"requests": 0, "tokens": 0,
                               "prompt_tokens": 0, "reply_tokens": 0}
            by_model[m]["requests"] += cnt
            by_model[m]["tokens"] += tok
            by_model[m]["prompt_tokens"] += r["sum_prompt"] or 0
            by_model[m]["reply_tokens"] += r["sum_reply"] or 0
        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "by_type": by_type,
            "by_model": by_model,
            "days": days,
        }

    async def get_ai_usage_daily(self, account_id: int, days: int = 7) -> list[dict]:
        """Get daily AI usage breakdown for the past N days."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            """SELECT DATE(created_at) as day,
                      COUNT(*) as requests,
                      SUM(total_tokens) as tokens
               FROM ai_usage
               WHERE account_id = ? AND created_at >= ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    # DIGEST SUBSCRIPTIONS (extended)
    # ══════════════════════════════════════════════════════════════

    async def subscribe_digest_ext(
        self, user_id: int, frequency: str = "daily",
        send_hour: int = 7, timezone: str = "America/New_York",
        report_type: str = "faults",
    ) -> None:
        """Subscribe to auto reports with timezone and report type support."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO digest_subscriptions
               (user_id, frequency, send_hour, timezone, report_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE
               SET frequency = excluded.frequency,
                   send_hour = excluded.send_hour,
                   timezone = excluded.timezone,
                   report_type = excluded.report_type,
                   is_active = 1""",
            (user_id, frequency, send_hour, timezone, report_type, now),
        )
        await self._db.commit()

    async def get_digest_subscribers_by_local_hour(self, utc_hour: int) -> list[dict]:
        """Get all active digest subscribers whose local send_hour matches now.

        Computes which UTC hour each subscriber's local send_hour maps to,
        and returns those matching the given utc_hour.
        """
        # Fetch all active subscriptions with user info
        cur = await self._db.execute(
            """SELECT ds.*, u.telegram_id, u.account_id, u.role, u.truck_num
               FROM digest_subscriptions ds
               JOIN users u ON u.id = ds.user_id
               WHERE ds.is_active = 1 AND u.is_active = 1""",
        )
        rows = await cur.fetchall()

        from datetime import datetime as _dt, timezone as _tz
        from zoneinfo import ZoneInfo

        now_utc = _dt.now(_tz.utc)
        results = []
        for r in rows:
            row = dict(r)
            tz_name = row.get("timezone", "America/New_York")
            send_hour = row.get("send_hour", 7)
            try:
                user_tz = ZoneInfo(tz_name)
                # Create a datetime at the user's desired local send_hour today
                local_now = now_utc.astimezone(user_tz)
                local_send = local_now.replace(hour=send_hour, minute=0, second=0, microsecond=0)
                # Convert that to UTC and check if the UTC hour matches
                utc_send = local_send.astimezone(_tz.utc)
                if utc_send.hour == utc_hour:
                    results.append(row)
            except Exception:
                # Fallback: treat send_hour as UTC
                if send_hour == utc_hour:
                    results.append(row)
        return results

    # ══════════════════════════════════════════════════════════════
    # ENCRYPTION MIGRATION
    # ══════════════════════════════════════════════════════════════

    async def migrate_encrypt_api_keys(self) -> int:
        """Encrypt all plaintext API keys in the companies table.

        Skips keys that are already encrypted (start with 'enc::').
        Returns the number of keys that were encrypted.
        """
        from encryption import is_enabled, encrypt as _encrypt, _ENC_PREFIX

        if not is_enabled():
            logger.info("Encryption not enabled — skipping key migration")
            return 0

        cur = await self._db.execute("SELECT id, samsara_api_key FROM companies")
        rows = await cur.fetchall()
        count = 0
        for row in rows:
            raw = row["samsara_api_key"]
            if raw.startswith(_ENC_PREFIX):
                continue  # already encrypted
            encrypted = _encrypt(raw)
            await self._db.execute(
                "UPDATE companies SET samsara_api_key = ? WHERE id = ?",
                (encrypted, row["id"]),
            )
            count += 1
        if count:
            await self._db.commit()
            logger.info(f"Encrypted {count} plaintext API key(s)")
        return count

    # ── Camera Check History ─────────────────────────────────────

    async def save_camera_check(
        self, account_id: int, vehicle_id: str, vehicle_name: str,
        camera_type: str, status: str, obstruction: str,
        alignment: str, quality: str, summary: str,
    ):
        """Insert a single camera check result."""
        await self._db.execute(
            "INSERT INTO camera_checks "
            "(account_id, vehicle_id, vehicle_name, camera_type, "
            "status, obstruction, alignment, quality, summary, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, vehicle_id, vehicle_name, camera_type,
             status, obstruction, alignment, quality, summary, self._now()),
        )
        await self._db.commit()

    async def get_camera_check_history(
        self, account_id: int, limit: int = 30,
        vehicle_name: str | None = None,
    ) -> list[dict]:
        """Get recent camera check history, newest first.

        Optionally filter by vehicle_name.
        """
        if vehicle_name:
            cur = await self._db.execute(
                "SELECT * FROM camera_checks "
                "WHERE account_id = ? AND vehicle_name = ? "
                "ORDER BY checked_at DESC LIMIT ?",
                (account_id, vehicle_name, limit),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM camera_checks "
                "WHERE account_id = ? "
                "ORDER BY checked_at DESC LIMIT ?",
                (account_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
