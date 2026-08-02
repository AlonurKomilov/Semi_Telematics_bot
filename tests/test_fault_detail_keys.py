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
