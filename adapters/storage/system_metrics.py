"""System capacity metrics — platform-scoped resource history.

Feeds the operator console's Capacity page (system.4truck.us): a 60s
sampler (capabilities/platform/capacity/sampler.py) records host CPU /
RAM / disk / network, Postgres, Redis and request-rate samples here;
the same sampler folds finished hours into the hourly tier (avg + PEAK
per signal — capacity planning reads peaks, never averages).

Three tables, all PLATFORM scope (one server, not per-tenant):

  * ``system_metrics_minute`` — raw 60s samples, short retention.
  * ``system_metrics_hourly`` — avg+peak per hour, long retention;
    the tier every chart and the headroom math read.
  * ``account_usage_daily``   — per-ACCOUNT request counts flushed
    nightly from the Redis metering hash (which customer costs what).

Retention windows are declared in capabilities/platform/capacity/
retention.py through the data-lifecycle hub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # structural typing only — composed into Database
    class _MixinBase:
        _db: Any
else:
    _MixinBase = object

# Column order shared by INSERT + the hourly rollup read.
_MINUTE_COLS = (
    "ts", "cpu_pct", "load1", "mem_pct", "mem_used_mb",
    "disk_pct", "disk_used_gb", "disk_busy_pct",
    "net_rx_kbps", "net_tx_kbps",
    "pg_connections", "pg_size_mb", "redis_mb",
    "requests_min", "queue_depth",
    "vehicles_active", "accounts_active",
)


class SystemMetricsMixin(_MixinBase):
    """Platform capacity samples + per-account usage rollups."""

    # ── writes ────────────────────────────────────────────────────

    async def insert_system_metrics_minute(self, sample: dict) -> None:
        """Insert one 60s sample.  ``sample`` keys mirror _MINUTE_COLS;
        missing values land as NULL (a probe that failed this tick must
        not sink the whole row)."""
        cols = ", ".join(_MINUTE_COLS)
        marks = ", ".join("?" for _ in _MINUTE_COLS)
        await self._db.execute(
            f"INSERT INTO system_metrics_minute ({cols}) VALUES ({marks}) "
            "ON CONFLICT (ts) DO NOTHING",
            tuple(sample.get(c) for c in _MINUTE_COLS),
        )
        await self._db.commit()

    async def rollup_system_metrics_hour(self, hour_iso: str) -> bool:
        """Fold one finished hour of minute rows into the hourly tier.

        ``hour_iso`` — the hour bucket start, e.g. ``2026-07-17T09:00``.
        Minute rows are matched by the zero-padded prefix
        (``2026-07-17T09:%``), so the LIKE is exact — no lexicographic
        edge cases.  Upsert — safe to re-run.  Returns False when the
        hour had no samples (sampler down)."""
        hour_prefix = hour_iso[:13]          # "2026-07-17T09"
        cur = await self._db.execute(
            """SELECT COUNT(*),
                      AVG(cpu_pct),      MAX(cpu_pct),
                      AVG(mem_pct),      MAX(mem_pct),
                      AVG(disk_busy_pct), MAX(disk_busy_pct),
                      AVG(requests_min), MAX(requests_min),
                      AVG(queue_depth),  MAX(queue_depth),
                      AVG(net_rx_kbps),  MAX(net_rx_kbps),
                      AVG(net_tx_kbps),  MAX(net_tx_kbps),
                      MAX(load1),
                      MAX(pg_connections),
                      MAX(disk_pct), MAX(disk_used_gb), MAX(pg_size_mb),
                      MAX(redis_mb), MAX(mem_used_mb),
                      MAX(vehicles_active), MAX(accounts_active)
                 FROM system_metrics_minute
                WHERE ts LIKE ?""",
            (hour_prefix + ":%",),
        )
        r = await cur.fetchone()
        if not r or not r[0]:
            return False
        vals = tuple(
            round(float(v), 2) if isinstance(v, float) else v
            for v in tuple(r)[1:]
        )
        await self._db.execute(
            """INSERT INTO system_metrics_hourly
               (hour, avg_cpu_pct, peak_cpu_pct, avg_mem_pct, peak_mem_pct,
                avg_disk_busy_pct, peak_disk_busy_pct,
                avg_requests_min, peak_requests_min,
                avg_queue_depth, peak_queue_depth,
                avg_net_rx_kbps, peak_net_rx_kbps,
                avg_net_tx_kbps, peak_net_tx_kbps,
                peak_load1, peak_pg_connections,
                disk_pct, disk_used_gb, pg_size_mb, redis_mb, mem_used_mb,
                vehicles_active, accounts_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (hour) DO UPDATE SET
                 avg_cpu_pct=excluded.avg_cpu_pct, peak_cpu_pct=excluded.peak_cpu_pct,
                 avg_mem_pct=excluded.avg_mem_pct, peak_mem_pct=excluded.peak_mem_pct,
                 avg_disk_busy_pct=excluded.avg_disk_busy_pct, peak_disk_busy_pct=excluded.peak_disk_busy_pct,
                 avg_requests_min=excluded.avg_requests_min, peak_requests_min=excluded.peak_requests_min,
                 avg_queue_depth=excluded.avg_queue_depth, peak_queue_depth=excluded.peak_queue_depth,
                 avg_net_rx_kbps=excluded.avg_net_rx_kbps, peak_net_rx_kbps=excluded.peak_net_rx_kbps,
                 avg_net_tx_kbps=excluded.avg_net_tx_kbps, peak_net_tx_kbps=excluded.peak_net_tx_kbps,
                 peak_load1=excluded.peak_load1, peak_pg_connections=excluded.peak_pg_connections,
                 disk_pct=excluded.disk_pct, disk_used_gb=excluded.disk_used_gb,
                 pg_size_mb=excluded.pg_size_mb, redis_mb=excluded.redis_mb,
                 mem_used_mb=excluded.mem_used_mb,
                 vehicles_active=excluded.vehicles_active,
                 accounts_active=excluded.accounts_active""",
            (hour_iso, *vals),
        )
        await self._db.commit()
        return True

    async def upsert_usage_breakdown_daily(
        self, day: str, dim: str, counts: dict[str, int],
    ) -> int:
        """Flush one day's per-``dim`` request counts (``dim`` =
        'surface' | 'feature').  Same GREATEST semantics as the
        per-account flush — the Redis hash is cumulative for the day,
        so a re-flush never regresses persisted history."""
        n = 0
        for key, requests in counts.items():
            await self._db.execute(
                """INSERT INTO usage_breakdown_daily (day, dim, key, requests)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (day, dim, key) DO UPDATE SET
                     requests = GREATEST(usage_breakdown_daily.requests, excluded.requests)""",
                (day, dim, str(key)[:40], int(requests)),
            )
            n += 1
        await self._db.commit()
        return n

    async def get_usage_breakdown(self, since_day: str, dim: str) -> dict[str, int]:
        """Summed per-key counts for ``dim`` since a UTC day (inclusive)."""
        cur = await self._db.execute(
            """SELECT key, SUM(requests) FROM usage_breakdown_daily
                WHERE day >= ? AND dim = ? GROUP BY key""",
            (since_day, dim),
        )
        return {str(r[0]): int(r[1]) for r in await cur.fetchall()}

    async def upsert_account_usage_daily(
        self, day: str, requests_by_account: dict[int, int],
    ) -> int:
        """Flush one day's per-account request counts (Redis hash →
        durable rows).  Additive upsert: a re-flush after a partial day
        never loses already-persisted counts (MAX wins — the hash is
        cumulative for the day)."""
        n = 0
        for account_id, requests in requests_by_account.items():
            await self._db.execute(
                """INSERT INTO account_usage_daily (day, account_id, requests)
                   VALUES (?, ?, ?)
                   ON CONFLICT (day, account_id) DO UPDATE SET
                     requests = GREATEST(account_usage_daily.requests, excluded.requests)""",
                (day, int(account_id), int(requests)),
            )
            n += 1
        await self._db.commit()
        return n

    # ── reads (Capacity page) ─────────────────────────────────────

    async def get_system_metrics_latest(self) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM system_metrics_minute ORDER BY ts DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_system_metrics_minutes(self, since_iso: str) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM system_metrics_minute WHERE ts >= ? ORDER BY ts",
            (since_iso,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_system_metrics_hours(self, since_iso: str) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM system_metrics_hourly WHERE hour >= ? ORDER BY hour",
            (since_iso,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_account_usage(
        self, since_day: str, account_id: int | None = None,
    ) -> list[dict]:
        params: list[Any] = [since_day]
        where = "day >= ?"
        if account_id is not None:
            where += " AND account_id = ?"
            params.append(account_id)
        cur = await self._db.execute(
            f"SELECT day, account_id, requests FROM account_usage_daily "
            f"WHERE {where} ORDER BY day, account_id",
            tuple(params),
        )
        return [dict(r) for r in await cur.fetchall()]

    # ── prunes (retention hub executors) ──────────────────────────

    async def prune_system_metrics_minute(self, cutoff_iso: str) -> int:
        cur = await self._db.execute(
            "DELETE FROM system_metrics_minute WHERE ts < ?", (cutoff_iso,),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def prune_system_metrics_hourly(self, cutoff_iso: str) -> int:
        cur = await self._db.execute(
            "DELETE FROM system_metrics_hourly WHERE hour < ?", (cutoff_iso,),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def prune_account_usage_daily(self, cutoff_day: str) -> int:
        cur = await self._db.execute(
            "DELETE FROM account_usage_daily WHERE day < ?", (cutoff_day,),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def prune_usage_breakdown_daily(self, cutoff_day: str) -> int:
        cur = await self._db.execute(
            "DELETE FROM usage_breakdown_daily WHERE day < ?", (cutoff_day,),
        )
        await self._db.commit()
        return cur.rowcount or 0
