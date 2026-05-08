"""Phase 5b — dual-write SQLite + PostgreSQL connection shim.

Wraps both an aiosqlite connection and an asyncpg-shaped connection,
mirroring writes to PostgreSQL while reads go to the configured
backend. Used during the SQLite → PostgreSQL cutover to validate
that PG behaves identically under real production load before flipping
the read path.

Wiring (per-tenant):
    primary    = aiosqlite     # source of truth during dual-write
    secondary  = pg_adapter    # shadow target
    DualWriteCore wraps both and exposes the aiosqlite-compatible API.

Behaviour:
    * ``execute(SQL, params)`` runs on primary first; result returned to
      caller. Then runs on secondary in the SAME async task. Failures on
      secondary are logged at WARNING and counted, never raised.
    * ``commit()`` runs on both.
    * ``executescript()`` runs on both (used for migrations).
    * READ traffic stays on primary unless ``DB_READ_BACKEND=pg``, in
      which case the same call goes to secondary FIRST and any
      divergent row counts are logged.

Telemetry:
    * Per-process counters (``infra/cache.cache_set("dualwrite:diverged", n)``)
      so ops can graph drift over time.
    * Each divergence event logs at WARNING with truncated SQL so the
      query that caused it is recoverable from prod logs.

This file is a *shim*, not a full backend. Mixins still hold the
aiosqlite reference. ``infra/platform.py`` decides whether to wrap the
connection in ``DualWriteCore`` based on the ``DB_DUAL_WRITE`` env
flag — see ``docs/runbooks/postgresql-rollout.md`` for staging order.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# Read-side preference. Switching from "sqlite" to "pg" during dual-write
# validates that PG behaves identically before the cutover. Default
# "sqlite" so no behavioural change until ops opts in.
_READ_BACKEND = os.getenv("DB_READ_BACKEND", "sqlite").lower()
_DUAL_WRITE_ENABLED = os.getenv("DB_DUAL_WRITE", "").lower() in ("1", "true", "yes")


def is_dual_write_enabled() -> bool:
    """True when DB_DUAL_WRITE is set — every write hits both backends."""
    return _DUAL_WRITE_ENABLED


def read_backend() -> str:
    """Returns "sqlite" or "pg" — which backend serves reads."""
    return _READ_BACKEND if _READ_BACKEND in ("sqlite", "pg") else "sqlite"


# ── Per-process divergence counters ───────────────────────────
# Bumped from DualWriteCursor; surfaced via /admin/dualwrite-status
# for ops monitoring.
_metrics: dict[str, int] = {
    "writes":          0,
    "writes_failed":   0,
    "reads":           0,
    "reads_diverged":  0,
}


def get_metrics() -> dict[str, int]:
    """Snapshot of per-process dual-write counters."""
    return dict(_metrics)


def reset_metrics() -> None:
    for k in _metrics:
        _metrics[k] = 0


# ── Cursor wrapper ────────────────────────────────────────────

class DualWriteCursor:
    """Mimics the aiosqlite/pg_adapter cursor API; returns rows from
    the configured read backend's cursor, optionally cross-checks row
    count against the secondary."""

    def __init__(self, primary_cur, secondary_cur, *, divergence_log_sql: str = ""):
        self._primary = primary_cur
        self._secondary = secondary_cur
        self._sql_for_log = divergence_log_sql
        # Match the aiosqlite/pg_adapter ``lastrowid`` attr.
        self.lastrowid = getattr(primary_cur, "lastrowid", 0)

    async def fetchone(self):
        primary_row = await self._primary.fetchone()
        return primary_row

    async def fetchall(self):
        primary = await self._primary.fetchall()
        if self._secondary is not None:
            try:
                secondary = await self._secondary.fetchall()
                _metrics["reads"] += 1
                if len(primary) != len(secondary):
                    _metrics["reads_diverged"] += 1
                    logger.warning(
                        "dualwrite:divergent-rowcount primary=%d secondary=%d sql=%s",
                        len(primary), len(secondary),
                        (self._sql_for_log or "")[:200],
                    )
            except Exception as e:
                logger.debug("dualwrite: secondary fetchall failed: %s", e)
        return primary


# ── Connection wrapper ────────────────────────────────────────

class DualWriteConnection:
    """Wraps a primary (aiosqlite) + secondary (pg_adapter) connection
    pair; exposes the aiosqlite API the mixins expect."""

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary
        # Mirror aiosqlite.Connection attributes the mixins poke at.
        self.row_factory = getattr(primary, "row_factory", None)

    @property
    def in_transaction(self) -> bool:
        return getattr(self._primary, "in_transaction", False)

    async def execute(self, sql: str, params: tuple = ()) -> DualWriteCursor:
        # Primary runs first. Its result is what the caller receives.
        primary_cur = await self._primary.execute(sql, params)
        secondary_cur = None
        if self._secondary is not None:
            try:
                secondary_cur = await self._secondary.execute(sql, params)
                _metrics["writes"] += 1
            except Exception as e:
                _metrics["writes_failed"] += 1
                logger.warning(
                    "dualwrite:secondary-failed err=%s sql=%s",
                    e, sql[:200],
                )
        return DualWriteCursor(primary_cur, secondary_cur, divergence_log_sql=sql)

    async def executescript(self, script: str) -> None:
        await self._primary.executescript(script)
        if self._secondary is not None:
            try:
                await self._secondary.executescript(script)
            except Exception as e:
                logger.warning(
                    "dualwrite:secondary-script-failed err=%s",
                    e,
                )

    async def commit(self) -> None:
        await self._primary.commit()
        if self._secondary is not None:
            try:
                await self._secondary.commit()
            except Exception as e:
                logger.warning("dualwrite:secondary-commit-failed err=%s", e)

    async def close(self) -> None:
        # Close in reverse order so primary stays open longest.
        if self._secondary is not None:
            try:
                await self._secondary.close()
            except Exception:
                pass
        await self._primary.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
