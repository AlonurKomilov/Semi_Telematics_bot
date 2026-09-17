"""The vehicle-state tick — one write path, whoever reports.

GOLDEN.  With Samsara alone — every account today — the neutral tick
must write exactly what the Samsara-only ingest wrote before it
existed.  ``GOLDEN`` is that output, frozen from the old code on
2026-09-17 against this fixture fleet, so the refactor is measured
against the past rather than against itself.  The one new column is
asserted separately.

TWO PROVIDERS.  Where two rows meet on one registry identity, each
reading group moves whole to the winner ``pick_readings`` names, the
row keeps the KEY the registry links to, and ``field_provenance`` says
who supplied what.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from capabilities import source as reconciliation
from capabilities.data_lifecycle.staleness import freshest
from capabilities.integrations.samsara import sync
from capabilities.integrations.shared import vehicle_state as vs
from capabilities.source.readings import NEWEST
from features.vehicles.warehouse import aggregator


@pytest_asyncio.fixture
async def tenant(pg_db):
    yield pg_db


@pytest_asyncio.fixture
async def aid(tenant) -> int:
    """A real account row: the precedence store keys on it (FK), and a
    golden that ran under a phantom id would not be the production
    path."""
    return (await tenant.create_account("State Co")).id


def _iso(now: datetime, minutes_ago: float) -> str:
    return (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


# ── the frozen fixture fleet and what the old ingest wrote for it ─────

FLEET = [
    {"id": "v1", "name": "T-1", "_org": "PTG", "vin": "1XKAD49X1KJ000001",
     "make": "Kenworth", "model": "T680", "year": 2019, "license_plate": "ABC123",
     "gateway_serial": "GW1",
     "location": {"latitude": 40.1, "longitude": -74.2, "speed": 51.5, "heading": 90,
                  "address": "I-95 N", "time": "2026-09-17T09:58:00+00:00"},
     "fuel": {"value": 60, "time": "2026-09-17T09:30:00+00:00"},
     "def_level": {"value": 12, "time": "2026-09-17T07:00:00+00:00"},
     "fault_codes": {"j1939": {"diagnosticTroubleCodes": [{"spn": 1}],
                               "checkEngineLights": {"red": True}}},
     "engine_state_raw": "On"},
    {"id": "v2", "name": "T-2", "_org": "PTG",
     "location": {}, "fuel": {}, "def_level": {}, "fault_codes": {}},
    {"id": "v3", "name": "T-3", "_org": "ABC",
     "location": {"latitude": 0, "longitude": 0, "speed": 0,
                  "time": "2026-09-17T09:00:00+00:00"},
     "fuel": {}, "def_level": {}, "fault_codes": {}, "engine_state_raw": "Off"},
]
ODOMETERS = [
    {"id": "v1", "odometer_miles": 120000.5, "time": "2026-09-17T09:59:00+00:00"},
    {"id": "v3", "odometer_miles": 500.0, "time": "2026-09-17T09:00:00+00:00"},
]
ENGINE_HOURS = [
    {"id": "v1", "engine_hours": 4321.5, "time": "2026-09-17T09:55:00+00:00"},
]

GOLDEN = [
    {"address": "I-95 N", "captured_at": "2026-09-17T09:58:00+00:00",
     "company_code": "PTG", "def_pct": 12.0, "def_time": "2026-09-17T07:00:00+00:00",
     "dtc_critical_count": 1, "engine_hours": 4321.5,
     "engine_hours_time": "2026-09-17T09:55:00+00:00", "engine_state": "moving",
     "fault_count": 1, "fuel_pct": 60.0, "fuel_time": "2026-09-17T09:30:00+00:00",
     "heading": 90.0, "last_driver_id": "", "last_driver_name": "",
     # REAL columns: float32 is what the store gives back.
     "lat": 40.099998474121094, "lon": -74.19999694824219,
     "odometer_mi": 120000.5, "odometer_time": "2026-09-17T09:59:00+00:00",
     "registry_id": None, "source_ts": "2026-09-17T09:59:00+00:00",
     "speed_mph": 51.5, "vehicle_id": "v1", "vehicle_name": "T-1"},
    {"address": "", "captured_at": "", "company_code": "PTG", "def_pct": None,
     "def_time": None, "dtc_critical_count": 0, "engine_hours": None,
     "engine_hours_time": None, "engine_state": "", "fault_count": 0,
     "fuel_pct": None, "fuel_time": None, "heading": None, "last_driver_id": "",
     "last_driver_name": "", "lat": None, "lon": None, "odometer_mi": None,
     "odometer_time": None, "registry_id": None, "source_ts": None,
     "speed_mph": None, "vehicle_id": "v2", "vehicle_name": "T-2"},
    {"address": "", "captured_at": "2026-09-17T09:00:00+00:00", "company_code": "ABC",
     "def_pct": None, "def_time": None, "dtc_critical_count": 0, "engine_hours": None,
     "engine_hours_time": None, "engine_state": "off", "fault_count": 0,
     "fuel_pct": None, "fuel_time": None, "heading": None, "last_driver_id": "",
     "last_driver_name": "", "lat": 0.0, "lon": 0.0, "odometer_mi": 500.0,
     "odometer_time": "2026-09-17T09:00:00+00:00", "registry_id": None,
     "source_ts": "2026-09-17T09:00:00+00:00", "speed_mph": 0.0,
     "vehicle_id": "v3", "vehicle_name": "T-3"},
]


class _Samsara:
    def __init__(self, fleet=FLEET, odometers=ODOMETERS, hours=ENGINE_HOURS):
        self._fleet, self._odo, self._hours = fleet, odometers, hours

    async def get_vehicles_overview(self):
        return self._fleet

    async def get_current_odometer_readings(self):
        return self._odo

    async def get_current_engine_hours_readings(self):
        return self._hours


def _wire(monkeypatch, tenant, client, providers):
    async def get_client(_aid):
        return client

    async def get_tenant_db(_aid):
        return tenant

    async def resolve(_aid, _cap):
        return list(providers)

    monkeypatch.setattr(sync, "get_client", get_client)
    monkeypatch.setattr(vs, "get_tenant_db", get_tenant_db)
    monkeypatch.setattr(vs, "resolve_all_providers_for", resolve)


async def _rows(tenant, aid):
    rows = await tenant.get_vehicle_state(aid)
    for r in rows:
        r.pop("updated_at", None)
    return rows


@pytest.mark.asyncio
async def test_golden_samsara_alone_writes_what_it_always_wrote(tenant, aid, monkeypatch):
    _wire(monkeypatch, tenant, _Samsara(), ["samsara"])
    assert await vs.ingest_vehicle_state(aid) == 3
    rows = await _rows(tenant, aid)
    prov = {r["vehicle_id"]: r.pop("field_provenance") for r in rows}
    assert rows == GOLDEN
    # The new column: every present group is Samsara's; a truck with no
    # fix and no odometer has nothing to attribute; a (0, 0) fix at 0 mph
    # is still a location reading (the speed is the value).
    assert prov == {
        "v1": {g: "samsara" for g in vs.READING_GROUPS},
        "v2": {},
        "v3": {"location": "samsara", "odometer": "samsara"},
    }


@pytest.mark.asyncio
async def test_no_connected_provider_writes_nothing(tenant, aid, monkeypatch):
    _wire(monkeypatch, tenant, _Samsara(), [])
    assert await vs.ingest_vehicle_state(aid) == 0
    assert await tenant.get_vehicle_state(aid) == []


@pytest.mark.asyncio
async def test_a_provider_that_cannot_be_asked_leaves_the_row_alone(tenant, aid, monkeypatch):
    _wire(monkeypatch, tenant, _Samsara(), ["samsara"])
    await vs.ingest_vehicle_state(aid)

    class _Down:
        async def get_vehicles_overview(self):
            raise RuntimeError("503")

    _wire(monkeypatch, tenant, _Down(), ["samsara"])
    assert await vs.ingest_vehicle_state(aid) == 0
    assert len(await tenant.get_vehicle_state(aid)) == 3


@pytest.mark.asyncio
async def test_a_provider_offering_the_capability_without_a_collector_is_skipped(tenant, aid, monkeypatch):
    _wire(monkeypatch, tenant, _Samsara(), ["samsara", "motive"])
    assert await vs.ingest_vehicle_state(aid) == 3


@pytest.mark.asyncio
async def test_provenance_reaches_the_minute_grain(tenant, aid, monkeypatch):
    _wire(monkeypatch, tenant, _Samsara(), ["samsara"])
    await vs.ingest_vehicle_state(aid)

    async def get_tenant_db(_aid):
        return tenant
    monkeypatch.setattr(aggregator, "get_tenant_db", get_tenant_db)
    assert await aggregator.snapshot_vehicle_state(aid) == 3
    cur = await tenant._db.execute(
        "SELECT vehicle_id, field_provenance FROM vehicle_state_minute "
        "WHERE account_id = ? ORDER BY vehicle_id", (aid,))
    got = {vid: (json.loads(p) if p else None) for vid, p in await cur.fetchall()}
    assert got["v1"] == {g: "samsara" for g in vs.READING_GROUPS}
    assert got["v2"] is None


# ── two providers, one truck ─────────────────────────────────────────

def _live_samsara(now: datetime) -> _Samsara:
    """v1 with a fresh fix, a STALE odometer, fresh engine hours and
    fuel, and no DEF reading at all."""
    fleet = [
        {"id": "v1", "name": "T-1", "_org": "PTG",
         "location": {"latitude": 40.1, "longitude": -74.2, "speed": 51.5,
                      "heading": 90, "address": "I-95 N", "time": _iso(now, 2)},
         "fuel": {"value": 60, "time": _iso(now, 5)}, "def_level": {},
         "fault_codes": {}, "engine_state_raw": "On"},
    ]
    return _Samsara(
        fleet,
        [{"id": "v1", "odometer_miles": 120000.5, "time": _iso(now, 40)}],
        [{"id": "v1", "engine_hours": 4321.5, "time": _iso(now, 5)}],
    )


def _other_collector(rid: int, now: datetime):
    """A second provider that resolved the truck by unit number itself
    (``registry_id`` set), with a newer fix, a FRESH odometer and the
    only DEF reading."""
    async def collect(_account_id, _tenant):
        return vs.StateBatch(provider_id="other", rows=[{
            "vehicle_id": "o-1", "vehicle_name": "T-1", "company_code": "PTG",
            "registry_id": rid,
            "lat": 41.0, "lon": -75.0, "speed_mph": 0.0, "heading": 10.0,
            "address": "Yard", "engine_state": "off", "captured_at": _iso(now, 1),
            "odometer_mi": 120100.0, "odometer_time": _iso(now, 3),
            "engine_hours": None, "engine_hours_time": None,
            "fuel_pct": None, "fuel_time": None,
            "def_pct": 40.0, "def_time": _iso(now, 4),
            "fault_count": 0, "dtc_critical_count": 0,
            "last_driver_id": "", "last_driver_name": "",
        }])
    return collect


async def _register_t1(tenant, aid) -> int:
    await tenant.upsert_from_integration(aid, [{
        "company_code": "PTG", "unit_number": "T-1", "telematics_ref": "v1",
        "vin": "", "make": "", "model": "", "year": None, "plate_number": "",
        "gateway_serial": "",
    }], source="samsara")
    return (await tenant.registry_ids_by_telematics_ref(aid))["v1"]


@pytest.mark.asyncio
async def test_two_providers_each_reading_moves_whole_and_the_key_holds(tenant, aid, monkeypatch):
    now = datetime.now(timezone.utc)
    rid = await _register_t1(tenant, aid)
    monkeypatch.setitem(vs._COLLECTORS, "other", _other_collector(rid, now))
    _wire(monkeypatch, tenant, _live_samsara(now), ["samsara", "other"])

    assert await vs.ingest_vehicle_state(aid) == 1
    rows = await _rows(tenant, aid)
    assert [r["vehicle_id"] for r in rows] == ["v1"], "one truck, one row, the registry's key"
    r = rows[0]
    assert r["registry_id"] == rid
    # Location: both fresh, Samsara preferred — the newer fix loses.
    assert (r["lat"], r["speed_mph"], r["captured_at"]) == (
        pytest.approx(40.1), 51.5, _iso(now, 2))
    # Odometer: Samsara's is 40 minutes old, the other's 3 — the whole
    # reading moves, value AND clock.
    assert (r["odometer_mi"], r["odometer_time"]) == (120100.0, _iso(now, 3))
    # Groups only one device reports come from that device.
    assert (r["fuel_pct"], r["engine_hours"], r["def_pct"]) == (60.0, 4321.5, 40.0)
    assert r["field_provenance"] == {
        "location": "samsara", "odometer": "other", "engine_hours": "samsara",
        "fuel": "samsara", "def": "other",
    }
    assert r["source_ts"] == freshest(
        r["captured_at"], r["odometer_time"], r["engine_hours_time"],
        r["fuel_time"], r["def_time"])


@pytest.mark.asyncio
async def test_choosing_newest_for_the_fix_moves_the_reading_not_the_row(tenant, aid, monkeypatch):
    """The owner's choice changes whose position shows.  It must not
    change the row's KEY: a second live row under the other provider's
    id, beside a stale first, is what an identity-anchored base
    prevents."""
    now = datetime.now(timezone.utc)
    rid = await _register_t1(tenant, aid)
    monkeypatch.setitem(vs._COLLECTORS, "other", _other_collector(rid, now))
    _wire(monkeypatch, tenant, _live_samsara(now), ["samsara", "other"])
    await reconciliation.set_precedence(tenant, aid, vs.ENTITY, {"location": NEWEST})

    assert await vs.ingest_vehicle_state(aid) == 1
    rows = await _rows(tenant, aid)
    assert [r["vehicle_id"] for r in rows] == ["v1"]
    r = rows[0]
    assert (r["lat"], r["speed_mph"], r["engine_state"], r["captured_at"]) == (
        41.0, 0.0, "off", _iso(now, 1))
    assert r["field_provenance"]["location"] == "other"
    assert r["field_provenance"]["fuel"] == "samsara"
