"""One device on the map is one row, however many registry rows claim it.

A provider rename that landed as a new unit number left two registry rows
with one Samsara id (60 "229" and 1003 "229 Idris Ahmed").  The merge
emitted both; every client keys a marker or a list row by the provider
id, so the map carried a duplicate React key and the list grew ghost
rows that survived every filter.  The merge now keeps the row the ingest
keeps — active first, then the lower id — and folds the other's
provenance in.
"""

from __future__ import annotations

from types import SimpleNamespace

from features.vehicles.warehouse.readers import merge_registry_with_live


def _reg(id_, unit, ref, *, active=True, sources=("samsara",)):
    return SimpleNamespace(
        id=id_, unit_number=unit, company_code="RMR", telematics_ref=ref,
        vehicle_type="truck", source="samsara", sources=list(sources), is_active=active,
    )


LIVE = [{"id": "281475000454550", "name": "229 Idris Ahmed", "_org": "RMR",
         "location": {"latitude": 41.0, "longitude": -91.0}}]


def test_two_registry_rows_for_one_device_draw_once():
    out = merge_registry_with_live(
        [_reg(60, "229", "281475000454550", sources=("samsara", "datatruck", "manual")),
         _reg(1003, "229 Idris Ahmed", "281475000454550")],
        LIVE,
    )
    ids = [r["id"] for r in out if r.get("id")]
    assert ids.count("281475000454550") == 1
    row = next(r for r in out if r.get("id") == "281475000454550")
    # The row that existed first is the one drawn...
    assert row["_registry_id"] == 60
    # ...carrying everything both rows knew about where the record came from.
    assert set(row["sources"]) >= {"samsara", "datatruck", "manual"}


def test_a_retired_claimant_loses_to_an_active_one_whatever_its_id():
    out = merge_registry_with_live(
        [_reg(60, "229", "281475000454550", active=False),
         _reg(1003, "229 Idris Ahmed", "281475000454550")],
        LIVE,
    )
    row = next(r for r in out if r.get("id") == "281475000454550")
    assert row["_registry_id"] == 1003
    assert [r["id"] for r in out if r.get("id")].count("281475000454550") == 1


def test_distinct_devices_are_untouched():
    out = merge_registry_with_live(
        [_reg(1, "101", "A"), _reg(2, "102", "B")],
        [{"id": "A", "name": "101", "_org": "RMR", "location": {}},
         {"id": "B", "name": "102", "_org": "RMR", "location": {}}],
    )
    assert sorted(r["_registry_id"] for r in out) == [1, 2]
