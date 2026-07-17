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
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def db(pg_db):
    return pg_db


@pytest_asyncio.fixture
async def capacity_app(pg_db, monkeypatch):
    """API + a system-owner JWT + a plain-owner JWT (same pattern as the
    other /system suites)."""
    db = pg_db
    acct = await db.create_account("Capacity Test Co")
    op = await db.create_user(910001, acct.id, role=Role.OWNER)
    non_op = await db.create_user(910002, acct.id, role=Role.OWNER)

    import capabilities.permissions.roles as perms
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {op.telegram_id})

    import infra.platform as _cp
    old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    transport = ASGITransport(app=create_api())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client, "db": db, "acct": acct,
            "op": {"Authorization": f"Bearer {create_jwt(op.telegram_id, acct.id, 'owner')}"},
            "non_op": {"Authorization": f"Bearer {create_jwt(non_op.telegram_id, acct.id, 'owner')}"},
        }
    _cp._db = old


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


# ── Feature/surface breakdown ─────────────────────────────────────

def test_feature_for_path_route_families():
    from capabilities.platform.capacity.requests import feature_for_path

    assert feature_for_path("/api/vehicles/103/inventory") == "vehicles"
    assert feature_for_path("/api/v1/work-orders/12/attachments") == "work-orders"
    assert feature_for_path("/api/ai/ask") == "ai"
    assert feature_for_path("/api/system/capacity/overview") == "system"
    # Cardinality guard: junk shapes fold into 'other'.
    assert feature_for_path("/api/%2e%2e/etc") == "other"
    assert feature_for_path("/api/" + "x" * 50) == "other"
    assert feature_for_path("") == "other"


@pytest.mark.asyncio
async def test_usage_breakdown_upsert_read_prune(db):
    await db.upsert_usage_breakdown_daily("2026-07-15", "feature", {"vehicles": 100, "ai": 20})
    await db.upsert_usage_breakdown_daily("2026-07-16", "feature", {"vehicles": 50})
    # GREATEST: a lower re-flush never regresses.
    await db.upsert_usage_breakdown_daily("2026-07-16", "feature", {"vehicles": 30})
    await db.upsert_usage_breakdown_daily("2026-07-16", "surface", {"dashboard": 999})

    counts = await db.get_usage_breakdown("2026-07-15", "feature")
    assert counts == {"vehicles": 150, "ai": 20}          # summed across days
    assert await db.get_usage_breakdown("2026-07-16", "feature") == {"vehicles": 50}
    # Dimensions never bleed into each other.
    assert await db.get_usage_breakdown("2026-07-15", "surface") == {"dashboard": 999}

    assert await db.prune_usage_breakdown_daily("2026-07-16") == 2
    assert await db.get_usage_breakdown("2026-07-01", "feature") == {"vehicles": 50}


@pytest.mark.asyncio
async def test_capacity_breakdown_endpoint_windows(capacity_app):
    from datetime import datetime, timedelta, timezone

    s = capacity_app
    db = s["db"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    await db.upsert_usage_breakdown_daily(old, "feature", {"vehicles": 400})
    await db.upsert_usage_breakdown_daily(today, "feature", {"vehicles": 25})

    r = await s["client"].get("/api/system/capacity/breakdown?days=1", headers=s["op"])
    assert r.status_code == 200
    assert r.json()["features"].get("vehicles") == 25      # today only

    r = await s["client"].get("/api/system/capacity/breakdown?days=30", headers=s["op"])
    assert r.json()["features"].get("vehicles") == 425     # window sums

    r = await s["client"].get("/api/system/capacity/breakdown?days=1", headers=s["non_op"])
    assert r.status_code == 403


# ── Threshold alerts ──────────────────────────────────────────────

def _cpu_rows(avg: float) -> list[dict]:
    return [{"cpu_pct": avg, "mem_pct": 40.0, "queue_depth": 0} for _ in range(5)]


def test_evaluate_hysteresis_zones():
    from capabilities.platform.capacity import alerts

    fresh = {"ts": "2026-07-17T09:00", "disk_pct": 30.0}
    assert alerts.evaluate(_cpu_rows(92.0), fresh, None)["cpu"]["state"] == "breach"
    assert alerts.evaluate(_cpu_rows(82.0), fresh, None)["cpu"]["state"] == "hold"
    assert alerts.evaluate(_cpu_rows(60.0), fresh, None)["cpu"]["state"] == "clear"
    # Ingest stall zones: ≥60 breach, 30–60 hold, <30 clear.
    assert alerts.evaluate([], fresh, 90.0)["ingest"]["state"] == "breach"
    assert alerts.evaluate([], fresh, 45.0)["ingest"]["state"] == "hold"
    assert alerts.evaluate([], fresh, 5.0)["ingest"]["state"] == "clear"


def test_evaluate_sampler_stall():
    from datetime import datetime, timedelta, timezone

    from capabilities.platform.capacity import alerts

    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
    sig = alerts.evaluate([], {"ts": old}, None)
    assert sig["sampler"]["state"] == "breach"


class _FakeBot:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id, text):  # noqa: ANN001
        self.sent.append(text)


class _FakeDB:
    """Just enough surface for check_and_alert."""

    def __init__(self, rows, latest):
        self.rows, self.latest = rows, latest
        self._db = self          # ingest probe hits _db.execute → raise

    async def get_system_metrics_minutes(self, since):
        return self.rows

    async def get_system_metrics_latest(self):
        return self.latest

    async def execute(self, *_a, **_k):
        raise RuntimeError("no vehicle_state in fake")


@pytest_asyncio.fixture
def alert_env(monkeypatch):
    """In-memory breach state + captured sends + owners allowlisted."""
    from types import SimpleNamespace

    import capabilities.permissions.roles as perms
    import infra.cache as cache
    from capabilities.platform.capacity import alerts

    active: set[str] = set()

    async def is_active(s):
        return s in active

    async def set_active(s):
        active.add(s)

    async def clear_active(s):
        active.discard(s)

    monkeypatch.setattr(alerts, "_is_active", is_active)
    monkeypatch.setattr(alerts, "_set_active", set_active)
    monkeypatch.setattr(alerts, "_clear_active", clear_active)
    monkeypatch.setattr(cache, "is_available", lambda: True)
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {111, 222})
    bot = _FakeBot()
    return {"app": SimpleNamespace(bot=bot), "bot": bot, "active": active}


@pytest.mark.asyncio
async def test_alert_fires_once_then_recovers(alert_env):
    from capabilities.platform.capacity import alerts

    fresh_latest = None  # no sampler-stall signal in play

    # Breach → exactly ONE alert to EACH operator.
    db = _FakeDB(_cpu_rows(92.0), fresh_latest)
    sent = await alerts.check_and_alert(db, alert_env["app"])
    assert len(sent) == 1 and "CPU" in sent[0] and "🔴" in sent[0]
    assert len(alert_env["bot"].sent) == 2          # two operators
    assert "cpu" in alert_env["active"]

    # Still breached → silence (no 5-minute spam).
    sent = await alerts.check_and_alert(db, alert_env["app"])
    assert sent == []

    # Hysteresis zone (82%) → still silence, state stays active.
    sent = await alerts.check_and_alert(_FakeDB(_cpu_rows(82.0), fresh_latest), alert_env["app"])
    assert sent == [] and "cpu" in alert_env["active"]

    # Clear (60%) → one recovery, state dropped.
    sent = await alerts.check_and_alert(_FakeDB(_cpu_rows(60.0), fresh_latest), alert_env["app"])
    assert len(sent) == 1 and "🟢" in sent[0]
    assert "cpu" not in alert_env["active"]

    # Cleared and still clear → silence.
    sent = await alerts.check_and_alert(_FakeDB(_cpu_rows(60.0), fresh_latest), alert_env["app"])
    assert sent == []


@pytest.mark.asyncio
async def test_alerts_skip_entirely_without_redis(monkeypatch, alert_env):
    """No shared state → no dedupe → the only safe move is silence."""
    import infra.cache as cache

    from capabilities.platform.capacity import alerts

    monkeypatch.setattr(cache, "is_available", lambda: False)
    sent = await alerts.check_and_alert(_FakeDB(_cpu_rows(99.0), None), alert_env["app"])
    assert sent == [] and alert_env["bot"].sent == []


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


# ── /system/capacity API ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_capacity_endpoints_are_operator_only(capacity_app):
    s = capacity_app
    for path in ("/api/system/capacity/overview",
                 "/api/system/capacity/series?window=24h",
                 "/api/system/capacity/accounts"):
        r = await s["client"].get(path, headers=s["non_op"])
        assert r.status_code == 403, path
        r = await s["client"].get(path)
        assert r.status_code == 401, path


@pytest.mark.asyncio
async def test_capacity_overview_and_series_shapes(capacity_app):
    s = capacity_app
    db = s["db"]
    # Seed a week of hourly peaks (cpu climbing 40→52%) + one minute row.
    for d in range(10, 17):
        for h in (9, 15):
            await db._db.execute(
                """INSERT INTO system_metrics_hourly
                   (hour, peak_cpu_pct, peak_mem_pct, disk_used_gb, disk_pct,
                    vehicles_active, accounts_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (hour) DO NOTHING""",
                (f"2026-07-{d}T{h:02d}:00", 40.0 + d, 35.0, 50.0 + d * 0.5, 26.0, 180, 4),
            )
    await db._db.commit()
    await db.insert_system_metrics_minute({
        "ts": "2026-07-17T08:30", "cpu_pct": 44.0, "mem_pct": 36.0,
        "vehicles_active": 180, "accounts_active": 4,
    })

    r = await s["client"].get("/api/system/capacity/overview", headers=s["op"])
    assert r.status_code == 200
    body = r.json()
    assert body["latest"]["ts"] == "2026-07-17T08:30"
    hr = body["headroom"]
    assert hr["watch_pct"] == 70.0 and hr["act_pct"] == 85.0
    # Headroom math sanity when the 7d window catches seeded peaks: the
    # binding resource is CPU and the estimate scales vehicles linearly
    # (vehicles × 70 / peak).  With sparse seeded hours confidence is low.
    if hr["binding_resource"] == "cpu" and hr["vehicles_at_watch"] is not None:
        assert hr["vehicles_at_watch"] == int(180 * 70.0 / hr["peak_cpu_pct"])
        assert hr["confidence"] == "low"

    r = await s["client"].get("/api/system/capacity/series?window=30d", headers=s["op"])
    assert r.status_code == 200
    assert r.json()["tier"] == "hourly"
    assert len(r.json()["points"]) >= 10
    r = await s["client"].get("/api/system/capacity/series?window=24h", headers=s["op"])
    assert r.json()["tier"] == "minute"


@pytest.mark.asyncio
async def test_capacity_accounts_labels_and_filter(capacity_app):
    s = capacity_app
    db = s["db"]
    acct = s["acct"]
    await db.upsert_account_usage_daily("2026-07-15", {acct.id: 1200, 999999: 5})
    r = await s["client"].get(
        f"/api/system/capacity/accounts?days=30&account_id={acct.id}",
        headers=s["op"],
    )
    assert r.status_code == 200
    body = r.json()
    assert all(row["account_id"] == acct.id for row in body["rows"])
    labels = {a["account_id"]: a["name"] for a in body["accounts"]}
    assert labels.get(acct.id) == "Capacity Test Co"


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
