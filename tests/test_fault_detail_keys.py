"""The DTC lifecycle store must accept the payload Samsara actually sends.

``vehicle_fault_detail`` sat at zero rows for its whole life — platform
wide — because the extraction read ``id`` / ``spn`` / ``fmi`` while the
live payload spells them ``spnId`` / ``fmiId`` and carries no ``id`` at
all (verified against production: 66 DTCs across 32 faulted vehicles,
keys ``spnId, fmiId, spnDescription, fmiDescription, milStatus, txId,
occurrenceCount, sourceAddressName``).  Every DTC therefore derived an
empty dtc_id and was skipped.  Third instance of the same provider
naming drift, after fuelPercent(s) and engineState(s).
"""

from __future__ import annotations

import pytest

from adapters.storage.warehouse import _dtc_id

# A DTC exactly as the live faults payload delivers it.
_LIVE_DTC = {
    "fmiDescription": "Low—moderate severity",
    "fmiId": 18,
    "occurrenceCount": 96,
    "spnId": 2659,
    "spnDescription": "Engine Exhaust Gas Recirculation 1 Mass Flow Rate",
    "milStatus": 1,
    "txId": 0,
    "sourceAddressName": "Engine #1",
}


def test_live_payload_derives_a_stable_dtc_id():
    assert _dtc_id(_LIVE_DTC) == "spn:2659-fmi:18"


def test_bare_spellings_still_work():
    # Anything already stored (raw_json replays, older fixtures) keeps
    # resolving to the SAME id, so lifecycle state carries across.
    assert _dtc_id({"spn": 2659, "fmi": 18}) == "spn:2659-fmi:18"


def test_provider_id_wins_when_present():
    assert _dtc_id({"id": "abc123", "spnId": 1, "fmiId": 2}) == "abc123"


def test_zero_is_a_valid_code_not_a_missing_one():
    # txId 0 taught us providers use 0 meaningfully; spn/fmi 0 must not
    # be treated as absent.
    assert _dtc_id({"spnId": 0, "fmiId": 0}) == "spn:0-fmi:0"


def test_truly_keyless_dtc_yields_nothing():
    assert _dtc_id({"fmiDescription": "??"}) == ""


@pytest.mark.asyncio
async def test_live_shaped_dtcs_actually_land_in_the_table(pg_db):
    """End to end: the exact production payload shape produces rows."""
    acct = 42
    new_obs, _cleared = await pg_db.upsert_vehicle_fault_details(
        acct, {"veh-1": [_LIVE_DTC]},
    )
    assert new_obs == 1
    cur = await pg_db._db.execute(
        "SELECT dtc_id, spn, fmi, description FROM vehicle_fault_detail "
        "WHERE account_id = ? AND vehicle_id = ?",
        (acct, "veh-1"),
    )
    rows = [dict(zip(("dtc_id", "spn", "fmi", "description"), r))
            for r in await cur.fetchall()]
    assert len(rows) == 1
    assert rows[0]["dtc_id"] == "spn:2659-fmi:18"
    assert rows[0]["spn"] == 2659
    assert rows[0]["fmi"] == 18
    # The SPN text names the fault; the FMI text merely grades it.
    assert "Exhaust Gas Recirculation" in rows[0]["description"]


@pytest.mark.asyncio
async def test_registry_id_rides_state_snapshot_and_orphans(pg_db):
    """Contract 3 end to end at the resolution point.

    A vehicle the registry knows gets stamped with OUR id; one it does
    not know keeps NULL and lands in quarantine — never a guess.
    """
    acct = 42
    await pg_db.add_vehicle(
        acct, unit_number="301", company_code="PTG",
    )
    # Link it the way the integration does.
    await pg_db.upsert_from_integration(
        acct,
        [{"company_code": "PTG", "unit_number": "301",
          "telematics_ref": "sam_301"}],
        source="samsara",
    )
    ref_map = await pg_db.registry_ids_by_telematics_ref(acct)
    assert "sam_301" in ref_map

    await pg_db.upsert_vehicle_state(acct, [
        {"vehicle_id": "sam_301", "vehicle_name": "301",
         "company_code": "PTG", "registry_id": ref_map["sam_301"]},
        {"vehicle_id": "sam_999", "vehicle_name": "999",
         "company_code": "PTG"},   # unknown to the registry
    ])
    rows = {r["vehicle_id"]: r for r in await pg_db.get_vehicle_state(acct)}
    assert rows["sam_301"]["registry_id"] == ref_map["sam_301"]
    assert rows["sam_999"]["registry_id"] is None

    # A later tick where resolution hiccups must NOT unlink history.
    await pg_db.upsert_vehicle_state(acct, [
        {"vehicle_id": "sam_301", "vehicle_name": "301",
         "company_code": "PTG"},   # no registry_id this time
    ])
    rows = {r["vehicle_id"]: r for r in await pg_db.get_vehicle_state(acct)}
    assert rows["sam_301"]["registry_id"] == ref_map["sam_301"]

    n = await pg_db.record_ingest_orphans(acct, "vehicles.state", [
        {"external_id": "sam_999", "name": "999", "company_code": "PTG"},
    ])
    assert n == 1
    # Re-sighting bumps the count instead of duplicating.
    await pg_db.record_ingest_orphans(acct, "vehicles.state", [
        {"external_id": "sam_999", "name": "999", "company_code": "PTG"},
    ])
    cur = await pg_db._db.execute(
        "SELECT count FROM ingest_orphans "
        "WHERE account_id = ? AND external_id = ?", (acct, "sam_999"))
    row = await cur.fetchone()
    assert row is not None and int(row[0]) == 2
