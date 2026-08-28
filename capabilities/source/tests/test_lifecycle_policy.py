"""Auto-pilot — who may change the roster on their own, and what "off"
must never mean.

Three write paths act on the vehicle list unconditionally today: the
Samsara ingest creates rows, the Datatruck projection creates rows, and
the departure sweep retires silent badges.  The policy makes each an
owner's choice — with two hard edges:

  DEFAULTS ARE TODAY'S BEHAVIOUR.  A default that blocked Datatruck
  adds would silently strand trailers on every existing account,
  because those exist precisely where Samsara carries nothing.

  OFF GOVERNS CREATION/RETIREMENT ONLY.  A source denied `add` still
  matches, enriches and revives rows the account already owns —
  "stop auto-adding vehicles" never means "freeze my fleet's VINs".
"""
from __future__ import annotations

import pytest

from capabilities.source import (
    LIFECYCLE_VERBS,
    get_lifecycle_policy,
    may_add,
    may_auto_inactivate,
    set_lifecycle_policy,
)


@pytest.mark.asyncio
async def test_defaults_equal_todays_unconditional_behaviour(pg_db):
    acct = (await pg_db.create_account("Lifecycle Default Co")).id
    policy = await get_lifecycle_policy(pg_db, acct, "vehicle")
    for src, verbs in LIFECYCLE_VERBS["vehicle"].items():
        for v in verbs:
            assert policy[src][v] is True, (
                f"{src}.{v} defaulted OFF — existing accounts would "
                "silently lose a mechanism nobody disabled"
            )


@pytest.mark.asyncio
async def test_a_verb_without_a_mechanism_cannot_be_stored(pg_db):
    """datatruck has no inactivate path; a stored flag for it would be
    a lie that renders as a working switch."""
    acct = (await pg_db.create_account("Lifecycle Clamp Co")).id
    stored = await set_lifecycle_policy(pg_db, acct, "vehicle", {
        "datatruck": {"add": False, "inactivate": False},
        "nonsense": {"add": False},
    })
    assert "inactivate" not in stored["datatruck"]
    assert "nonsense" not in stored
    assert stored["datatruck"]["add"] is False


@pytest.mark.asyncio
async def test_add_off_blocks_creation_but_never_enrichment(pg_db):
    acct = (await pg_db.create_account("Lifecycle Add Co")).id
    # A truck the account already owns, created while adds were on.
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "E1",
         "telematics_ref": "ref-e1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)

    await set_lifecycle_policy(pg_db, acct, "vehicle",
                               {"samsara": {"add": False}})

    # Next tick: an enrichment for the existing truck plus a brand-new
    # one.  The new truck must not appear; the old one must still learn
    # its VIN.
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "E1",
         "telematics_ref": "ref-e1", "vin": "VIN0000000000E1"},
        {"company_code": "PTG", "unit_number": "NEW-2",
         "telematics_ref": "ref-new2"},
    ], source="samsara")

    rows = {r.unit_number: r for r in await pg_db.list_vehicles(acct)}
    assert "NEW-2" not in rows, "auto-add OFF still created a truck"
    assert rows["E1"].vin == "VIN0000000000E1", (
        "add OFF froze enrichment — 'stop adding' became 'stop syncing'"
    )


@pytest.mark.asyncio
async def test_add_off_never_blocks_revival_of_a_swept_truck(pg_db):
    """Revival is the sweep's documented contract and rides the MATCH
    branch, not the insert branch — the gate must not reach it."""
    acct = (await pg_db.create_account("Lifecycle Revive Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "R1",
         "telematics_ref": "ref-r1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    # Swept for silence (flag only, status untouched).
    await pg_db._db.execute(
        "UPDATE vehicles SET is_active = 0 WHERE id = ?", (v.id,))
    await pg_db._db.commit()
    await set_lifecycle_policy(pg_db, acct, "vehicle",
                               {"samsara": {"add": False}})

    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "R1",
         "telematics_ref": "ref-r1"},
    ], source="samsara")
    (back,) = await pg_db.list_vehicles(acct)
    assert back.id == v.id and back.is_active


@pytest.mark.asyncio
async def test_inactivate_off_stops_the_sweep(pg_db, monkeypatch):
    acct = (await pg_db.create_account("Lifecycle Sweep Co")).id
    await set_lifecycle_policy(pg_db, acct, "vehicle",
                               {"samsara": {"inactivate": False}})
    result = await pg_db.sweep_departed_vehicles(acct)
    assert result.get("skipped_policy") is True
    assert result["departed"] == []
    assert result["registry_deactivated"] == 0


@pytest.mark.asyncio
async def test_datatruck_add_off_stops_the_projection_creating(pg_db):
    acct = (await pg_db.create_account("Lifecycle DT Co")).id
    await set_lifecycle_policy(pg_db, acct, "vehicle",
                               {"datatruck": {"add": False}})
    n = await pg_db.project_external_vehicles(acct, [
        {"unit_number": "TRL-1", "vin": "TRAILER0000000101"},
    ], vehicle_type="trailer", source="datatruck")
    assert n == 0
    assert await pg_db.list_vehicles(acct) == []
