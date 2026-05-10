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

import os
import re
import logging
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# SQLite PRAGMA pattern — skip entirely in PostgreSQL
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\s+", re.IGNORECASE)

# ``PRAGMA table_info(X)`` is used by migrations to introspect columns.
# Translate to an information_schema query that returns the same 6-column
# shape as SQLite (``cid, name, type, notnull, dflt_value, pk``) so callers
# don't need to branch on backend.
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+table_info\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)

# ``sqlite_master`` references in introspection queries (``SELECT … FROM
# sqlite_master WHERE type='table' AND name=…``).  Rewrite to a synthesised
# UNION over ``pg_tables`` and ``pg_indexes`` exposing the same
# ``(name, type, sql)`` columns.  ``sql`` is empty since PG doesn't store
# original CREATE statements; callers using it for substring checks must
# compare against the live ``information_schema`` shape instead.
_SQLITE_MASTER_RE = re.compile(r"\bsqlite_master\b", re.IGNORECASE)

# SQLite-specific clauses to strip from CREATE TABLE for PostgreSQL
_AUTOINCREMENT_RE = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
# SQLite uses INTEGER PRIMARY KEY for autoincrement — map to SERIAL in PG
_INT_PK_RE = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)

# SQLite ``datetime('now')`` / ``datetime('now', '-7 days')`` and ``date('now', ...)``
# Both literal-offset and parameterised forms are matched. The parameterised
# form preserves the ``?`` so the subsequent ``? → $N`` pass picks it up.
#   datetime('now')                    → NOW()
#   datetime('now', '-7 days')         → (NOW() + INTERVAL '-7 days')
#   datetime('now', ?)                 → (NOW() + (?::interval))
#   date('now', ...)                   → (NOW() + ...)::date  (same shape)
_DATETIME_NOW_BARE_RE = re.compile(
    r"\bdatetime\(\s*'now'\s*\)", re.IGNORECASE,
)
_DATE_NOW_BARE_RE = re.compile(
    r"\bdate\(\s*'now'\s*\)", re.IGNORECASE,
)
_DATETIME_NOW_OFFSET_LITERAL_RE = re.compile(
    r"\bdatetime\(\s*'now'\s*,\s*'([^']*)'\s*\)", re.IGNORECASE,
)
_DATE_NOW_OFFSET_LITERAL_RE = re.compile(
    r"\bdate\(\s*'now'\s*,\s*'([^']*)'\s*\)", re.IGNORECASE,
)
_DATETIME_NOW_OFFSET_PARAM_RE = re.compile(
    r"\bdatetime\(\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE,
)
_DATE_NOW_OFFSET_PARAM_RE = re.compile(
    r"\bdate\(\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE,
)

# ``INSERT OR IGNORE INTO foo (...) VALUES (...)`` is SQLite-specific.
# PostgreSQL equivalent is ``INSERT INTO foo (...) VALUES (...) ON CONFLICT DO NOTHING``.
# We split the translation into two steps:
#   (1) strip the ``OR IGNORE`` modifier so the row reads as plain INSERT
#   (2) append ``ON CONFLICT DO NOTHING`` to the end of the statement
# That way the existing ``RETURNING id`` augmentation in execute() still applies
# and we don't have to teach the rest of the adapter about the modifier.
_INSERT_OR_IGNORE_RE = re.compile(
    r"\bINSERT\s+OR\s+IGNORE\b", re.IGNORECASE,
)
# ``INSERT OR REPLACE`` is harder — PG's ``ON CONFLICT DO UPDATE`` requires
# explicitly enumerating columns to set. We don't currently have any callers
# using OR REPLACE (everyone uses ``ON CONFLICT DO UPDATE`` directly), but
# detect it so we can warn instead of producing silent corruption.
_INSERT_OR_REPLACE_RE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\b", re.IGNORECASE,
)

# Patterns that we *recognise* as SQLite-specific but don't currently handle.
# When detected we emit a WARNING log line tagged ``pg_adapter:untranslated``
# so prod logs surface every query the adapter passed through unchanged that
# might fail in PG. Keep this list minimal — false positives create noise.
_UNTRANSLATED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bjson_extract\b", re.IGNORECASE),
     "json_extract — use PG's '->' / '->>' / 'jsonb_path_query' instead"),
    (re.compile(r"\bjulianday\b", re.IGNORECASE),
     "julianday — use EXTRACT(EPOCH FROM ...) / 86400.0 in PG"),
    (re.compile(r"\bstrftime\b", re.IGNORECASE),
     "strftime — use to_char() in PG"),
    (re.compile(r"\bprintf\(", re.IGNORECASE),
     "printf — use format() / concat() in PG"),
    (re.compile(r"\bWITHOUT\s+ROWID\b", re.IGNORECASE),
     "WITHOUT ROWID — silently dropped; PG always has implicit row identity"),
    (re.compile(r"\brandom\(\)", re.IGNORECASE),
     "random() — works in PG but returns float in [0, 1) not int (use random()::int * N)"),
]

# Compatibility-mode flag — set when we're actually running against PG.
# Tests can monkeypatch this.
_LOG_UNTRANSLATED = os.getenv("PG_ADAPTER_DEBUG", "").lower() in ("1", "true", "yes")


def _warn_if_untranslated(sql: str) -> None:
    """Surface SQL patterns we recognise but don't translate.

    Log level is INFO so it shows in normal production logs without spamming
    DEBUG; `PG_ADAPTER_DEBUG=1` raises to WARNING for the dual-write phase.
    """
    if not _LOG_UNTRANSLATED:
        return
    for pat, msg in _UNTRANSLATED_PATTERNS:
        if pat.search(sql):
            logger.warning(
                "pg_adapter:untranslated %s | sql=%s",
                msg,
                sql[:200] + ("…" if len(sql) > 200 else ""),
            )
            break  # one warning per query is enough


def _sqlite_to_pg_sql(sql: str) -> str:
    """Translate a single SQLite SQL statement to PostgreSQL-compatible SQL.

    Handles:
      - ? → $N positional parameters
      - AUTOINCREMENT → (removed; SERIAL handles it)
      - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
      - datetime('now') / date('now') → NOW() / NOW()::date
      - datetime('now', '-7 days') / date('now', '-7 days')
        → (NOW() + INTERVAL '-7 days') / ((NOW() + INTERVAL '-7 days')::date)
      - datetime('now', ?) / date('now', ?)
        → (NOW() + ($N::interval)) / ((NOW() + ($N::interval))::date)
      - INSERT OR IGNORE → INSERT … ON CONFLICT DO NOTHING
      - PRAGMA → empty string (caller should skip)
      - IF NOT EXISTS on indexes → kept (PG supports it)

    Recognised-but-untranslated patterns (json_extract, julianday, strftime,
    printf, WITHOUT ROWID, random()) emit an INFO log so the dual-write
    phase can catch them in prod logs.
    """
    # ``PRAGMA table_info(X)`` — translate to information_schema query
    # returning the same 6-column shape SQLite produces. Match BEFORE the
    # generic PRAGMA-skip below so we don't return empty.
    pti = _PRAGMA_TABLE_INFO_RE.match(sql)
    if pti:
        tbl = pti.group(1)
        return (
            "SELECT ordinal_position - 1 AS cid, column_name AS name, "
            "data_type AS type, "
            "(CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END) AS notnull, "
            "column_default AS dflt_value, "
            "(CASE WHEN column_name = ANY("
            "  SELECT a.attname FROM pg_index i "
            "  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            f"  WHERE i.indrelid = '{tbl}'::regclass AND i.indisprimary"
            ") THEN 1 ELSE 0 END) AS pk "
            "FROM information_schema.columns "
            f"WHERE table_schema='public' AND table_name='{tbl}' "
            "ORDER BY ordinal_position"
        )

    if _PRAGMA_RE.match(sql):
        return ""  # caller skips empty strings

    # ``sqlite_master`` → UNION over pg_tables + pg_indexes that exposes the
    # same ``(name, type, sql)`` columns SQLite returns.
    if _SQLITE_MASTER_RE.search(sql):
        replacement = (
            "(SELECT tablename AS name, 'table' AS type, ''::text AS sql "
            "FROM pg_tables WHERE schemaname='public' "
            "UNION ALL SELECT indexname AS name, 'index' AS type, ''::text AS sql "
            "FROM pg_indexes WHERE schemaname='public') AS sqlite_master"
        )
        sql = _SQLITE_MASTER_RE.sub(replacement, sql)

    _warn_if_untranslated(sql)

    # SERIAL primary key
    sql = _INT_PK_RE.sub("SERIAL PRIMARY KEY", sql)
    sql = _AUTOINCREMENT_RE.sub("", sql)

    # ── datetime() / date() rewrites ────────────────────────
    # Order matters: handle the offset forms (literal + param) before the
    # bare form so the bare-form regex doesn't eat the wrapping quotes.
    sql = _DATETIME_NOW_OFFSET_LITERAL_RE.sub(
        lambda m: f"(NOW() + INTERVAL '{m.group(1)}')", sql,
    )
    sql = _DATE_NOW_OFFSET_LITERAL_RE.sub(
        lambda m: f"((NOW() + INTERVAL '{m.group(1)}')::date)", sql,
    )
    sql = _DATETIME_NOW_OFFSET_PARAM_RE.sub("(NOW() + (?::interval))", sql)
    sql = _DATE_NOW_OFFSET_PARAM_RE.sub("((NOW() + (?::interval))::date)", sql)
    sql = _DATETIME_NOW_BARE_RE.sub("NOW()", sql)
    sql = _DATE_NOW_BARE_RE.sub("(NOW()::date)", sql)

    # ``datetime(col)`` / ``date(col)`` — column-coercion form. SQLite uses
    # this to normalise a TEXT-stored timestamp for comparison; PG has no
    # ``datetime()`` function, so we cast the column to ``timestamptz`` /
    # ``date`` instead. Runs AFTER the ``datetime('now', …)`` rewrites so
    # those don't get matched by the column regex below.
    _COL = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?"
    sql = re.sub(
        rf"\bdatetime\(\s*({_COL})\s*\)",
        r"(\1::timestamptz)",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        rf"\bdate\(\s*({_COL})\s*\)",
        r"(\1::date)",
        sql,
        flags=re.IGNORECASE,
    )

    # ── INSERT OR IGNORE ────────────────────────────────────
    is_insert_or_ignore = bool(_INSERT_OR_IGNORE_RE.search(sql))
    if is_insert_or_ignore:
        sql = _INSERT_OR_IGNORE_RE.sub("INSERT", sql)

    # INSERT OR REPLACE → log warning; we don't auto-translate because it
    # requires column enumeration the regex can't reliably extract.
    if _INSERT_OR_REPLACE_RE.search(sql):
        logger.warning(
            "pg_adapter: INSERT OR REPLACE not auto-translated — "
            "rewrite to ON CONFLICT DO UPDATE manually | sql=%s",
            sql[:200],
        )

    # ── ? → $1, $2, … ────────────────────────────────────────
    count = 0
    result = []
    i = 0
    in_string = False
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_string = not in_string
        if ch == "?" and not in_string:
            count += 1
            result.append(f"${count}")
        else:
            result.append(ch)
        i += 1
    sql = "".join(result)

    # ── Append ON CONFLICT DO NOTHING for INSERT OR IGNORE ──
    # Done after parameter rewrite so we don't accidentally inject extra
    # placeholders or trip the simple ``startswith("INSERT")`` check
    # later in execute(). Strip a trailing semicolon if present.
    if is_insert_or_ignore:
        trimmed = sql.rstrip().rstrip(";").rstrip()
        # Avoid double-appending if someone already wrote ON CONFLICT.
        if "ON CONFLICT" not in trimmed.upper():
            sql = trimmed + " ON CONFLICT DO NOTHING"

    return sql


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
        # asyncpg Connections are NOT safe for concurrent use — only one
        # operation can be in flight at a time, otherwise asyncpg raises
        # "another operation is in progress". Mixins share a single
        # ``self._db`` (this object) across all coroutines, so without a
        # lock parallel API requests collide. The lock serialises every
        # underlying ``self._conn.*`` call. Reads that need parallelism
        # should use ``Database.acquire()`` which checks out a fresh
        # pool connection per request.
        import asyncio as _asyncio
        self._lock = _asyncio.Lock()

    async def execute(self, sql: str, params: tuple = ()) -> _PgCursor:
        pg_sql = _sqlite_to_pg_sql(sql)
        if not pg_sql.strip():
            return _PgCursor([])

        stripped = pg_sql.strip().upper()

        async with self._lock:
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
        """Execute a semicolon-separated SQL script (used for schema creation).

        Strips ``--`` line comments before splitting so a stray ``;`` inside
        a comment ("``-- foo; bar``") doesn't tear a CREATE TABLE in two.
        """
        # Strip ``--`` line comments to end-of-line so a ``;`` inside a
        # comment (e.g. "-- One row per account.  Stripe IDs stored here;
        # provider_data holds…") never breaks the statement split.
        clean_lines = []
        for line in script.splitlines():
            idx = line.find("--")
            if idx >= 0:
                line = line[:idx]
            clean_lines.append(line)
        clean_script = "\n".join(clean_lines)

        async with self._lock:
            for stmt in clean_script.split(";"):
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
