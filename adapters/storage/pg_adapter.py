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

import contextvars
import os
import re
import logging
from datetime import timedelta
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


# ── Connection-state contextvars ─────────────────────────────────────
# When set, ``_pinned_conn`` carries the asyncpg.Connection that the
# current async-task tree must reuse for every ``execute()`` — populated
# by ``Database.transaction()`` so multi-statement transactions don't
# get scattered across separate pool acquires (which would lose
# atomicity entirely).
#
# ``_current_account_id`` carries the tenant scope.  Set by
# ``Database.with_account()``; consumed by the pool acquire hook to
# ``SELECT set_config('app.account_id', ...)`` on every fresh
# connection.  Contextvars are async-task-local, so two concurrent
# requests under different accounts never see each other's GUC — the
# main reason this refactor exists.
_pinned_conn: contextvars.ContextVar = contextvars.ContextVar(
    "_pg_pinned_conn", default=None,
)
_current_account_id: contextvars.ContextVar = contextvars.ContextVar(
    "_pg_current_account_id", default=None,
)


def _set_pinned_conn(conn):
    """Set the pinned connection for the current task tree.

    Returns the contextvar token caller must pass to ``_pinned_conn.reset()``
    on exit.  Used by ``Database.transaction()``.
    """
    return _pinned_conn.set(conn)


def _set_current_account(account_id):
    """Set the per-task ``app.account_id`` scope.

    ``account_id=None`` clears the scope.  Returns a token to reset.
    """
    return _current_account_id.set(account_id)


def _get_current_account():
    """Read the current task's ``app.account_id`` (or None if unset)."""
    return _current_account_id.get()


def _get_pinned_conn():
    """Read the pinned conn for the current task (or None if unpinned)."""
    return _pinned_conn.get()


# Pattern matching the SQLite interval-modifier strings the codebase
# passes to ``datetime('now', ?)`` / ``date('now', ?)`` calls — e.g.
# ``"-30 days"``, ``"+7 days"``, ``"-2 hours"``.  asyncpg accepts
# ``timedelta`` for ``::interval`` parameters but rejects raw strings,
# so we convert at the binding boundary.
_SQLITE_INTERVAL_RE = re.compile(
    r"^\s*([+-]?)\s*(\d+)\s+"
    r"(second|sec|minute|min|hour|day|week|month|year)s?\s*$",
    re.IGNORECASE,
)

_INTERVAL_UNIT_TO_TIMEDELTA = {
    "second": "seconds", "sec": "seconds",
    "minute": "minutes", "min": "minutes",
    "hour": "hours",
    "day": "days",
    "week": "weeks",
}


def _maybe_coerce_interval_param(value: Any) -> Any:
    """Return ``value`` converted to ``timedelta`` if it's a SQLite-style
    interval modifier (e.g. ``"-30 days"``); otherwise return unchanged.

    Only called when the SQL the parameter is bound to contains a
    ``::interval`` cast, so non-interval strings on unrelated columns
    are never touched.  Month/year intervals fall through unchanged —
    they have no fixed-length timedelta equivalent and the few queries
    that need them either use literal intervals (translated to PG
    syntax directly) or pass days approximations explicitly.
    """
    if not isinstance(value, str):
        return value
    m = _SQLITE_INTERVAL_RE.match(value)
    if not m:
        return value
    sign = -1 if m.group(1) == "-" else 1
    qty = int(m.group(2)) * sign
    unit = m.group(3).lower()
    td_kwarg = _INTERVAL_UNIT_TO_TIMEDELTA.get(unit)
    if td_kwarg is None:
        return value  # month/year — leave for caller / PG to error visibly
    return timedelta(**{td_kwarg: qty})


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

    # ── BEGIN IMMEDIATE / BEGIN EXCLUSIVE → BEGIN ──────────────
    # SQLite's IMMEDIATE / EXCLUSIVE transaction modes acquire write
    # locks at BEGIN time so a write-write contention error fires
    # immediately rather than mid-transaction.  Postgres uses MVCC
    # and doesn't have an equivalent; a plain BEGIN is the correct
    # mapping (the transaction still gets serialisable-ish isolation
    # via the default READ COMMITTED level, which is what the SQLite
    # path was approximating).
    _stripped = sql.strip().upper()
    if _stripped in ("BEGIN IMMEDIATE", "BEGIN EXCLUSIVE",
                     "BEGIN IMMEDIATE TRANSACTION", "BEGIN EXCLUSIVE TRANSACTION"):
        return "BEGIN"

    # ── instr(s, sub) → strpos(s, sub) ─────────────────────────
    # SQLite's ``instr`` returns the 1-based index of ``sub`` in
    # ``s`` (or 0 when absent).  Postgres' ``strpos`` is identical
    # in both signature and semantics, so the substitution is
    # mechanical.  Required by migration 033 which back-fills
    # alert_subkey from a colon-prefixed last_detail.
    sql = re.sub(r"\binstr\s*\(", "strpos(", sql, flags=re.IGNORECASE)

    # ── MAX(a, b) → GREATEST(a, b), MIN(a, b) → LEAST(a, b) ─────
    # SQLite overloads MAX/MIN as both aggregates AND 2-argument
    # scalar functions.  Postgres treats MAX/MIN strictly as
    # aggregates and uses GREATEST/LEAST for scalar comparisons —
    # ``UPDATE accounts SET storage_used_bytes = MAX(0, ...)`` fails
    # in PG with "function max(integer, integer) does not exist".
    # We only rewrite when the MAX/MIN call has TWO comma-separated
    # arguments (matched non-greedily and one level deep) — the
    # one-arg aggregate form ``MAX(col)`` and grouped aggregates
    # are left alone.
    #
    # The pattern uses negative look-around to skip aggregates that
    # legitimately appear in subqueries (``... FROM (SELECT MAX(x)
    # FROM y) ...``); two-arg scalar usage is what production code
    # writes for ``MAX(0, col - delta)`` clamps.
    _scalar_pair = re.compile(
        r"\b(MAX|MIN)\s*\(\s*([^(),]+(?:\([^()]*\)[^(),]*)*)\s*,"
        r"\s*([^()]+(?:\([^()]*\)[^()]*)*)\s*\)",
        flags=re.IGNORECASE,
    )
    sql = _scalar_pair.sub(
        lambda m: f"{'GREATEST' if m.group(1).upper() == 'MAX' else 'LEAST'}"
                  f"({m.group(2)}, {m.group(3)})",
        sql,
    )

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

def _parse_status_rowcount(status: object) -> int:
    """Extract the affected-row count from asyncpg's command-status string.

    ``Connection.execute()`` returns a string like ``"UPDATE 3"``,
    ``"DELETE 12"``, ``"INSERT 0 5"`` (the middle 0 is the oid which
    asyncpg keeps for back-compat).  The trailing integer is always
    the affected-row count.  DDL statements (``CREATE TABLE``) return
    just the verb with no integer — we treat that as 0.

    Robust to: status being None (some test doubles), empty string,
    or a multi-word verb like "CREATE INDEX".  Defaults to 0 rather
    than raising so existing ``cur.rowcount > 0`` callers stay safe.
    """
    if not status or not isinstance(status, str):
        return 0
    parts = status.strip().split()
    if not parts:
        return 0
    last = parts[-1]
    if last.isdigit():
        return int(last)
    return 0


class _PgCursor:
    """Thin wrapper around an asyncpg fetch result that mimics aiosqlite.Cursor.

    ``rowcount`` matches aiosqlite semantics: number of rows the
    statement affected (UPDATE/DELETE) or returned (SELECT).  For
    INSERT it's the number of inserted rows.  Callers in
    ``adapters/storage/{maintenance,parking,permissions,…}.py`` rely
    on this to gate "did the row actually exist?" branches like
    ``return cur.rowcount > 0`` — without it those branches crash
    with ``'_PgCursor' object has no attribute 'rowcount'``.
    """

    def __init__(self, rows: list, lastrowid: int = 0, rowcount: int = 0):
        self._rows = rows
        self._pos = 0
        self.lastrowid = lastrowid  # asyncpg returns lastrowid via RETURNING id
        # Default to len(rows) so SELECT callers always see a sane
        # number; UPDATE/DELETE callers override via the explicit kwarg.
        self.rowcount = rowcount if rowcount else len(rows)

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
    """Pool-backed proxy exposing the aiosqlite API used by mixins.

    Previously this class wrapped a single pinned asyncpg connection
    plus a global ``asyncio.Lock`` — every mixin call serialized through
    that one connection.  Now it's a proxy around the pool: each
    ``execute()`` acquires a fresh connection unless the current
    async-task tree has pinned one via ``_pinned_conn`` (set by
    :meth:`Database.transaction`).  No more global lock; concurrent
    requests run in parallel against the pool.

    Two modes per call:

    * **Pinned** — when ``_pinned_conn`` is set (inside a
      ``Database.transaction()`` block): every ``execute()`` reuses that
      one connection so multi-statement transactions retain atomicity.
    * **Pooled** — otherwise: each ``execute()`` acquires-runs-releases
      around the underlying ``asyncpg.Pool``.  Before yielding the
      acquired connection, the proxy applies ``app.account_id`` from
      ``_current_account_id`` so Postgres RLS sees the right tenant
      scope on every query without an explicit per-method GUC reset.

    Implements:
      execute(sql, params) → _PgCursor
      executemany(sql, params_seq) → _PgCursor
      executescript(sql)   → None  (whole script runs on one acquired conn)
      commit()             → no-op (transactions handled by transaction())
      close()              → no-op (no long-held resources)

    The ``pinned_conn`` constructor arg keeps the historical signature
    working for ``_PgPool.acquire_connection()`` — passes a specific
    asyncpg.Connection that this proxy will reuse for every operation,
    bypassing the pool.  Used by ``Database.acquire()`` and by the
    legacy migration path in ``core.initialize()``.
    """

    def __init__(self, pool, pinned_conn=None):
        self._pool = pool  # _PgPool wrapping the asyncpg.Pool
        # When set at construction time, this proxy ALWAYS uses this
        # connection (e.g. the explicit acquire() context manager).
        # When None, the proxy honors the contextvar pinning (set by
        # transaction()) and otherwise pool-acquires per call.
        self._construct_pinned = pinned_conn
        self._in_transaction = False
        # row_factory attribute to satisfy aiosqlite.Row compat checks
        self.row_factory = None

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        """Yield an asyncpg.Connection for one operation.

        Priority order:
          1. Constructor-pinned conn (legacy path / explicit acquire())
          2. Contextvar-pinned conn (active transaction)
          3. Fresh acquire from the pool — also stamps app.account_id
             from ``_current_account_id`` before yielding, then resets
             the GUC after the operation so a recycled pool conn doesn't
             leak the previous caller's tenant scope.
        """
        if self._construct_pinned is not None:
            # Constructor-pinned: don't acquire/release, just yield.
            # GUC is the caller's responsibility (set explicitly on
            # entry/exit of the pin window).
            yield self._construct_pinned
            return

        pinned = _pinned_conn.get()
        if pinned is not None:
            # Active transaction — reuse its connection.  GUC was set
            # when the transaction started so it's already correct.
            yield pinned
            return

        # Fresh acquire from the pool.  Stamp the GUC on entry so RLS
        # sees the right tenant; reset on exit so the next caller of
        # this pool slot starts clean.
        acquired = await self._pool._pool.acquire()
        try:
            aid = _current_account_id.get()
            aid_str = "" if aid is None else str(aid)
            try:
                await acquired.execute(
                    "SELECT set_config('app.account_id', $1, false)", aid_str,
                )
            except Exception as e:
                # Don't fail the whole call if set_config doesn't apply
                # (e.g. running against an old DB that doesn't recognize
                # the GUC).  Log debug only; the query will still run
                # without the tenant scope, which is the legacy behavior.
                logger.debug("set_config app.account_id failed: %s", e)
            yield acquired
        finally:
            try:
                await acquired.execute(
                    "SELECT set_config('app.account_id', '', false)",
                )
            except Exception:
                pass
            await self._pool._pool.release(acquired)

    async def execute(self, sql: str, params: tuple = ()) -> _PgCursor:
        pg_sql = _sqlite_to_pg_sql(sql)
        if not pg_sql.strip():
            return _PgCursor([])

        # If the translated SQL contains an ``::interval`` cast we
        # might be passing a SQLite-style string like ``"-30 days"`` as
        # a parameter — asyncpg can't bind that to INTERVAL.  Convert
        # such values to ``timedelta`` first; non-interval params are
        # left untouched.
        if "::interval" in pg_sql:
            params = tuple(_maybe_coerce_interval_param(p) for p in params)

        stripped = pg_sql.strip().upper()

        async with self._acquire() as conn:
            # For SELECT / RETURNING, fetch rows.  rowcount = len(rows).
            if stripped.startswith("SELECT") or "RETURNING" in stripped:
                rows = await conn.fetch(pg_sql, *params)
                return _PgCursor(rows, rowcount=len(rows))

            # For INSERT with lastrowid, append RETURNING id.
            #
            # We skip the RETURNING augmentation when the statement
            # already carries an ON CONFLICT clause: those targets
            # are typically composite-PK junction tables
            # (driver_document_notifications, user_companies, etc.)
            # that have no ``id`` column at all, and asyncpg's failure
            # mode here is to abort the entire surrounding transaction
            # so the caller's next statement also errors out with
            # ``InFailedSQLTransactionError``.  Plain execute() returns
            # a status row count, which is what ``cur.rowcount > 0``
            # callers (record_doc_notification, assign_user_company)
            # actually need to gate fresh-vs-dedup.
            if (
                stripped.startswith("INSERT")
                and "RETURNING" not in stripped
                and "ON CONFLICT" not in stripped
            ):
                try:
                    returning_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
                    row = await conn.fetchrow(returning_sql, *params)
                    lastrowid = row["id"] if row else 0
                    return _PgCursor(
                        [row] if row else [],
                        lastrowid=lastrowid,
                        rowcount=1 if row else 0,
                    )
                except Exception:
                    # If RETURNING id fails (no id column), fall back.
                    status = await conn.execute(pg_sql, *params)
                    return _PgCursor([], rowcount=_parse_status_rowcount(status))

            # UPDATE / DELETE / DDL — asyncpg returns a status string
            # like "UPDATE 3" / "DELETE 12" whose tail integer is the
            # affected row count.  Parsing it lets callers gate on
            # ``cur.rowcount > 0`` (used in maintenance, parking,
            # permissions, geofence, etc.).
            status = await conn.execute(pg_sql, *params)
            return _PgCursor([], rowcount=_parse_status_rowcount(status))

    async def executemany(self, sql: str, params_seq) -> "_PgCursor":
        """Run the same statement many times — aiosqlite-compatible.

        Now holds one pool connection for the entire batch instead of
        acquiring per row — much cheaper for the per-cycle bulk UPDATE
        in maintenance, etc.  Without this, a 500-row batch would do
        1000 acquire/release round trips against the pool.
        """
        params_list = list(params_seq)
        if not params_list:
            return _PgCursor([])
        # Pin the batch to one connection so all rows share the same
        # session (critical for ``app.account_id`` consistency and to
        # avoid pool-acquire churn).
        async with self._acquire() as conn:
            sub_proxy = _PgConnection(self._pool, pinned_conn=conn)
            for p in params_list:
                await sub_proxy.execute(sql, tuple(p))
        return _PgCursor([])

    async def executescript(self, script: str) -> None:
        """Execute a semicolon-separated SQL script (used for schema creation).

        Strips ``--`` line comments before splitting so a stray ``;`` inside
        a comment ("``-- foo; bar``") doesn't tear a CREATE TABLE in two.

        All statements run on a single acquired connection so multi-step
        DDL (e.g. CREATE TABLE followed by CREATE INDEX on the same
        table) shares one session.
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

        async with self._acquire() as conn:
            for stmt in clean_script.split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                pg_stmt = _sqlite_to_pg_sql(stmt)
                if not pg_stmt.strip():
                    continue
                try:
                    await conn.execute(pg_stmt)
                except Exception as e:
                    # CREATE IF NOT EXISTS can still fail in PG on concurrent init
                    if "already exists" in str(e).lower():
                        logger.debug("Schema object already exists (skipping): %s", e)
                    else:
                        raise

    async def commit(self) -> None:
        """No-op — transaction lifecycle lives on ``Database.transaction()``."""
        pass

    async def rollback(self) -> None:
        """No-op — transaction lifecycle lives on ``Database.transaction()``."""
        pass

    async def close(self) -> None:
        """No long-held resources to release.

        The legacy implementation released a pinned connection back to
        the pool on close().  In the pool-proxy world there's nothing
        to release — each ``execute()`` already returned its connection.
        Constructor-pinned proxies are managed by their owner
        (e.g. ``Database.acquire()``'s context manager).
        """
        pass


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
        """Return a *constructor-pinned* proxy holding one pool conn.

        Used by ``Database.acquire()`` and the legacy migration init
        path.  Caller must ``await proxy._release()`` when done so the
        underlying asyncpg connection returns to the pool.

        Most callers should NOT use this — pass the pool-backed
        ``Database._db`` proxy instead, which acquires per operation.
        """
        conn = await self._pool.acquire()
        proxy = _PgConnection(self, pinned_conn=conn)
        return proxy

    async def release(self, conn) -> None:
        """Release an asyncpg.Connection (or a constructor-pinned proxy) back to the pool."""
        # Accept either a raw asyncpg conn or our _PgConnection wrapper.
        target = getattr(conn, "_construct_pinned", None)
        if target is not None:
            await self._pool.release(target)
        else:
            await self._pool.release(conn)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_PgConnection]:
        """Yield a constructor-pinned proxy backed by one pool connection.

        Used by ``Database.acquire()`` for read parallelism (``read_all``
        / ``read_one``).  The proxy is owned by the caller for the
        duration of the ``with`` block, then released on exit.
        """
        proxy = await self.acquire_connection()
        try:
            yield proxy
        finally:
            await self.release(proxy)


async def open_pg_pool(dsn: str, pool_size: int = 4) -> _PgPool:
    """Create and initialize a PostgreSQL connection pool."""
    pool = _PgPool(dsn, min_size=2, max_size=max(pool_size, 4))
    await pool.initialize()
    return pool
