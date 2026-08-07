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

from capabilities.integrations import reconciliation as recon


@pytest.mark.asyncio
async def test_add_list_get_round_trip(db):
    vid = await db.add_vehicle(
        42, unit_number="247", company_code="PTG", vehicle_type="truck",
        vin="1HGTEST0000000001", plate_number="ABC1234",
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
    assert fetched is not None and fetched.vin == "1HGTEST0000000001"


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
async def test_one_unusable_year_cannot_take_the_whole_roster_down(db):
    """A provider's sloppy value must cost its own field, not the fleet.

    Samsara sends model year as a STRING ('2026') and asyncpg refuses a
    str for an INTEGER column.  The roster upserts inside ONE
    transaction, so that single bad bind rolled back every other vehicle
    with it — and the caller logged it at debug, so the registry sat
    frozen for 47 days while the ingest reported success every minute.
    """
    rows = [
        {"company_code": "PTG", "unit_number": "301",
         "telematics_ref": "sam_301", "year": "2026"},        # provider string
        {"company_code": "PTG", "unit_number": "302",
         "telematics_ref": "sam_302", "year": 2024},          # honest int
        {"company_code": "PTG", "unit_number": "303",
         "telematics_ref": "sam_303", "year": "N/A"},         # not a year
        {"company_code": "PTG", "unit_number": "304",
         "telematics_ref": "sam_304", "year": "garbled"},     # nor this
    ]
    n = await db.upsert_from_integration(42, rows, source="samsara")
    assert n == 4, "every vehicle lands, however the provider spelled the year"

    by_unit = {v.unit_number: v for v in await db.list_vehicles(42)}
    assert set(by_unit) >= {"301", "302", "303", "304"}
    # A year we can read is kept, whichever type it arrived as.
    assert by_unit["301"].year == 2026
    assert by_unit["302"].year == 2024
    # One we cannot read is simply absent — never a guess, never a crash.
    assert by_unit["303"].year in (None, 0)
    assert by_unit["304"].year in (None, 0)


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
async def test_upsert_blank_spec_does_not_wipe_existing_cross_source(db):
    """Cross-source data-loss guard: a Samsara tick with NO VIN/make
    (passes '') must NOT erase the VIN/make a prior sync (Datatruck) filled
    in.  Without fill-don't-wipe, Samsara's 60s ingest blanked the Datatruck
    VIN every minute — the row showed empty even though Datatruck delivered
    it."""
    # Datatruck-style sync fills the paperwork Samsara lacks.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "103",
        "vin": "1ABC123", "make": "Freightliner",
        "plate_number": "TX-9", "year": 2021,
    }], source="datatruck")

    # Samsara tick on the SAME unit — live tracker, blank paperwork.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "103",
        "vin": "", "make": "", "plate_number": "", "year": None,
        "telematics_ref": "samsara_99",
    }], source="samsara")

    v = (await db.list_vehicles(42))[0]
    assert v.vin == "1ABC123"                # preserved, NOT wiped
    assert v.make == "Freightliner"          # preserved
    assert v.plate_number == "TX-9"          # preserved
    assert v.year == 2021                    # preserved
    assert v.telematics_ref == "samsara_99"  # Samsara filled what it HAS

    # Under source precedence (default: Datatruck owns the plate), Samsara —
    # the LOWER-priority source — does NOT overwrite even with a real value.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "103", "plate_number": "TX-SAMSARA",
    }], source="samsara")
    v2 = (await db.list_vehicles(42))[0]
    assert v2.plate_number == "TX-9"         # Datatruck (higher priority) kept

    # The owner (Datatruck) DOES overwrite its own field with a new value.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "103", "plate_number": "TX-NEW",
    }], source="datatruck")
    v3 = (await db.list_vehicles(42))[0]
    assert v3.plate_number == "TX-NEW"       # higher-priority source overwrites
    assert v3.vin == "1ABC123"               # untouched field still preserved


@pytest.mark.asyncio
async def test_precedence_owner_wins_a_genuine_conflict(db):
    """Two sources, two DIFFERENT non-empty makes.  Default precedence makes
    Datatruck the owner of paperwork, so it wins regardless of WRITE ORDER —
    not 'whoever wrote last'."""
    # Samsara writes a make first.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "55", "make": "Frtlnr",
    }], source="samsara")
    # Datatruck (higher priority for make) sends a different make → wins.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "55", "make": "Freightliner",
    }], source="datatruck")
    assert (await db.list_vehicles(42))[0].make == "Freightliner"
    # A later Samsara tick can't take it back (lower priority).
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "55", "make": "Frtlnr",
    }], source="samsara")
    assert (await db.list_vehicles(42))[0].make == "Freightliner"


@pytest.mark.asyncio
async def test_precedence_is_configurable_per_field(db):
    """The owner can flip a field's precedence: make Samsara authoritative
    for ``make``, and now Samsara's value wins over Datatruck's."""
    import json
    # A real account row — set_account_setting has a FK to accounts.
    aid = (await db.create_account("Precedence Co")).id
    await db.set_account_setting(
        aid, "vehicle_field_precedence",
        json.dumps({"make": ["samsara", "datatruck"]}),
    )
    await db.upsert_from_integration(aid, [{
        "company_code": "CFT", "unit_number": "55", "make": "Freightliner",
    }], source="datatruck")
    # Samsara is now primary for make → overwrites Datatruck's value.
    await db.upsert_from_integration(aid, [{
        "company_code": "CFT", "unit_number": "55", "make": "Samsara-Make",
    }], source="samsara")
    assert (await db.list_vehicles(aid))[0].make == "Samsara-Make"


@pytest.mark.asyncio
async def test_operator_pin_is_never_overwritten_by_sync(db):
    """A field the operator edits is pinned (provenance=manual); no
    integration — even the higher-priority one — overwrites it (Layer 2)."""
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "77", "vin": "SAMVIN",
    }], source="samsara")
    vid = (await db.list_vehicles(42))[0].id
    # Operator hand-corrects the VIN.
    await db.update_vehicle(42, vid, vin="OPERATOR-VIN")
    # Datatruck (higher priority for VIN) tries a different VIN → blocked.
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "77", "vin": "DATATRUCK-VIN",
    }], source="datatruck")
    assert (await db.list_vehicles(42))[0].vin == "OPERATOR-VIN"


@pytest.mark.asyncio
async def test_set_precedence_round_trips_and_validates(db):
    """The config setter expands a per-field primary pick into a full order,
    ignores unknown fields / sources, and round-trips through the options
    payload the panel reads."""
    aid = (await db.create_account("Prec Co")).id
    await recon.set_precedence(db, aid, "vehicle", {
        "make": "samsara",     # valid → applied
        "vin": "bogus",        # invalid source → ignored (stays default)
        "nope": "samsara",     # unknown field → ignored
    })
    opts = await recon.precedence_options(db, aid, "vehicle")
    by = {f["key"]: f["primary"] for f in opts["fields"]}
    assert by["make"] == "samsara"
    assert by["vin"] == "datatruck"          # default kept
    assert "nope" not in by
    assert opts["sources"] == ["datatruck", "samsara"]


# ── Cross-source conflicts (Layer 3) ──────────────────────────


@pytest.mark.asyncio
async def test_conflict_recorded_when_sources_disagree(db):
    """Two sources, two DIFFERENT non-empty makes → an open conflict naming
    the stored winner + the other value (so the operator can review)."""
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Freightliner",
    }], source="datatruck")
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Frtlnr",
    }], source="samsara")
    conflicts = await recon.list_open(db, 42, entity_type="vehicle")
    makes = [c for c in conflicts if c["field"] == "make"]
    assert len(makes) == 1
    c = makes[0]
    assert c["current_value"] == "Freightliner"   # stored winner (Datatruck)
    assert c["current_source"] == "datatruck"
    assert c["incoming_value"] == "Frtlnr"         # the other (Samsara)
    assert c["incoming_source"] == "samsara"
    assert await recon.count_open(db, 42) == 1


@pytest.mark.asyncio
async def test_conflict_clears_when_sources_agree(db):
    """A disagreement that later resolves itself (the lower source corrects
    upstream) auto-clears the open conflict."""
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Freightliner",
    }], source="datatruck")
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Frtlnr",
    }], source="samsara")
    assert await recon.count_open(db, 42) == 1
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Freightliner",
    }], source="samsara")
    assert await recon.count_open(db, 42) == 0


@pytest.mark.asyncio
async def test_resolve_conflict_pins_chosen_value(db):
    """Resolving picks a value, writes it onto the vehicle, PINS it (so no
    sync undoes the call), and closes the conflict."""
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Freightliner",
    }], source="datatruck")
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Frtlnr",
    }], source="samsara")
    cid = (await recon.list_open(db, 42, entity_type="vehicle"))[0]["id"]
    res = await recon.resolve(db, 42, cid, chosen_value="Frtlnr", resolved_by=1)
    assert res is not None
    assert (await db.list_vehicles(42))[0].make == "Frtlnr"
    assert await recon.count_open(db, 42) == 0
    # Pinned — a later higher-priority Datatruck sync can't change it back,
    # and raises no new conflict (an operator pin isn't a source conflict).
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "88", "make": "Freightliner",
    }], source="datatruck")
    assert (await db.list_vehicles(42))[0].make == "Frtlnr"
    assert await recon.count_open(db, 42) == 0


# ── Identity reconciliation / de-dup (kind C) ─────────────────


@pytest.mark.asyncio
async def test_upsert_vin_reconciles_instead_of_duplicating(db):
    """Datatruck-first: a truck Datatruck synced (no company scoping) must NOT
    be duplicated when Samsara later reports it under a company — Samsara
    reconciles on VIN, enriches the row, and adopts the company."""
    await db.project_external_vehicles(42, [{
        "unit_number": "103", "vin": "1ABC123", "make": "Freightliner",
    }], vehicle_type="truck", source="datatruck")
    await db.upsert_from_integration(42, [{
        "company_code": "CFT", "unit_number": "103", "vin": "1ABC123",
        "telematics_ref": "sam_9",
    }], source="samsara")
    rows = await db.list_vehicles(42)
    assert len(rows) == 1                       # reconciled, NOT duplicated
    v = rows[0]
    assert v.vin == "1ABC123"
    assert v.telematics_ref == "sam_9"          # Samsara enriched it
    assert v.company_code == "CFT"              # adopted the real company


@pytest.mark.asyncio
async def test_find_duplicate_vehicles_by_vin(db):
    """Two active rows sharing a non-empty VIN surface as a duplicate."""
    await db.add_vehicle(42, unit_number="A1", vin="DUPVIN", company_code="X")
    await db.add_vehicle(42, unit_number="A2", vin="DUPVIN", company_code="Y")
    await db.add_vehicle(42, unit_number="B1", vin="UNIQUEVIN")
    dups = await db.find_duplicate_vehicles(42)
    assert len(dups) == 1
    assert dups[0]["vin"] == "DUPVIN"
    assert len(dups[0]["vehicle_ids"]) == 2
    assert set(dups[0]["units"]) == {"A1", "A2"}


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
    from features.vehicles.warehouse.readers import merge_registry_with_live
    registry = [_reg(1, "247", ref="sam_99", source="samsara")]
    live = [_live("sam_99", "247")]
    out = merge_registry_with_live(registry, live)
    assert len(out) == 1
    assert out[0]["vehicle_type"] == "truck"
    assert out[0]["source"] == "samsara"
    assert out[0]["location"]["engineState"] == "On"   # live data carried
    assert not out[0].get("_no_telemetry")


def test_merge_matches_by_company_unit_case_insensitive():
    from features.vehicles.warehouse.readers import merge_registry_with_live
    registry = [_reg(1, "247", org="PTG")]
    live = [_live("x", "247", org="ptg")]   # different case
    out = merge_registry_with_live(registry, live)
    assert len(out) == 1 and not out[0].get("_no_telemetry")


def test_merge_synthesizes_no_telemetry_for_trailer():
    from features.vehicles.warehouse.readers import merge_registry_with_live
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
    from features.vehicles.warehouse.readers import merge_registry_with_live
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
    # ``uid`` is on every real token since the user-id rollout — the
    # activity trail resolves the actor from it, no platform DB needed.
    user = {"account_id": 42, "sub": "1", "uid": 1}

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
    # ``uid`` is on every real token since the user-id rollout — the
    # activity trail resolves the actor from it, no platform DB needed.
    user = {"account_id": 42, "sub": "1", "uid": 1}

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
            999999, rt.VehicleUpdate(make="x"), user={"account_id": 42, "sub": "1", "uid": 1},
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
        [{"unit_number": "247", "vin": "1HGTEST0000000001",
          "plate_number": "ABC1234", "make": "Freightliner"}],
        vehicle_type="truck", source="datatruck",
    )
    assert n == 1
    rows = await db.list_vehicles(42)
    assert len(rows) == 1                      # NOT duplicated
    v = rows[0]
    assert v.vin == "1HGTEST0000000001"         # VIN filled in
    assert v.plate_number == "ABC1234"
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


# ── Datatruck ref binding (ref-first identity ladder) ─────────


@pytest.mark.asyncio
async def test_projection_stamps_ref_then_matches_ref_first(db):
    """First sync DISCOVERS the row by VIN and stamps datatruck_ref; from
    then on the ref decides identity outright — even if the natural keys
    drift upstream, the same asset updates in place (no duplicate, no
    re-guessing)."""
    await db.upsert_from_integration(
        42, [{"company_code": "PTG", "unit_number": "247",
              "vin": "VREF1", "telematics_ref": "sam_1"}], source="samsara",
    )
    # Discovery pass: VIN match → ref stamped.
    n = await db.project_external_vehicles(
        42, [{"external_id": "T9", "unit_number": "247", "vin": "VREF1",
              "make": "Volvo"}],
        vehicle_type="truck", source="datatruck",
    )
    assert n == 1
    v = (await db.list_vehicles(42))[0]
    assert v.datatruck_ref == "T9"
    assert v.make == "Volvo"

    # Upstream corrects the VIN — the ref still wins (identity kept),
    # and the VIN flows as ordinary datatruck-owned enrichment.
    await db.project_external_vehicles(
        42, [{"external_id": "T9", "unit_number": "247", "vin": "VREF1-FIXED"}],
        vehicle_type="truck", source="datatruck",
    )
    rows = await db.list_vehicles(42)
    assert len(rows) == 1                    # no duplicate row
    assert rows[0].vin == "VREF1-FIXED"
    assert rows[0].datatruck_ref == "T9"


@pytest.mark.asyncio
async def test_projection_insert_stamps_ref(db):
    """A net-new Datatruck asset lands with its ref bound from birth."""
    await db.project_external_vehicles(
        42, [{"external_id": "TR7", "unit_number": "SS006109", "vin": "3H3X"}],
        vehicle_type="trailer", source="datatruck",
    )
    v = (await db.list_vehicles(42, vehicle_type="trailer"))[0]
    assert v.datatruck_ref == "TR7"


# ── Preview / plan (dry-run reconciliation) ───────────────────


@pytest.mark.asyncio
async def test_plan_classifies_new_enrich_review(db):
    # Existing registry: 204 (has VIN, no plate), 305 (no VIN), and two
    # rows sharing unit 900 (ambiguous).
    await db.add_vehicle(42, unit_number="204", vin="VIN204")
    await db.add_vehicle(42, unit_number="305")
    await db.add_vehicle(42, unit_number="900", company_code="A")
    await db.add_vehicle(42, unit_number="900", company_code="B")

    rows = [
        {"unit_number": "204", "vin": "VIN204", "plate_number": "P1"},  # vin match → enrich (plate)
        {"unit_number": "305", "vin": "VINNEW", "make": "Volvo"},       # unit match → enrich (vin, make)
        {"unit_number": "777", "vin": "V777"},                          # no match → new
        {"unit_number": "900", "vin": "V900"},                          # ambiguous → review
    ]
    diff = await db.plan_external_vehicles(42, rows, vehicle_type="truck")
    assert diff["counts"]["new"] == 1
    assert diff["counts"]["enrich"] == 2
    assert diff["counts"]["review"] == 1
    assert {f for e in diff["enrich"] for f in e["fills"]} >= {"plate_number", "vin", "make"}
    # A plan NEVER writes.
    assert len(await db.list_vehicles(42)) == 4


@pytest.mark.asyncio
async def test_plan_matches_by_plate(db):
    # Existing has a plate but a DIFFERENT unit name than Datatruck uses;
    # plate matching should still reconcile them (no duplicate).
    await db.add_vehicle(42, unit_number="OLDNAME", plate_number="XYZ999")
    rows = [{"unit_number": "204", "plate_number": "XYZ999", "vin": "V1"}]
    diff = await db.plan_external_vehicles(42, rows, vehicle_type="truck")
    assert diff["counts"]["new"] == 0
    assert diff["counts"]["enrich"] == 1
    assert diff["enrich"][0]["by"] == "plate"
