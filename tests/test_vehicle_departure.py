"""The departure sweep — retiring silent badges without eating the fleet.

The live-state table served every badge a truck ever wore as a current
vehicle: unit 6729 existed three times, two of them silent since May.
The sweep retires stale badges — registry entry inactive, live row
gone, history untouched — and refuses to run when the WHOLE account is
stale, because fleet-wide silence is our outage, not their departure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


async def _seed(db, acct, vid, name, company, days_ago):
    await db.upsert_vehicle_state(acct, [{
        "vehicle_id": vid, "vehicle_name": name, "company_code": company,
        "captured_at": _iso(days_ago),
    }])


@pytest.mark.asyncio
async def test_sweep_retires_stale_badges_and_keeps_live_ones(pg_db):
    acct = 42
    # The 6729 shape: one live badge, one dead badge, same physical truck.
    await pg_db.add_vehicle(acct, unit_number="6729", company_code="PTG")
    await pg_db.upsert_from_integration(
        acct,
        [{"company_code": "PTG", "unit_number": "6729",
          "telematics_ref": "badge_live"}],
        source="samsara",
    )
    await pg_db.add_vehicle(acct, unit_number="6729", company_code="OSY",
                            status="yard")
    await pg_db.upsert_from_integration(
        acct,
        [{"company_code": "OSY", "unit_number": "6729",
          "telematics_ref": "badge_dead"}],
        source="samsara",
    )
    await _seed(pg_db, acct, "badge_live", "6729", "PTG", 0.1)
    await _seed(pg_db, acct, "badge_dead", "6729", "OSY", 80)
    await _seed(pg_db, acct, "other", "230", "G1", 2)

    result = await pg_db.sweep_departed_vehicles(acct)

    assert [d[0] for d in result["departed"]] == ["badge_dead"]
    assert result["registry_deactivated"] == 1
    assert not result["skipped_all_stale"]

    remaining = {r["vehicle_id"] for r in await pg_db.get_vehicle_state(acct)}
    assert remaining == {"badge_live", "other"}

    # Registry: the dead badge's entry is inactive; its OPERATOR status
    # survives untouched — that column belongs to people.
    all_rows = {(v.company_code, v.unit_number): v
                for v in await pg_db.list_vehicles(acct, include_inactive=True)}
    dead = all_rows[("OSY", "6729")]
    assert dead.is_active in (0, False)
    assert dead.status == "yard"
    live = all_rows[("PTG", "6729")]
    assert live.is_active in (1, True)


@pytest.mark.asyncio
async def test_whole_fleet_stale_is_an_outage_not_a_departure(pg_db):
    acct = 43
    await _seed(pg_db, acct, "v1", "101", "G1", 45)
    await _seed(pg_db, acct, "v2", "102", "G1", 60)

    result = await pg_db.sweep_departed_vehicles(acct)

    assert result["skipped_all_stale"]
    assert result["departed"] == []
    assert len(await pg_db.get_vehicle_state(acct)) == 2


@pytest.mark.asyncio
async def test_blank_captured_at_is_new_not_departed(pg_db):
    # A freshly added badge that has not transmitted yet has no provider
    # timestamp.  It has not "departed" — it has not arrived.
    acct = 44
    await _seed(pg_db, acct, "fresh", "555", "PTG", 0.1)
    await pg_db.upsert_vehicle_state(acct, [{
        "vehicle_id": "newborn", "vehicle_name": "556",
        "company_code": "PTG", "captured_at": "",
    }])

    result = await pg_db.sweep_departed_vehicles(acct)

    assert result["departed"] == []
    remaining = {r["vehicle_id"] for r in await pg_db.get_vehicle_state(acct)}
    assert "newborn" in remaining


@pytest.mark.asyncio
async def test_history_survives_the_retirement(pg_db):
    acct = 45
    await _seed(pg_db, acct, "gone", "777", "RMR", 90)
    await _seed(pg_db, acct, "here", "778", "RMR", 1)
    # History under the departing badge.
    await pg_db.upsert_vehicle_telemetry_hourly(acct, [{
        "vehicle_id": "gone", "hour_utc": "2026-05-01T10:00:00",
        "miles": 42.0,
    }])

    await pg_db.sweep_departed_vehicles(acct)

    cur = await pg_db._db.execute(
        "SELECT COUNT(*) FROM vehicle_telemetry "
        "WHERE account_id = ? AND vehicle_id = ?", (acct, "gone"))
    assert (await cur.fetchone())[0] == 1
