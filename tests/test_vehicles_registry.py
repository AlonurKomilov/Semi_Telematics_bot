"""Vehicle registry mixin — CRUD, isolation, upsert idempotency.

The registry is the single source of truth for vehicles in our DB;
integrations enrich it.  These tests exercise the storage contract:
manual add/list/get/update/soft-delete, tenant isolation, and the
``upsert_from_integration`` path used by the backfill + ingestor
(idempotent on (account_id, company_code, unit_number), preserving
operator-set vehicle_type/status/notes across re-syncs).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_add_list_get_round_trip(db):
    vid = await db.add_vehicle(
        42, unit_number="247", company_code="PTG", vehicle_type="truck",
        vin="3AKJJHDR7VSXC469", plate_number="PXF8448",
        make="Freightliner", model="Cascadia", year=2027,
    )
    assert vid > 0

    rows = await db.list_vehicles(42)
    assert len(rows) == 1
    v = rows[0]
    assert v.unit_number == "247"
    assert v.vehicle_type == "truck"
    assert v.year == 2027
    assert v.source == "manual"
    assert v.is_active is True

    fetched = await db.get_vehicle(42, vid)
    assert fetched is not None and fetched.vin == "3AKJJHDR7VSXC469"


@pytest.mark.asyncio
async def test_add_trailer_with_no_telematics(db):
    """A trailer is a first-class registry row — no engine, no GPS,
    just a roster entry that inspections/maintenance can reference."""
    await db.add_vehicle(
        42, unit_number="SS006414", vehicle_type="trailer",
        vin="3H3V532XXXX", plate_number="594147T",
    )
    trailers = await db.list_vehicles(42, vehicle_type="trailer")
    assert len(trailers) == 1
    assert trailers[0].vehicle_type == "trailer"
    # The truck filter must NOT return it.
    assert await db.list_vehicles(42, vehicle_type="truck") == []


@pytest.mark.asyncio
async def test_add_rejects_blank_unit_and_bad_type(db):
    with pytest.raises(ValueError, match="unit_number is required"):
        await db.add_vehicle(42, unit_number="   ")
    with pytest.raises(ValueError, match="vehicle_type must be"):
        await db.add_vehicle(42, unit_number="X", vehicle_type="boat")


@pytest.mark.asyncio
async def test_update_partial_and_soft_delete(db):
    vid = await db.add_vehicle(42, unit_number="100", make="Volvo")

    changed = await db.update_vehicle(42, vid, make="Kenworth", notes="leased")
    assert changed is True
    v = await db.get_vehicle(42, vid)
    assert v.make == "Kenworth" and v.notes == "leased"

    # No-op update returns False.
    assert await db.update_vehicle(42, vid) is False

    # Soft delete drops it from the default list but keeps history.
    assert await db.deactivate_vehicle(42, vid) is True
    assert await db.list_vehicles(42) == []
    assert len(await db.list_vehicles(42, include_inactive=True)) == 1


@pytest.mark.asyncio
async def test_tenant_isolation(db):
    await db.add_vehicle(1001, unit_number="A1", company_code="X")
    await db.add_vehicle(2002, unit_number="A1", company_code="X")
    a = await db.list_vehicles(1001)
    b = await db.list_vehicles(2002)
    assert len(a) == 1 and len(b) == 1
    assert await db.count_vehicles(1001) == 1
    # Account A cannot fetch account B's row by id.
    b_id = b[0].id
    assert await db.get_vehicle(1001, b_id) is None


@pytest.mark.asyncio
async def test_upsert_from_integration_idempotent_and_preserves_operator_fields(db):
    # Operator adds a unit by hand and reclassifies + annotates it.
    await db.add_vehicle(
        42, unit_number="247", company_code="PTG",
        vehicle_type="other", status="inactive", notes="in the shop",
    )

    # Integration (Samsara) syncs the same unit — spec refreshes, but
    # the operator's vehicle_type / status / notes must survive.
    rows = [{
        "company_code": "PTG", "unit_number": "247",
        "vehicle_type": "truck",  # integration's guess — must NOT win
        "vin": "NEWVIN123", "make": "Freightliner",
        "telematics_ref": "samsara_vehicle_99",
        "status": "active", "notes": "from samsara",
    }]
    n = await db.upsert_from_integration(42, rows, source="samsara")
    assert n == 1

    v = (await db.list_vehicles(42))[0]
    assert v.vin == "NEWVIN123"               # spec refreshed
    assert v.telematics_ref == "samsara_vehicle_99"
    assert v.source == "samsara"
    assert v.vehicle_type == "other"          # operator classification preserved
    assert v.notes == "in the shop"           # operator note preserved

    # Re-running the same sync is idempotent — still one row.
    await db.upsert_from_integration(42, rows, source="samsara")
    assert await db.count_vehicles(42) == 1


@pytest.mark.asyncio
async def test_upsert_skips_blank_unit_and_empty_list(db):
    assert await db.upsert_from_integration(42, [], source="samsara") == 0
    n = await db.upsert_from_integration(
        42,
        [{"unit_number": "  ", "vin": "x"}, {"unit_number": "9", "vin": "y"}],
        source="samsara",
    )
    assert n == 1  # blank-unit row skipped
    assert await db.count_vehicles(42) == 1


# ── Registry-first read merge (pure helper) ───────────────────


def _reg(id, unit, *, org="PTG", type="truck", ref="", source="manual", vin=""):
    from adapters.storage.vehicles_registry import Vehicle
    return Vehicle(
        id=id, account_id=42, company_code=org, unit_number=unit,
        vehicle_type=type, vin=vin, plate_number="", make="", model="",
        year=None, status="active", source=source, telematics_ref=ref,
        notes="", is_active=True, created_at="", updated_at="",
    )


def _live(id, name, org="PTG"):
    return {"id": id, "name": name, "_org": org,
            "location": {"speedMilesPerHour": 30, "engineState": "On"}}


def test_merge_enriches_match_by_telematics_ref():
    from capabilities.telemetry.warehouse_reader import merge_registry_with_live
    registry = [_reg(1, "247", ref="sam_99", source="samsara")]
    live = [_live("sam_99", "247")]
    out = merge_registry_with_live(registry, live)
    assert len(out) == 1
    assert out[0]["vehicle_type"] == "truck"
    assert out[0]["source"] == "samsara"
    assert out[0]["location"]["engineState"] == "On"   # live data carried
    assert not out[0].get("_no_telemetry")


def test_merge_matches_by_company_unit_case_insensitive():
    from capabilities.telemetry.warehouse_reader import merge_registry_with_live
    registry = [_reg(1, "247", org="PTG")]
    live = [_live("x", "247", org="ptg")]   # different case
    out = merge_registry_with_live(registry, live)
    assert len(out) == 1 and not out[0].get("_no_telemetry")


def test_merge_synthesizes_no_telemetry_for_trailer():
    from capabilities.telemetry.warehouse_reader import merge_registry_with_live
    registry = [_reg(7, "SS006414", type="trailer", vin="3H3V")]
    out = merge_registry_with_live(registry, [])  # no live data at all
    assert len(out) == 1
    assert out[0]["_no_telemetry"] is True
    assert out[0]["vehicle_type"] == "trailer"
    assert out[0]["vin"] == "3H3V"
    assert out[0]["location"] == {}


def test_merge_appends_unregistered_live_vehicle():
    """A Samsara vehicle the registry hasn't caught yet must still
    show (safety) — not vanish behind the registry spine."""
    from capabilities.telemetry.warehouse_reader import merge_registry_with_live
    registry = [_reg(1, "247", ref="sam_99", source="samsara")]
    live = [_live("sam_99", "247"), _live("sam_new", "999")]
    out = merge_registry_with_live(registry, live)
    names = {o["name"] for o in out}
    assert names == {"247", "999"}
    # The unregistered one defaults to truck/samsara.
    extra = next(o for o in out if o["name"] == "999")
    assert extra["vehicle_type"] == "truck" and extra["source"] == "samsara"


# ── Route handlers (create / update / delete) ─────────────────


def _route_tenant(db, account_id=42):
    """Bind the real ``db`` fixture as the tenant the route resolves."""
    from unittest.mock import AsyncMock
    import features.vehicles.router as rt

    async def fake_get_tenant_db(_acct):
        return db
    return rt, fake_get_tenant_db


@pytest.mark.asyncio
async def test_route_create_update_delete(db, monkeypatch):
    rt, fake_tdb = _route_tenant(db)
    monkeypatch.setattr(rt, "_get_tenant_db", fake_tdb)
    user = {"account_id": 42, "sub": "1"}

    created = await rt.create_vehicle(
        rt.VehicleCreate(unit_number="T-99", vehicle_type="trailer", vin="V1"),
        user=user,
    )
    assert created["unit_number"] == "T-99"
    assert created["vehicle_type"] == "trailer"
    vid = created["id"]

    updated = await rt.update_registry_vehicle(
        vid, rt.VehicleUpdate(make="Wabash", status="inactive"), user=user,
    )
    assert updated["make"] == "Wabash"

    out = await rt.delete_registry_vehicle(vid, user=user)
    assert out["deactivated"] is True
    assert await db.list_vehicles(42) == []


@pytest.mark.asyncio
async def test_route_create_duplicate_is_409(db, monkeypatch):
    rt, fake_tdb = _route_tenant(db)
    monkeypatch.setattr(rt, "_get_tenant_db", fake_tdb)
    from fastapi import HTTPException
    user = {"account_id": 42, "sub": "1"}

    await rt.create_vehicle(
        rt.VehicleCreate(unit_number="DUP", company_code="X"), user=user,
    )
    with pytest.raises(HTTPException) as exc:
        await rt.create_vehicle(
            rt.VehicleCreate(unit_number="DUP", company_code="X"), user=user,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_route_update_missing_is_404(db, monkeypatch):
    rt, fake_tdb = _route_tenant(db)
    monkeypatch.setattr(rt, "_get_tenant_db", fake_tdb)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await rt.update_registry_vehicle(
            999999, rt.VehicleUpdate(make="x"), user={"account_id": 42, "sub": "1"},
        )
    assert exc.value.status_code == 404


def test_can_manage_vehicles_default_roles():
    """Owner/Admin/Fleet manage by default; others don't (but the flag
    is delegatable via the Permissions matrix)."""
    from capabilities.permissions.roles import ROLE_PERMISSIONS, Role
    assert ROLE_PERMISSIONS[Role.OWNER].can_manage_vehicles is True
    assert ROLE_PERMISSIONS[Role.ADMIN].can_manage_vehicles is True
    assert ROLE_PERMISSIONS[Role.FLEET].can_manage_vehicles is True
    assert ROLE_PERMISSIONS[Role.SAFETY].can_manage_vehicles is False
    assert ROLE_PERMISSIONS[Role.DRIVER].can_manage_vehicles is False


# ── Datatruck → registry projection (Phase 2-A) ───────────────


@pytest.mark.asyncio
async def test_projection_fills_vin_on_samsara_row_by_unit(db):
    """The key reconciliation: a Samsara-sourced truck has unit '247'
    but vin='' (the warehouse doesn't track VIN).  Datatruck has the
    same unit WITH the VIN and no company scoping.  Projection must
    enrich the existing row (fill the VIN) — NOT create a duplicate."""
    await db.upsert_from_integration(
        42, [{"company_code": "PTG", "unit_number": "247",
              "telematics_ref": "sam_99"}], source="samsara",
    )
    n = await db.project_external_vehicles(
        42,
        [{"unit_number": "247", "vin": "3AKJJHDR7VSXC469",
          "plate_number": "PXF8448", "make": "Freightliner"}],
        vehicle_type="truck", source="datatruck",
    )
    assert n == 1
    rows = await db.list_vehicles(42)
    assert len(rows) == 1                      # NOT duplicated
    v = rows[0]
    assert v.vin == "3AKJJHDR7VSXC469"         # VIN filled in
    assert v.plate_number == "PXF8448"
    assert v.source == "samsara"               # source preserved
    assert v.telematics_ref == "sam_99"        # samsara link preserved


@pytest.mark.asyncio
async def test_projection_matches_by_vin_exact(db):
    await db.add_vehicle(42, unit_number="OLD-NAME", vin="VINMATCH1",
                         company_code="PTG")
    # Datatruck reports a different unit label but the SAME vin.
    n = await db.project_external_vehicles(
        42, [{"unit_number": "247", "vin": "VINMATCH1", "make": "Volvo"}],
        vehicle_type="truck", source="datatruck",
    )
    assert n == 1
    rows = await db.list_vehicles(42)
    assert len(rows) == 1                       # matched by VIN, no dup
    assert rows[0].make == "Volvo"              # empty make filled
    assert rows[0].unit_number == "OLD-NAME"    # existing unit untouched


@pytest.mark.asyncio
async def test_projection_inserts_net_new_trailer(db):
    """A Datatruck trailer Samsara never reports → brand-new registry
    row, typed trailer, source datatruck."""
    n = await db.project_external_vehicles(
        42, [{"unit_number": "SS006414", "vin": "3H3V", "plate_number": "594147T"}],
        vehicle_type="trailer", source="datatruck",
    )
    assert n == 1
    v = (await db.list_vehicles(42, vehicle_type="trailer"))[0]
    assert v.unit_number == "SS006414"
    assert v.source == "datatruck"
    assert v.vin == "3H3V"


@pytest.mark.asyncio
async def test_projection_does_not_clobber_operator_edits(db):
    """Operator hand-corrected the make; a later Datatruck sync must
    not overwrite a non-empty field."""
    vid = await db.add_vehicle(42, unit_number="247", make="CORRECTED",
                               vin="VIN9")
    await db.project_external_vehicles(
        42, [{"unit_number": "247", "vin": "VIN9", "make": "datatruck-make"}],
        vehicle_type="truck", source="datatruck",
    )
    v = await db.get_vehicle(42, vid)
    assert v.make == "CORRECTED"   # non-empty field preserved


@pytest.mark.asyncio
async def test_projection_ambiguous_unit_inserts_rather_than_guess(db):
    """Two trucks named '103' in different companies (a legal Samsara
    state).  A Datatruck '103' with no VIN match is ambiguous — we
    must NOT guess which to enrich; insert a distinct row instead."""
    await db.upsert_from_integration(
        42,
        [{"company_code": "A", "unit_number": "103"},
         {"company_code": "B", "unit_number": "103"}],
        source="samsara",
    )
    await db.project_external_vehicles(
        42, [{"unit_number": "103", "vin": "NEWVIN"}],
        vehicle_type="truck", source="datatruck",
    )
    rows = [v for v in await db.list_vehicles(42) if v.unit_number == "103"]
    # Two samsara + one datatruck (company_code='') = three rows.
    assert len(rows) == 3
    assert any(v.source == "datatruck" and v.vin == "NEWVIN" for v in rows)
