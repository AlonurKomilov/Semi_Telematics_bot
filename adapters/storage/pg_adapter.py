"""PostgreSQL connection adapter for the storage layer.

When DATABASE_URL is set (e.g. postgresql://user:pass@host/dbname),
this module provides asyncpg-backed replacements for the aiosqlite
connection and cursor APIs used throughout the mixin layer.

Design goals:
  - Zero changes required in any mixin (accounts.py, users.py, …)
  - SQL uses SQLite `?` placeholders — this layer rewrites to `$N`
  - asyncpg Records expose `row["col"]` just like aiosqlite.Row
  - Transactions work the same way (BEGIN / COMMIT / ROLLBACK)
  - PRAGMAs are silently ignored (PostgreSQL has no PRAGMA)
  - executescript() converts semicolons to individual execute() calls
    (needed for schema creation)

Usage — automatic when DATABASE_URL is set:
    DATABASE_URL=postgresql://user:pass@localhost/4truck

The module is imported lazily in _DatabaseCore so the rest of the
codebase never needs to know which backend is active.
"""

from __future__ import annotations

import re
import logging
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# SQLite PRAGMA pattern — skip entirely in PostgreSQL
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\s+", re.IGNORECASE)

# SQLite-specific clauses to strip from CREATE TABLE for PostgreSQL
_AUTOINCREMENT_RE = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
# SQLite uses INTEGER PRIMARY KEY for autoincrement — map to SERIAL in PG
_INT_PK_RE = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)


def _sqlite_to_pg_sql(sql: str) -> str:
    """Translate a single SQLite SQL statement to PostgreSQL-compatible SQL.

    Handles:
      - ? → $N positional parameters
      - AUTOINCREMENT → (removed; SERIAL handles it)
      - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
      - datetime('now') → NOW()
      - PRAGMA → empty string (caller should skip)
      - IF NOT EXISTS on indexes → kept (PG supports it)
    """
    if _PRAGMA_RE.match(sql):
        return ""  # caller skips empty strings

    # SERIAL primary key
    sql = _INT_PK_RE.sub("SERIAL PRIMARY KEY", sql)
    sql = _AUTOINCREMENT_RE.sub("", sql)

    # SQLite datetime() → PG NOW()
    sql = re.sub(r"datetime\('now'\)", "NOW()", sql, flags=re.IGNORECASE)

    # ? → $1, $2, …
    count = 0
    result = []
    i = 0
    while i < len(sql):
        if sql[i] == "?" and (i == 0 or sql[i - 1] != "'"):
            count += 1
            result.append(f"${count}")
        else:
            result.append(sql[i])
        i += 1
    return "".join(result)


# ── asyncpg cursor shim ────────────────────────────────────────────

class _PgCursor:
    """Thin wrapper around an asyncpg fetch result that mimics aiosqlite.Cursor."""

    def __init__(self, rows: list, lastrowid: int = 0):
        self._rows = rows
        self._pos = 0
        self.lastrowid = lastrowid  # asyncpg returns lastrowid via RETURNING id

    async def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return _PgRow(row)
        return None

    async def fetchall(self):
        result = [_PgRow(r) for r in self._rows[self._pos:]]
        self._pos = len(self._rows)
        return result


class _PgRow:
    """asyncpg Record wrapper that exposes row["col"] and row[index] like aiosqlite.Row."""

    def __init__(self, record):
        self._record = record  # asyncpg Record

    def __getitem__(self, key):
        return self._record[key]

    def keys(self):
        return self._record.keys()

    def __iter__(self):
        return iter(self._record)

    def __len__(self):
        return len(self._record)

    def get(self, key, default=None):
        try:
            return self._record[key]
        except KeyError:
            return default


# ── asyncpg connection shim ────────────────────────────────────────

class _PgConnection:
    """Wraps an asyncpg Connection to expose the aiosqlite API used by mixins.

    Implements:
      execute(sql, params) → _PgCursor
      executescript(sql)   → None
      fetchall()           — not used directly (go through cursor)
      commit()             → no-op (autocommit or managed by _PgPool)
      close()              → releases connection to pool
    """

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._in_transaction = False
        # row_factory attribute to satisfy aiosqlite.Row compat checks
        self.row_factory = None

    async def execute(self, sql: str, params: tuple = ()) -> _PgCursor:
        pg_sql = _sqlite_to_pg_sql(sql)
        if not pg_sql.strip():
            return _PgCursor([])

        stripped = pg_sql.strip().upper()

        # For SELECT / RETURNING, fetch rows
        if stripped.startswith("SELECT") or "RETURNING" in stripped:
            rows = await self._conn.fetch(pg_sql, *params)
            return _PgCursor(rows)

        # For INSERT with lastrowid, append RETURNING id
        if stripped.startswith("INSERT") and "RETURNING" not in stripped:
            try:
                returning_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
                row = await self._conn.fetchrow(returning_sql, *params)
                lastrowid = row["id"] if row else 0
                return _PgCursor([row] if row else [], lastrowid=lastrowid)
            except Exception:
                # If RETURNING id fails (no id column), fall back
                await self._conn.execute(pg_sql, *params)
                return _PgCursor([])

        # UPDATE / DELETE / DDL
        await self._conn.execute(pg_sql, *params)
        return _PgCursor([])

    async def executescript(self, script: str) -> None:
        """Execute a semicolon-separated SQL script (used for schema creation)."""
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            pg_stmt = _sqlite_to_pg_sql(stmt)
            if not pg_stmt.strip():
                continue
            try:
                await self._conn.execute(pg_stmt)
            except Exception as e:
                # CREATE IF NOT EXISTS can still fail in PG on concurrent init
                if "already exists" in str(e).lower():
                    logger.debug("Schema object already exists (skipping): %s", e)
                else:
                    raise

    async def commit(self) -> None:
        """No-op — asyncpg uses explicit transactions or autocommit."""
        pass

    async def close(self) -> None:
        await self._pool.release(self._conn)


# ── Pool / factory ────────────────────────────────────────────────

class _PgPool:
    """asyncpg connection pool wrapper."""

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10):
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None  # asyncpg.Pool after initialize()

    async def initialize(self) -> None:
        import asyncpg
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=60,
        )
        logger.info("asyncpg pool connected to PostgreSQL (min=%d max=%d)",
                    self._min_size, self._max_size)

    async def acquire_connection(self) -> _PgConnection:
        conn = await self._pool.acquire()
        return _PgConnection(conn, self._pool)

    async def release(self, conn) -> None:
        await self._pool.release(conn)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_PgConnection]:
        conn = await self.acquire_connection()
        try:
            yield conn
        finally:
            await conn.close()


async def open_pg_pool(dsn: str, pool_size: int = 4) -> _PgPool:
    """Create and initialize a PostgreSQL connection pool."""
    pool = _PgPool(dsn, min_size=2, max_size=max(pool_size, 4))
    await pool.initialize()
    return pool
