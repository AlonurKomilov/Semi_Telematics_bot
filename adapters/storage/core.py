"""Database core — connection management, row converters, helpers.

PostgreSQL is the only supported backend.  ``DATABASE_URL`` is
mandatory; we refuse to boot without it.  The SQLite branch that used
to live here was retired (cutover 2026-05-08); the only remaining
SQLite consumers are the test suite's per-test fixtures which run
against a ``testcontainers`` Postgres instance instead.
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Any

from .models import Role, Account, Company, User, AuthorizedChat, Invite
from . import schema, migrations, platform_schema, platform_migrations

logger = logging.getLogger(__name__)

_DEFAULT_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "4"))

# PostgreSQL DSN — REQUIRED.  Initialise() reads this at runtime so
# tests can monkeypatch it (see tests/conftest.py pg_db fixture).
_DATABASE_URL = os.getenv("DATABASE_URL", "")


class _DatabaseCore:
    """Base class providing connection lifecycle, row converters, and utilities.

    All operations go through an asyncpg pool wrapped by
    ``_PgConnection`` (adapters/storage/pg_adapter.py) which exposes
    the SQLite-compatible API the mixins were originally written for.
    """

    def __init__(self, path: str = "", pool_size: int = _DEFAULT_POOL_SIZE):
        # ``path`` is retained for backwards compatibility with callers
        # that pass a SQLite-style file path; it is ignored in the PG
        # branch and exists only so existing constructors don't have to
        # be rewritten.
        self.path = path
        self._pool_size = pool_size
        self._db: Optional[Any] = None  # _PgConnection wrapper
        self._pg_pool = None

    @property
    def _using_postgres(self) -> bool:
        return self._pg_pool is not None

    async def initialize(self):
        """Open the PG pool, create the schema, and run all migrations.

        ``DATABASE_URL`` is required — we refuse to start without it
        rather than silently fall back to a sentinel backend.
        """
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is required.  Set DATABASE_URL=postgresql://... "
                "in your .env (see .env.example) and restart."
            )
        from .pg_adapter import open_pg_pool, _PgConnection
        self._pg_pool = await open_pg_pool(_DATABASE_URL, pool_size=self._pool_size)

        # ``self._db`` is now a pool-PROXY, not a pinned connection.
        # Each ``self._db.execute(...)`` acquires a fresh connection
        # from the pool (or honors the contextvar pinned by
        # ``Database.transaction()``).  This is the Tier 4 Phase 2
        # pool refactor — the previous shared single connection +
        # asyncio.Lock serialized every mutation through one socket.
        self._db = _PgConnection(self._pg_pool)

        await schema.create_tables(self._db)
        await migrations.run_all(self._db)
        await platform_schema.create_tables(self._db)
        await platform_migrations.run_all(self._db)
        logger.info(
            "Database ready (PostgreSQL via asyncpg pool, pool_size=%d)",
            self._pool_size,
        )

    @asynccontextmanager
    async def acquire(self):
        """Check out a read connection from the asyncpg pool.

        Usage::

            async with db.acquire() as conn:
                cur = await conn.execute("SELECT ...")
                rows = await cur.fetchall()
        """
        async with self._pg_pool.connection() as conn:
            yield conn

    async def read_all(self, sql: str, params: tuple = ()) -> list:
        """Execute *sql* on a pooled read connection and return all rows.

        Use this for SELECT queries in mixins instead of ``self._db.execute``
        so reads are load-spread across the read pool and the write
        connection is reserved for mutations.
        """
        async with self.acquire() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def read_one(self, sql: str, params: tuple = ()):
        """Execute *sql* on a pooled read connection and return one row or None."""
        async with self.acquire() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def close(self):
        # In the pool-proxy world ``self._db.close()`` is a no-op (the
        # proxy doesn't own a connection), so the historical 60-second
        # close hang from holding a pinned writer is no longer a risk —
        # ``self._pg_pool.close()`` drains the pool directly.
        if self._db is not None:
            try:
                await self._db.close()
            except Exception:
                pass
            self._db = None
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None

    @asynccontextmanager
    async def transaction(self):
        """Async context manager for a transaction.

        Usage::
            async with db.transaction():
                await db._db.execute(...)
                await db._db.execute(...)

        Acquires one connection from the pool, pins it for the current
        async-task tree via a contextvar so every ``self._db.execute(...)``
        inside the block reuses it, runs an asyncpg native transaction,
        and releases on exit.  Commits on success, rolls back on
        exception.

        Nested ``transaction()`` calls in the same task tree are a
        no-op pass-through: the outer transaction's BEGIN/COMMIT
        handles the lifecycle.  asyncpg savepoints aren't used yet
        — partial rollback in nested blocks isn't supported and
        callers shouldn't rely on it.
        """
        from .pg_adapter import _pinned_conn, _set_pinned_conn

        already_pinned = _pinned_conn.get()
        if already_pinned is not None:
            # Nested transaction — just yield; the outer block owns
            # commit/rollback.  A rollback in the inner raises, which
            # propagates to the outer; the outer's except branch fires
            # ROLLBACK and we end up in a consistent state.
            yield
            return

        # Acquire a dedicated connection for the duration of the block.
        conn = await self._pg_pool._pool.acquire()
        token = _set_pinned_conn(conn)
        try:
            # Apply tenant scope on the pinned conn if set.  The
            # ``_PgConnection._acquire`` GUC-stamp path won't fire here
            # because the proxy will reuse this pinned conn directly.
            from .pg_adapter import _current_account_id
            aid = _current_account_id.get()
            if aid is not None:
                await conn.execute(
                    "SELECT set_config('app.account_id', $1, false)", str(aid),
                )

            tx = conn.transaction()
            await tx.start()
            try:
                yield
                await tx.commit()
            except BaseException:
                await tx.rollback()
                raise
        finally:
            _pinned_conn.reset(token)
            # Clear GUC so the recycled pool slot doesn't leak scope.
            try:
                await conn.execute(
                    "SELECT set_config('app.account_id', '', false)",
                )
            except Exception:
                pass
            await self._pg_pool._pool.release(conn)

    @asynccontextmanager
    async def with_account(self, account_id: int | None):
        """Set ``app.account_id`` for the current async-task tree.

        Postgres RLS policies read this GUC via
        ``current_setting('app.account_id', true)::int`` to enforce
        per-tenant isolation.  Without RLS enabled the value is a
        harmless session variable; with RLS enabled it determines
        which rows the wrapped queries can see.

        Usage::
            async with db.with_account(acc.id):
                tasks = await db.get_pending_tasks()  # filtered by RLS

        ``account_id=None`` clears the scope — used when entering a
        cross-tenant admin section that should not be RLS-filtered.
        Production behavior under RLS: rows with no matching account
        return empty; use the ``4truck_admin`` BYPASSRLS role for
        legitimate cross-tenant reads.

        The scope is propagated via a contextvar in :mod:`pg_adapter`,
        which is async-task-local — so two concurrent requests under
        different accounts never see each other's GUC, even though the
        pool reuses connections.  The pool-acquire hook in
        ``_PgConnection._acquire`` stamps ``set_config`` on every
        fresh acquire based on this contextvar.  If a transaction is
        active (pinned conn), the GUC is applied immediately to that
        conn so subsequent statements in the same transaction see the
        new scope.
        """
        from .pg_adapter import (
            _current_account_id, _set_current_account, _pinned_conn,
        )

        prev_account = _current_account_id.get()
        token = _set_current_account(account_id)

        # If we're inside a transaction (pinned conn), apply the GUC
        # right now so the rest of the transaction sees it.  Pool-mode
        # callers will pick up the new value on the next ``_acquire()``.
        pinned = _pinned_conn.get()
        new_str = "" if account_id is None else str(account_id)
        if pinned is not None:
            try:
                await pinned.execute(
                    "SELECT set_config('app.account_id', $1, false)", new_str,
                )
            except Exception as e:
                logger.debug("with_account set_config failed: %s", e)

        try:
            yield
        finally:
            _current_account_id.reset(token)
            if pinned is not None:
                prev_str = "" if prev_account is None else str(prev_account)
                try:
                    await pinned.execute(
                        "SELECT set_config('app.account_id', $1, false)", prev_str,
                    )
                except Exception:
                    pass

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
            bot_token_encrypted=row["bot_token_encrypted"] if "bot_token_encrypted" in row.keys() else None,
            bot_username=row["bot_username"] if "bot_username" in row.keys() else "",
            webhook_secret=row["webhook_secret"] if "webhook_secret" in row.keys() else "",
            payroll_enabled=bool(row["payroll_enabled"]) if "payroll_enabled" in row.keys() else False,
            coaching_enabled=bool(row["coaching_enabled"]) if "coaching_enabled" in row.keys() else False,
            timezone=row["timezone"] if "timezone" in row.keys() else "America/New_York",
        )

    def _row_to_company(self, row) -> Company:
        from infra.crypto import decrypt as _dec
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
            samsara_driver_id=row["samsara_driver_id"] if "samsara_driver_id" in row.keys() else None,
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
