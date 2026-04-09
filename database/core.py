"""Database core — connection management, row converters, helpers."""

from __future__ import annotations

import asyncio
import aiosqlite
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from encryption import encrypt as _enc, decrypt as _dec

from .models import Role, Account, Company, User, AuthorizedChat, Invite
from . import schema, migrations

logger = logging.getLogger(__name__)

# Default number of read-pool connections per database.
# Each connection runs in its own thread via aiosqlite, so N connections
# allow N concurrent reads under WAL mode.
_DEFAULT_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "4"))


class _DatabaseCore:
    """Base class providing connection lifecycle, row converters, and utilities."""

    def __init__(self, path: str = "data/bot.db", pool_size: int = _DEFAULT_POOL_SIZE):
        self.path = path
        self._pool_size = pool_size
        self._db: Optional[aiosqlite.Connection] = None  # writer connection
        self._read_pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._all_readers: list[aiosqlite.Connection] = []

    async def _open_connection(self) -> aiosqlite.Connection:
        """Open a new aiosqlite connection with standard pragmas."""
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def initialize(self):
        """Open DB, create/migrate schema, and spin up the read pool."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await self._open_connection()
        await schema.create_tables(self._db)
        await migrations.run_all(self._db)

        # Spin up read-pool connections
        for _ in range(self._pool_size):
            conn = await self._open_connection()
            self._all_readers.append(conn)
            self._read_pool.put_nowait(conn)

        logger.info(
            "Database ready at %s (writer + %d readers)", self.path, self._pool_size,
        )

    @asynccontextmanager
    async def acquire(self):
        """Check out a read connection from the pool.

        Usage::

            async with db.acquire() as conn:
                cur = await conn.execute("SELECT ...")
                rows = await cur.fetchall()

        The connection is returned to the pool when the block exits.
        For write operations, use ``self._db`` (the dedicated writer)
        or the ``transaction()`` context manager.
        """
        conn = await self._read_pool.get()
        try:
            yield conn
        finally:
            self._read_pool.put_nowait(conn)

    async def close(self):
        for conn in self._all_readers:
            await conn.close()
        self._all_readers.clear()
        # Drain the queue
        while not self._read_pool.empty():
            try:
                self._read_pool.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._db:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self):
        """Async context manager for an IMMEDIATE transaction.

        Usage::
            async with db.transaction():
                await db._db.execute(...)
                await db._db.execute(...)
        Commits on success, rolls back on exception.
        """
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            await self._db.execute("COMMIT")
        except BaseException:
            await self._db.execute("ROLLBACK")
            raise

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

    # ── Row converters ────────────────────────────────────────────

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
            alert_parking=bool(row["alert_parking"]) if "alert_parking" in row.keys() else True,
            ai_parking=bool(row["ai_parking"]) if "ai_parking" in row.keys() else False,
            alert_camera=bool(row["alert_camera"]) if "alert_camera" in row.keys() else True,
            quiet_start=row["quiet_start"] if "quiet_start" in row.keys() else None,
            quiet_end=row["quiet_end"] if "quiet_end" in row.keys() else None,
            timezone=row["timezone"] if "timezone" in row.keys() else "America/New_York",
            language=row["language"] if "language" in row.keys() else "en",
            last_shift_report=row["last_shift_report"] if "last_shift_report" in row.keys() else None,
            email=row["email"] if "email" in row.keys() else None,
            password_hash=row["password_hash"] if "password_hash" in row.keys() else None,
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

    # ── Encryption migration (public) ────────────────────────────

    async def migrate_encrypt_api_keys(self) -> int:
        """Encrypt all plaintext API keys in the companies table."""
        return await migrations.migrate_encrypt_api_keys(self._db)
