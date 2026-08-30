"""``source`` is who created the row, and it stops lying about it.

The column's own contract ("records who created the row") was
contradicted by one line: the Samsara upsert refreshed it to "the
latest integration to touch the row", every 60 seconds.  So unit 144
— enriched by Datatruck on four of six fields — claimed samsara, and a
Datatruck-created trailer reported samsara within a minute of Samsara
merely touching it.

The honest answer is plural and DERIVED: creator ∪ provenance owners.
Both facts already live on the row; a stored list would be a second
thing to keep in sync forever, to work around a one-line bug.
"""
from __future__ import annotations

import pytest

from adapters.storage.vehicles_registry import Vehicle


def _v(**kw) -> Vehicle:
    base = dict(
        id=1, account_id=1, company_code="PTG", unit_number="144",
        vehicle_type="truck", vin="", plate_number="", make="", model="",
        year=None, status="active", source="samsara", telematics_ref="r",
        notes="", is_active=True, created_at="", updated_at="",
    )
    base.update(kw)
    return Vehicle(**base)


def test_sources_is_creator_first_then_enrichers():
    """The order is a fact: creator leads, enrichers follow sorted.
    NOT a fill-priority — that is per-field and configurable, so no
    single row ordering could state it truthfully."""
    v = _v(source="samsara", field_provenance={
        "vin": "datatruck", "plate_number": "datatruck",
        "make": "samsara", "model": "datatruck",
    })
    assert v.sources == ("samsara", "datatruck")


def test_a_hand_edit_shows_up_as_a_source():
    v = _v(source="datatruck", field_provenance={"vin": "manual"})
    assert v.sources == ("datatruck", "manual")
    # And a hand-CREATED truck leads with Local, whoever enriched it.
    v2 = _v(source="manual", field_provenance={"vin": "samsara"})
    assert v2.sources == ("manual", "samsara")


def test_a_provider_that_owns_nothing_is_not_a_source():
    """Decided, not defaulted: syncing a row without creating it or
    winning a field contributes nothing a reader can point at."""
    v = _v(source="datatruck", field_provenance={})
    assert v.sources == ("datatruck",)


@pytest.mark.asyncio
async def test_a_sync_touching_a_row_never_changes_its_creator(pg_db):
    """The one-line stomp, proven gone.

    Datatruck creates a trailer; Samsara later matches and enriches the
    same row.  Before this fix, `source` read samsara within one tick
    of that — the creator overwritten by the last toucher.
    """
    acct = (await pg_db.create_account("Source Honesty Co")).id
    await pg_db.project_external_vehicles(acct, [
        {"unit_number": "TR-9", "vin": "TRAILER0000000009"},
    ], vehicle_type="trailer", source="datatruck")
    (v,) = await pg_db.list_vehicles(acct)
    assert v.source == "datatruck"

    # Samsara touches the same row (VIN match) with enrichment.
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "TR-9",
         "vin": "TRAILER0000000009", "make": "GREAT DANE",
         "telematics_ref": "ref-tr9"},
    ], source="samsara")

    (after,) = await pg_db.list_vehicles(acct, include_inactive=True)
    assert after.source == "datatruck", (
        "the creator was overwritten by the last toucher — the stomp "
        "is back"
    )
    # And the honest answer now names both.
    assert after.sources[0] == "datatruck", "the creator must lead"
    assert "samsara" in after.sources
