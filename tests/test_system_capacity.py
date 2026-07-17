"""Capacity monitoring — sampler, hourly rollup, metering, retention.

Covers the Phase-1 spine: minute samples land, the hourly tier folds
avg+PEAK correctly, per-account metering upserts additively, prunes
respect their cutoffs, and every probe/counter degrades gracefully
(no Redis, first psutil tick) instead of raising into the request
path or the scheduler.
"""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def db(pg_db):
    return pg_db


# ── Mixin: minute → hourly rollup ─────────────────────────────────

@pytest.mark.asyncio
async def test_minute_insert_and_hourly_rollup_avg_peak(db):
    # Three samples inside one hour with a known spread.
    for minute, cpu, req in (("09:05", 10.0, 60), ("09:25", 50.0, 120), ("09:45", 30.0, 90)):
        await db.insert_system_metrics_minute({
            "ts": f"2026-07-17T{minute}",
            "cpu_pct": cpu, "load1": 1.0, "mem_pct": 40.0, "mem_used_mb": 9000,
            "disk_pct": 27.0, "disk_used_gb": 51.0, "disk_busy_pct": 5.0,
            "net_rx_kbps": 100.0, "net_tx_kbps": 80.0,
            "pg_connections": 12, "pg_size_mb": 2048, "redis_mb": 60.0,
            "requests_min": req, "queue_depth": 0,
            "vehicles_active": 186, "accounts_active": 4,
        })
    # A sample in the NEXT hour must not leak into the rollup.
    await db.insert_system_metrics_minute({"ts": "2026-07-17T10:02", "cpu_pct": 99.0})

    assert await db.rollup_system_metrics_hour("2026-07-17T09:00") is True
    hours = await db.get_system_metrics_hours("2026-07-17T00:00")
    row = next(h for h in hours if h["hour"] == "2026-07-17T09:00")
    assert row["avg_cpu_pct"] == 30.0          # (10+50+30)/3
    assert row["peak_cpu_pct"] == 50.0         # peak, not average — capacity reads peaks
    assert row["peak_requests_min"] == 120
    assert row["vehicles_active"] == 186

    # Idempotent: re-running the fold changes nothing.
    assert await db.rollup_system_metrics_hour("2026-07-17T09:00") is True
    again = await db.get_system_metrics_hours("2026-07-17T09:00")
    assert next(h for h in again if h["hour"] == "2026-07-17T09:00")["avg_cpu_pct"] == 30.0

    # An hour with no samples reports False (sampler was down).
    assert await db.rollup_system_metrics_hour("2026-07-17T03:00") is False


@pytest.mark.asyncio
async def test_minute_insert_tolerates_missing_probes(db):
    # A row where every probe failed except the timestamp still lands.
    await db.insert_system_metrics_minute({"ts": "2026-07-17T11:11"})
    latest = await db.get_system_metrics_latest()
    assert latest["ts"] == "2026-07-17T11:11"
    assert latest["cpu_pct"] is None


# ── Mixin: per-account metering flush ─────────────────────────────

@pytest.mark.asyncio
async def test_account_usage_flush_is_additive_never_regresses(db):
    day = "2026-07-16"
    await db.upsert_account_usage_daily(day, {1: 500, 2: 80})
    # A later flush with a LOWER count (e.g. Redis hash evicted and the
    # day restarted counting) must not shrink persisted history.
    await db.upsert_account_usage_daily(day, {1: 300, 2: 120})
    rows = await db.get_account_usage("2026-07-01")
    by_acct = {r["account_id"]: r["requests"] for r in rows}
    assert by_acct[1] == 500     # GREATEST kept the high-water mark
    assert by_acct[2] == 120     # normal forward progress applied


# ── Mixin: prunes ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prunes_respect_cutoffs(db):
    await db.insert_system_metrics_minute({"ts": "2026-07-10T00:00"})
    await db.insert_system_metrics_minute({"ts": "2026-07-17T00:00"})
    assert await db.prune_system_metrics_minute("2026-07-15T00:00") == 1
    remaining = await db.get_system_metrics_minutes("2026-01-01T00:00")
    assert [r["ts"] for r in remaining] == ["2026-07-17T00:00"]

    await db.upsert_account_usage_daily("2026-01-01", {1: 5})
    await db.upsert_account_usage_daily("2026-07-16", {1: 9})
    assert await db.prune_account_usage_daily("2026-06-01") == 1
    rows = await db.get_account_usage("2020-01-01")
    assert [r["day"] for r in rows] == ["2026-07-16"]


# ── Metering helpers ──────────────────────────────────────────────

def test_surface_mapping_is_interface_not_persona():
    from capabilities.platform.capacity.requests import surface_for_host

    # Every dashboard subdomain is the SAME SPA — one surface.
    for host in ("dash.4truck.us", "fleet.4truck.us", "safety.4truck.us",
                 "accounting.4truck.us", "hr.4truck.us", "dispatch.4truck.us"):
        assert surface_for_host(host) == "dashboard"
    assert surface_for_host("app.4truck.us") == "miniapp"
    assert surface_for_host("api.4truck.us:443") == "api"
    assert surface_for_host("system.4truck.us") == "console"
    assert surface_for_host("bot.4truck.us") == "bot"
    assert surface_for_host("") == "dashboard"


@pytest.mark.asyncio
async def test_metering_is_noop_without_redis():
    """Counting and reading must never raise when Redis is absent —
    metering can't add a failure mode to the request path."""
    from capabilities.platform.capacity import requests as metering

    await metering.count_request("dash.4truck.us", 42)      # no raise
    assert await metering.requests_last_minute() is None    # unavailable ≠ 0
    assert await metering.account_counts("2026-07-17") == {}


# ── Sampler probes ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_host_probe_warmup_then_rates(db):
    import capabilities.platform.capacity.sampler as sampler

    sampler._prev = None
    first = sampler._host_probe()
    # Warm-up tick: rate metrics are NULL (0.0 would read as "idle").
    assert first["cpu_pct"] is None
    assert first["mem_used_mb"] > 0
    second = sampler._host_probe()
    assert isinstance(second["cpu_pct"], float)
    assert second["net_rx_kbps"] is not None


@pytest.mark.asyncio
async def test_sampler_job_end_to_end(db, monkeypatch):
    """The scheduler job writes a real row through a real Database."""
    import infra.platform as platform
    import capabilities.platform.capacity.sampler as sampler

    monkeypatch.setattr(platform, "_db", db, raising=False)
    sampler._prev = None
    await sampler.job_capacity_sample()
    latest = await db.get_system_metrics_latest()
    assert latest is not None
    # Platform counts came from the seeded test DB (ints, not NULL).
    assert isinstance(latest["accounts_active"], int)
