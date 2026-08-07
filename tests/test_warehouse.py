"""telemetry warehouse tests.

Covers:

  * ``WarehouseMixin`` upserts + reads (vehicle_state_live, safety_event_log,
    driver_efficiency_day, vehicle_telemetry_hourly).
  * Idempotency (re-ingesting the same Samsara payload doesn't dupe).
  * Filter contracts on the readers (company, vehicle_nums, days window).
  * ``warehouse_reader`` flag gating + Samsara fallback.

No live Samsara: every ingestor test stubs ``get_client`` so we exercise
the SQL paths against an in-memory tenant DB.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database
from features.vehicles.warehouse import readers as warehouse_reader, aggregator
from capabilities.integrations.samsara import sync


# ── tenant DB fixture ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def tenant(pg_db):
    """Per-test PG database — same engine as production.

    Forwards to the shared ``pg_db`` fixture (testcontainers PG with
    per-test schema reset).
    """
    yield pg_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── vehicle_state_live ────────────────────────────────────────────────────


class TestVehicleState:
    @pytest.mark.asyncio
    async def test_upsert_and_read(self, tenant):
        rows = [
            {"vehicle_id": "v1", "vehicle_name": "T-1", "company_code": "A",
             "lat": 40.0, "lon": -74.0, "speed_mph": 55, "engine_state": "On"},
            {"vehicle_id": "v2", "vehicle_name": "T-2", "company_code": "B",
             "lat": 41.0, "lon": -75.0},
        ]
        n = await tenant.upsert_vehicle_state(1, rows)
        assert n == 2

        all_rows = await tenant.get_vehicle_state(1)
        assert len(all_rows) == 2
        # Ordered by vehicle_name.
        assert [r["vehicle_name"] for r in all_rows] == ["T-1", "T-2"]
        assert all_rows[0]["lat"] == 40.0

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, tenant):
        await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "v1", "vehicle_name": "T-1", "speed_mph": 55},
        ])
        await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "v1", "vehicle_name": "T-1", "speed_mph": 0,
             "engine_state": "Off"},
        ])
        rows = await tenant.get_vehicle_state(1)
        assert len(rows) == 1
        assert rows[0]["speed_mph"] == 0
        assert rows[0]["engine_state"] == "Off"

    @pytest.mark.asyncio
    async def test_filters(self, tenant):
        await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "v1", "vehicle_name": "T-1", "company_code": "A"},
            {"vehicle_id": "v2", "vehicle_name": "T-2", "company_code": "A"},
            {"vehicle_id": "v3", "vehicle_name": "T-3", "company_code": "B"},
        ])
        a_only = await tenant.get_vehicle_state(1, company="A")
        assert {r["vehicle_id"] for r in a_only} == {"v1", "v2"}

        picked = await tenant.get_vehicle_state(1, vehicle_nums=["T-1", "T-3"])
        assert {r["vehicle_id"] for r in picked} == {"v1", "v3"}

        single = await tenant.get_vehicle_state(1, vehicle_id="v2")
        assert len(single) == 1 and single[0]["vehicle_name"] == "T-2"

    @pytest.mark.asyncio
    async def test_blank_vehicle_id_skipped(self, tenant):
        n = await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "", "vehicle_name": "Ghost"},
            {"vehicle_id": "v1", "vehicle_name": "T-1"},
        ])
        assert n == 1


# ── safety_event_log ─────────────────────────────────────────────────


class TestSafetyEventLog:
    @pytest.mark.asyncio
    async def test_idempotent_insert(self, tenant):
        events = [
            {"samsara_event_id": "e1", "vehicle_id": "v1", "event_type": "harsh_brake",
             "occurred_at": _now_iso(), "driver_id": "d1"},
            {"samsara_event_id": "e2", "vehicle_id": "v1", "event_type": "speeding",
             "occurred_at": _now_iso()},
        ]
        n1 = await tenant.insert_safety_events(1, events)
        assert n1 == 2
        # Re-ingest the same window: dedup index drops both.
        n2 = await tenant.insert_safety_events(1, events)
        assert n2 == 0
        # New event gets through.
        n3 = await tenant.insert_safety_events(1, [
            {"samsara_event_id": "e3", "vehicle_id": "v2",
             "event_type": "harsh_turn", "occurred_at": _now_iso()},
        ])
        assert n3 == 1

    @pytest.mark.asyncio
    async def test_window_and_filters(self, tenant):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        recent = _now_iso()
        await tenant.insert_safety_events(1, [
            {"samsara_event_id": "old1", "vehicle_id": "v1", "vehicle_name": "TRK-1", "occurred_at": old,
             "event_type": "harsh_brake"},
            {"samsara_event_id": "new1", "vehicle_id": "v1", "vehicle_name": "TRK-1", "occurred_at": recent,
             "event_type": "harsh_brake", "driver_id": "d1"},
            {"samsara_event_id": "new2", "vehicle_id": "v2", "vehicle_name": "TRK-2", "occurred_at": recent,
             "event_type": "speeding"},
        ])
        rows = await tenant.get_safety_events_warehouse(1, days=7)
        ids = {r["samsara_event_id"] for r in rows}
        assert "old1" not in ids
        assert {"new1", "new2"} <= ids

        only_brake = await tenant.get_safety_events_warehouse(1, days=7, event_type="harsh_brake")
        assert {r["samsara_event_id"] for r in only_brake} == {"new1"}

        only_v2 = await tenant.get_safety_events_warehouse(1, days=7, vehicle_id="v2")
        assert {r["samsara_event_id"] for r in only_v2} == {"new2"}

        only_trk2 = await tenant.get_safety_events_warehouse(1, days=7, vehicle_name="TRK-2")
        assert {r["samsara_event_id"] for r in only_trk2} == {"new2"}

    @pytest.mark.asyncio
    async def test_raw_json_round_trip(self, tenant):
        await tenant.insert_safety_events(1, [
            {"samsara_event_id": "e1", "vehicle_id": "v1",
             "occurred_at": _now_iso(), "raw": {"foo": "bar", "n": 42}},
        ])
        rows = await tenant.get_safety_events_warehouse(1)
        assert rows[0]["raw"] == {"foo": "bar", "n": 42}


# ── driver_efficiency_day ──────────────────────────────────────────


class TestDriverEfficiency:
    @pytest.mark.asyncio
    async def test_upsert_then_aggregate(self, tenant):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        await tenant.upsert_driver_efficiency_day(1, [
            {"driver_id": "d1", "driver_name": "Alice", "day": today,
             "miles": 200, "drive_h": 8, "harsh_brake": 1, "overspeed_min": 5},
            {"driver_id": "d1", "driver_name": "Alice", "day": yesterday,
             "miles": 100, "drive_h": 4, "harsh_brake": 0, "overspeed_min": 0},
            {"driver_id": "d2", "driver_name": "Bob", "day": today,
             "miles": 50, "drive_h": 2},
        ])
        agg = await tenant.get_driver_efficiency_window(1, days=7)
        by_id = {r["driver_id"]: r for r in agg}
        assert by_id["d1"]["miles"] == 300
        assert by_id["d1"]["drive_h"] == 12
        assert by_id["d1"]["harsh_brake"] == 1
        assert by_id["d2"]["miles"] == 50

    @pytest.mark.asyncio
    async def test_upsert_overwrites_same_day(self, tenant):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await tenant.upsert_driver_efficiency_day(1, [
            {"driver_id": "d1", "day": today, "miles": 100, "drive_h": 4},
        ])
        await tenant.upsert_driver_efficiency_day(1, [
            {"driver_id": "d1", "day": today, "miles": 250, "drive_h": 9},
        ])
        agg = await tenant.get_driver_efficiency_window(1, days=1)
        assert len(agg) == 1
        assert agg[0]["miles"] == 250


# ── vehicle_telemetry_hourly ─────────────────────────────────────────


class TestTelemetryHourly:
    @pytest.mark.asyncio
    async def test_upsert_and_read(self, tenant):
        hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        rows = [
            {"vehicle_id": "v1", "hour_utc": hour.strftime("%Y-%m-%dT%H:00:00"),
             "miles": 60, "drive_min": 55, "max_speed_mph": 70,
             "harsh_event_count": 1},
        ]
        n = await tenant.upsert_vehicle_state_hour(1, rows)
        assert n == 1
        out = await tenant.get_vehicle_state_hour(1)
        assert out[0]["miles"] == 60
        assert out[0]["harsh_event_count"] == 1


# ── shape adapters (pure functions, no DB) ───────────────────────────


class TestShapeAdapters:
    def test_vehicle_overview_to_state_row(self):
        v = {
            "id": "v1", "name": "T-1", "_org": "ACME",
            "location": {"latitude": 40.5, "longitude": -74.5,
                         "speedMilesPerHour": 60, "address": "I-95"},
            "fuel": {"value": 75},
            "def_level": {"value": 80},
            "fault_codes": {"j1939": {
                "diagnosticTroubleCodes": [{"code": "P0001"}, {"code": "P0002"}],
                "checkEngineLights": {"red": True},
            }},
        }
        row = sync._vehicle_overview_to_state_row(v)
        assert row["vehicle_id"] == "v1"
        assert row["company_code"] == "ACME"
        assert row["fuel_pct"] == 75
        assert row["def_pct"] == 80
        assert row["fault_count"] == 2
        assert row["dtc_critical_count"] == 1

    def test_safety_event_to_log_row_severity(self):
        hi = sync._safety_event_to_log_row({"event_id": "e1", "g_force": 2.5})
        med = sync._safety_event_to_log_row({"event_id": "e2", "g_force": 1.6})
        low = sync._safety_event_to_log_row({"event_id": "e3", "g_force": 0.5})
        none = sync._safety_event_to_log_row({"event_id": "e4"})
        assert hi["severity"] == "high"
        assert med["severity"] == "medium"
        assert low["severity"] == "low"
        assert none["severity"] == ""


# ── warehouse_reader flag gating ─────────────────────────────────────


class TestWarehouseReader:
    @pytest.mark.asyncio
    async def test_flag_off_uses_fallback(self, monkeypatch):
        from infra import config
        monkeypatch.setattr(config, "WAREHOUSE_READS_ENABLED", False)

        called = {"n": 0}
        async def fallback():
            called["n"] += 1
            return [{"id": "v1", "name": "live"}]

        rows = await warehouse_reader.get_current_vehicles(
            account_id=999, samsara_fallback=fallback,
        )
        assert rows == [{"id": "v1", "name": "live"}]
        assert called["n"] == 1

    @pytest.mark.asyncio
    async def test_flag_on_no_tenant_falls_back(self, monkeypatch):
        from infra import config
        monkeypatch.setattr(config, "WAREHOUSE_READS_ENABLED", True)
        # No tenant configured for this account in the test env \u2014
        # ``get_tenant_db`` returns None, the reader falls through to
        # the live-Samsara callable.
        async def get_tenant_db_stub(_aid):
            return None
        monkeypatch.setattr(warehouse_reader, "get_tenant_db", get_tenant_db_stub)

        async def fallback():
            return [{"id": "live"}]
        rows = await warehouse_reader.get_current_vehicles(
            account_id=999, samsara_fallback=fallback,
        )
        assert rows == [{"id": "live"}]

    @pytest.mark.asyncio
    async def test_flag_on_warehouse_hit(self, tenant, monkeypatch):
        from infra import config
        monkeypatch.setattr(config, "WAREHOUSE_READS_ENABLED", True)

        async def get_tenant_db_stub(_aid):
            return tenant
        monkeypatch.setattr(warehouse_reader, "get_tenant_db", get_tenant_db_stub)

        # A FRESH source_ts is now part of "warehouse hit": rows that
        # are present but stale legitimately fall back since the Phase-4
        # age contract (see test_reader_staleness.py for that case).
        from datetime import datetime, timezone
        await tenant.upsert_vehicle_state(1, [
            {"vehicle_id": "v1", "vehicle_name": "T-1", "company_code": "A",
             "source_ts": datetime.now(timezone.utc).isoformat()},
        ])

        async def fallback():
            raise AssertionError("fallback must not run when warehouse is fresh")

        rows = await warehouse_reader.get_current_vehicles(
            account_id=1, samsara_fallback=fallback,
        )
        assert len(rows) == 1
        # Reader returns the live-overview shape so downstream route
        # helpers (``_simplify`` etc.) work unchanged when the flag flips.
        assert rows[0]["name"] == "T-1"
        assert rows[0]["_org"] == "A"


# ── ingestor with stubbed Samsara client ─────────────────────────────


class _StubClient:
    def __init__(self, fleet=None, events=None, eff=None):
        self._fleet = fleet or []
        self._events = events or []
        self._eff = eff or []

    async def get_vehicles_overview(self):
        return self._fleet

    async def get_events(self, days: int = 7):
        return self._events

    async def get_driver_efficiency(self, days: int = 7):
        return self._eff


class TestIngestor:
    @pytest.mark.asyncio
    async def test_ingest_vehicle_state(self, tenant, monkeypatch):
        client = _StubClient(fleet=[
            {"id": "v1", "name": "T-1",
             "location": {"latitude": 40, "longitude": -74, "speed": 50},
             "fuel": {"value": 60}, "def_level": {}, "fault_codes": {}},
            {"id": "v2", "name": "T-2",
             "location": {}, "fuel": {}, "def_level": {}, "fault_codes": {}},
        ])

        async def get_client_stub(_aid):
            return client
        async def get_tenant_db_stub(_aid):
            return tenant

        monkeypatch.setattr(sync, "get_client", get_client_stub)
        monkeypatch.setattr(sync, "get_tenant_db", get_tenant_db_stub)

        n = await sync.ingest_vehicle_state(1)
        assert n == 2
        rows = await tenant.get_vehicle_state(1)
        assert {r["vehicle_id"] for r in rows} == {"v1", "v2"}

    @pytest.mark.asyncio
    async def test_ingest_safety_events_idempotent(self, tenant, monkeypatch):
        ts = _now_iso()
        client = _StubClient(events=[
            {"event_id": "e1", "vehicle_id": "v1", "event_type": "harsh_brake",
             "time": ts, "g_force": 1.7},
            {"event_id": "e2", "vehicle_id": "v1", "event_type": "speeding",
             "time": ts},
        ])

        async def get_client_stub(_aid):
            return client
        async def get_tenant_db_stub(_aid):
            return tenant
        monkeypatch.setattr(sync, "get_client", get_client_stub)
        monkeypatch.setattr(sync, "get_tenant_db", get_tenant_db_stub)

        n1 = await sync.ingest_safety_events(1)
        assert n1 == 2
        n2 = await sync.ingest_safety_events(1)
        assert n2 == 0  # dedup

    @pytest.mark.asyncio
    async def test_ingest_driver_efficiency(self, tenant, monkeypatch):
        client = _StubClient(eff=[
            {"id": "d1", "name": "Alice", "_miles": 200, "_drive_h": 8,
             "_harsh_brake": 1, "_overspeed_min": 5},
            {"id": "d2", "name": "Bob", "_miles": 50, "_drive_h": 2},
        ])
        async def get_client_stub(_aid):
            return client
        async def get_tenant_db_stub(_aid):
            return tenant
        monkeypatch.setattr(sync, "get_client", get_client_stub)
        monkeypatch.setattr(sync, "get_tenant_db", get_tenant_db_stub)

        n = await sync.ingest_driver_efficiency_day(1)
        assert n == 2
        agg = await tenant.get_driver_efficiency_window(1, days=1)
        by_id = {r["driver_id"]: r for r in agg}
        assert by_id["d1"]["miles"] == 200
        assert by_id["d1"]["harsh_brake"] == 1

    @pytest.mark.asyncio
    async def test_no_samsara_client_returns_zero(self, tenant, monkeypatch):
        async def get_client_raise(_aid):
            raise RuntimeError("no api key")
        async def get_tenant_db_stub(_aid):
            return tenant
        monkeypatch.setattr(sync, "get_client", get_client_raise)
        monkeypatch.setattr(sync, "get_tenant_db", get_tenant_db_stub)

        # Each ingestor must swallow the missing-client case so a single
        # unconfigured tenant can't take the whole scheduler down.
        assert await sync.ingest_vehicle_state(1) == 0
        assert await sync.ingest_safety_events(1) == 0
        assert await sync.ingest_driver_efficiency_day(1) == 0


# Silence unused-import warning for the SimpleNamespace helper that some
# extensions of this module may add later.
_ = SimpleNamespace


# ── Reading as-of-date (back-dated work orders) ───────────────────


@pytest.mark.asyncio
async def test_get_reading_as_of(tenant):
    """Odometer/engine-hours as of a date = the latest snapshot at-or-
    before end of that date, resolved from the unit name."""
    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
    ])
    await tenant.upsert_vehicle_state_minutes(1, [
        {"vehicle_id": "v1", "captured_at": "2026-06-01T12:00:00Z",
         "odometer_mi": 1000, "engine_hours": 100},
        {"vehicle_id": "v1", "captured_at": "2026-06-10T12:00:00Z",
         "odometer_mi": 1500, "engine_hours": 150},
        {"vehicle_id": "v1", "captured_at": "2026-06-17T12:00:00Z",
         "odometer_mi": 2000, "engine_hours": 200},
    ])

    # On the service date itself → that day's reading (not the latest).
    r = await tenant.get_reading_as_of(1, "204", "2026-06-10")
    assert r is not None
    assert round(r["odometer_miles"]) == 1500
    assert round(r["engine_hours"]) == 150

    # Between readings → the most recent at-or-before.
    r2 = await tenant.get_reading_as_of(1, "204", "2026-06-05")
    assert round(r2["odometer_miles"]) == 1000

    # Name match is case-insensitive.
    assert (await tenant.get_reading_as_of(1, "204", "2026-06-17"))["odometer_miles"] == 2000

    # No snapshot before the date → None (caller keeps current).
    assert await tenant.get_reading_as_of(1, "204", "2026-05-01") is None
    # Unknown vehicle → None.
    assert await tenant.get_reading_as_of(1, "ghost", "2026-06-10") is None


@pytest.mark.asyncio
async def test_get_snapshot_coverage(tenant):
    """Coverage window powers the "why is there no reading" note: linked
    vs not, and how far back our odometer history reaches."""
    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
        # Telematics-linked but no odometer snapshots yet.
        {"vehicle_id": "v2", "vehicle_name": "trailer-7", "company_code": "A"},
    ])
    await tenant.upsert_vehicle_state_minutes(1, [
        {"vehicle_id": "v1", "captured_at": "2026-06-01T12:00:00Z",
         "odometer_mi": 1000, "engine_hours": 100},
        {"vehicle_id": "v1", "captured_at": "2026-06-17T12:00:00Z",
         "odometer_mi": 2000, "engine_hours": 200},
    ])

    cov = await tenant.get_snapshot_coverage(1, "204")
    assert cov["telematics_linked"] is True
    assert cov["coverage_start"][:10] == "2026-06-01"
    assert cov["coverage_end"][:10] == "2026-06-17"

    # Linked, but no odometer snapshots → linked True, window empty.
    cov2 = await tenant.get_snapshot_coverage(1, "trailer-7")
    assert cov2["telematics_linked"] is True
    assert cov2["coverage_start"] is None

    # Not in vehicle_state_live at all → not linked.
    cov3 = await tenant.get_snapshot_coverage(1, "ghost")
    assert cov3["telematics_linked"] is False
    assert cov3["coverage_start"] is None


@pytest.mark.asyncio
async def test_get_reading_as_of_daily_eod_fallback(tenant):
    """Tier-2: a date older than the 7-day snapshot window resolves from
    the long-retention daily EOD reading; recent dates still prefer the
    precise snapshot."""
    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
    ])
    # A precise snapshot only for a recent day.
    await tenant.upsert_vehicle_state_minutes(1, [
        {"vehicle_id": "v1", "captured_at": "2026-06-16T12:00:00Z",
         "odometer_mi": 5000, "engine_hours": 500},
    ])
    # Daily EOD rows reaching months back (snapshots long pruned).
    await tenant.upsert_vehicle_state_day(1, [
        {"vehicle_id": "v1", "day_utc": "2026-03-10",
         "odometer_eod": 1200, "engine_hours_eod": 120},
        {"vehicle_id": "v1", "day_utc": "2026-04-10",
         "odometer_eod": 1800, "engine_hours_eod": 180},
    ])

    # Old date, no snapshot → daily EOD (latest at-or-before).
    r = await tenant.get_reading_as_of(1, "204", "2026-04-15")
    assert round(r["odometer_miles"]) == 1800
    assert round(r["engine_hours"]) == 180
    assert r["as_of"] == "2026-04-10"

    # Even older → the earlier daily row.
    r2 = await tenant.get_reading_as_of(1, "204", "2026-03-20")
    assert round(r2["odometer_miles"]) == 1200

    # Recent date with a snapshot → precise snapshot wins (tier 1).
    r3 = await tenant.get_reading_as_of(1, "204", "2026-06-16")
    assert round(r3["odometer_miles"]) == 5000
    assert r3["as_of"] == "2026-06-16T12:00:00Z"

    # Before any history at all → None (caller keeps current).
    assert await tenant.get_reading_as_of(1, "204", "2026-01-01") is None


@pytest.mark.asyncio
async def test_get_snapshot_coverage_spans_daily(tenant):
    """Coverage reach is the UNION of snapshot + daily EOD tiers, so the
    "Only tracked since…" note reflects the real ~2-year window."""
    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
    ])
    await tenant.upsert_vehicle_state_minutes(1, [
        {"vehicle_id": "v1", "captured_at": "2026-06-16T12:00:00Z",
         "odometer_mi": 5000, "engine_hours": 500},
    ])
    await tenant.upsert_vehicle_state_day(1, [
        {"vehicle_id": "v1", "day_utc": "2025-09-01",
         "odometer_eod": 100, "engine_hours_eod": 10},
    ])
    cov = await tenant.get_snapshot_coverage(1, "204")
    # Start reaches back to the daily tier; end is the fresh snapshot.
    assert cov["coverage_start"][:10] == "2025-09-01"
    assert cov["coverage_end"][:10] == "2026-06-16"


@pytest.mark.asyncio
async def test_aggregate_day_window_captures_eod(tenant):
    """The daily aggregator banks the end-of-day odometer/engine-hours
    (MAX over the day, since both are monotonic) into the long-retention
    daily table — the heart of forward-accruing odometer history."""
    from datetime import datetime as _dt, timezone as _tz
    await tenant.upsert_vehicle_state(1, [
        {"vehicle_id": "v1", "vehicle_name": "204", "company_code": "A"},
    ])
    # Three rising readings within one hour of one day.
    await tenant.upsert_vehicle_state_minutes(1, [
        {"vehicle_id": "v1", "captured_at": "2026-06-15T12:00:00",
         "odometer_mi": 1000, "engine_hours": 100, "engine_state": "moving",
         "speed_mph": 50},
        {"vehicle_id": "v1", "captured_at": "2026-06-15T12:30:00",
         "odometer_mi": 1500, "engine_hours": 150, "engine_state": "moving",
         "speed_mph": 55},
        {"vehicle_id": "v1", "captured_at": "2026-06-15T12:55:00",
         "odometer_mi": 2000, "engine_hours": 200, "engine_state": "idle",
         "speed_mph": 0},
    ])
    day = _dt(2026, 6, 15, 0, 0, tzinfo=_tz.utc)
    # Hourly rollup first (daily rows derive from the hourly table)…
    await aggregator._aggregate_hour_window(
        tenant, 1, _dt(2026, 6, 15, 12, 0, tzinfo=_tz.utc),
    )
    # …then the daily rollup, which also reads snapshots for EOD.
    await aggregator._aggregate_day_window(tenant, 1, day)

    cur = await tenant._db.execute(
        "SELECT odometer_eod, engine_hours_eod FROM vehicle_state_day "
        "WHERE account_id = ? AND vehicle_id = ? "
        "AND bucket_start = ?",
        (1, "v1", "2026-06-15"),
    )
    row = dict(await cur.fetchone())
    assert round(row["odometer_eod"]) == 2000
    assert round(row["engine_hours_eod"]) == 200

    # As-of for that date now resolves the banked EOD reading.
    r = await tenant.get_reading_as_of(1, "204", "2026-06-15")
    assert round(r["odometer_miles"]) == 2000
