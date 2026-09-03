"""Vehicle documents — a truck's paperwork survives the truck retiring.

The whole point of the folder: registration, title, insurance and
annual inspections belong to the TRUCK, live where a compliance
officer browses (``{COMPANY}/vehicles/{unit}/``), follow it into the
archive tree when it retires, and come home when it is restored.
"""
from __future__ import annotations

import pytest

from features.vehicles.documents.paths import (
    vehicle_docs_archive_bucket,
    vehicle_docs_bucket,
)


def test_buckets_are_readable_and_traversal_safe():
    assert vehicle_docs_bucket("PTG INC", "6862") == (
        "PTG INC/vehicles/6862/documents")
    # Two different archives, and they must not be confused: a
    # SUPERSEDED PAPER goes inside the truck it belongs to …
    assert vehicle_docs_archive_bucket("PTG INC", "6862") == (
        "PTG INC/vehicles/6862/documents/_archive")
    # … while a RETIRED TRUCK takes its whole folder — papers and work
    # orders together — into the dated archive tree.
    from capabilities.object_storage.paths import vehicle_archive_folder
    assert vehicle_archive_folder("PTG INC", "6862", "2026-08-28") == (
        "PTG INC/vehicles/_archive/2026-08-28/6862")
    # A unit number is user data on a path.  Neutralised, not rejected:
    # the separators become underscores, so it stays ONE component.
    hostile = vehicle_docs_bucket("X", "../../etc")
    assert "/../" not in hostile and not hostile.startswith("..")
    # X / vehicles / <one component> / documents
    assert hostile.count("/") == 3


@pytest.mark.asyncio
async def test_documents_round_trip_and_quota(pg_db):
    acct = (await pg_db.create_account("Docs Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "D-1",
         "telematics_ref": "ref-d1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)

    doc = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="registration",
        bucket="PTG/vehicles/D-1", object_key="registration_x.pdf",
        file_name="reg.pdf", file_size=1000, mime_type="application/pdf",
    )
    docs = await pg_db.list_vehicle_documents(acct, v.id)
    assert [d.id for d in docs] == [doc.id]

    # Delete is soft and refunds the quota; the row records what existed.
    gone = await pg_db.delete_vehicle_document(acct, doc.id)
    assert gone is not None and gone.object_key == "registration_x.pdf"
    assert await pg_db.list_vehicle_documents(acct, v.id) == []
    # Double delete is a no-op, not a second refund.
    assert await pg_db.delete_vehicle_document(acct, doc.id) is None


@pytest.mark.asyncio
async def test_an_unknown_doc_type_is_refused(pg_db):
    acct = (await pg_db.create_account("Docs Refuse Co")).id
    with pytest.raises(ValueError):
        await pg_db.add_vehicle_document(
            acct, 1, doc_type="selfie", bucket="b", object_key="k")


@pytest.mark.asyncio
async def test_archive_moves_the_folder_and_restore_brings_it_home(
    pg_db, tmp_path, monkeypatch,
):
    """The driver-archive recipe, end to end on the local backend:
    physical move + row rewrite, then the exact reverse."""
    from features.vehicles.folder_archive import (
        archive_vehicle_folder, restore_vehicle_folder,
    )

    acct = (await pg_db.create_account("Docs Move Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "M-1",
         "telematics_ref": "ref-m1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)

    # A real object in the real (test-pinned) store.
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.paths import resolve_company_folder
    from features.vehicles.documents.paths import vehicle_docs_bucket
    store = await get_object_storage_for_account(acct, pg_db)
    company_folder = await resolve_company_folder(pg_db, acct, "PTG")
    live = vehicle_docs_bucket(company_folder, "M-1")
    store.put(live, "title_x.pdf", b"%PDF-1.4 test")
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="title", bucket=live,
        object_key="title_x.pdf", file_name="title.pdf", file_size=13,
    )

    archived_bucket = await archive_vehicle_folder(pg_db, acct, v.id)
    assert archived_bucket and "/vehicles/_archive/" in archived_bucket
    (d,) = await pg_db.list_vehicle_documents(acct, v.id)
    # The TRUCK folder moved, so the documents bucket is its child.
    assert d.bucket == f"{archived_bucket}/documents", (
        "document rows must follow the truck folder")
    assert store.get(d.bucket, "title_x.pdf") == b"%PDF-1.4 test"

    home = await restore_vehicle_folder(pg_db, acct, v.id)
    assert home and home.endswith("/vehicles/M-1"), "the truck comes home"
    (d,) = await pg_db.list_vehicle_documents(acct, v.id)
    assert d.bucket == live
    assert store.get(live, "title_x.pdf") == b"%PDF-1.4 test"


@pytest.mark.asyncio
async def test_moving_a_truck_with_no_documents_is_a_cheap_noop(pg_db):
    from features.vehicles.folder_archive import archive_vehicle_folder

    acct = (await pg_db.create_account("Docs Noop Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "N-1",
         "telematics_ref": "ref-n1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    assert await archive_vehicle_folder(pg_db, acct, v.id) is None


# ── An archived truck's page answers from the registry, instantly ────


@pytest.mark.asyncio
async def test_an_archived_trucks_page_never_waits_on_the_provider(
    pg_db, monkeypatch,
):
    """The detail resolver fans out one provider round-trip per company
    (~3-5s each).  For a retired truck that fan-out bought either
    nothing or months-old state — and then the active-only registry
    fallback could not see the truck, so the page loaded forever.

    The short-circuit must fire BEFORE the provider; the sentinel
    raises if it is ever consulted.
    """
    from features.vehicles import router as vr

    acct = (await pg_db.create_account("Archived Detail Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "AD-1",
         "telematics_ref": "ref-ad1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db.deactivate_vehicle(acct, v.id)

    async def _provider_must_not_be_called(*a, **kw):
        raise AssertionError(
            "an archived truck's detail page reached the provider")
    monkeypatch.setattr(vr, "_svc_vehicle_detail",
                        _provider_must_not_be_called)

    async def _tenant(_aid):
        return pg_db
    monkeypatch.setattr(vr, "_get_tenant_db", _tenant)

    matches = await vr._resolve_vehicle(
        "AD-1", None, {"account_id": acct}, allowed=[])
    assert matches, "the archived truck must resolve from the registry"
    assert matches[0].get("name") == "AD-1"


@pytest.mark.asyncio
async def test_a_live_truck_that_inherited_the_name_still_gets_live_data(
    pg_db, monkeypatch,
):
    """Door numbers are reused.  When ANY active row answers to the
    name, the provider path must run — the live truck wins."""
    from features.vehicles import router as vr

    acct = (await pg_db.create_account("Inherited Name Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "IN-1",
         "telematics_ref": "ref-in1"},
    ], source="samsara")
    (old,) = await pg_db.list_vehicles(acct)
    await pg_db.deactivate_vehicle(acct, old.id)
    # The number goes on a different, LIVE truck in another company.
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "OSY", "unit_number": "IN-1",
         "telematics_ref": "ref-in2"},
    ], source="samsara")

    called = []

    async def _provider(*a, **kw):
        called.append(1)
        return []
    monkeypatch.setattr(vr, "_svc_vehicle_detail", _provider)

    async def _tenant(_aid):
        return pg_db
    monkeypatch.setattr(vr, "_get_tenant_db", _tenant)

    await vr._resolve_vehicle("IN-1", None, {"account_id": acct}, allowed=[])
    assert called, "a live truck sharing the name was denied its provider data"


# ── A LIVE truck's detail must carry its registry identity ──────────


@pytest.mark.asyncio
async def test_a_reporting_trucks_detail_carries_registry_id_and_sources(
    pg_db, monkeypatch,
):
    """Documents and Source key off ``registry_id``; Source also reads
    ``sources``.  The provider answers by NAME and knows neither, so a
    LIVE truck's detail payload carried neither — and both cards
    rendered nothing on exactly the trucks that report, while working
    on trailers and manual rows.  The list endpoint has overlaid the
    registry since it was built; this path had not.
    """
    from features.vehicles import router as vr

    acct = (await pg_db.create_account("Live Detail Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "LD-1",
         "telematics_ref": "ref-ld1", "vin": "1HGLIVE000000001"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)

    # What the provider returns: a live row, by name, with no idea the
    # registry exists.
    async def _provider(_aid, _name, company=None):
        return [{"name": "LD-1", "_org": "PTG", "make": "FREIGHTLINER"}]
    monkeypatch.setattr(vr, "_svc_vehicle_detail", _provider)

    async def _tenant(_aid):
        return pg_db
    monkeypatch.setattr(vr, "_get_tenant_db", _tenant)

    (match,) = await vr._resolve_vehicle(
        "LD-1", None, {"account_id": acct}, allowed=[])
    assert match["registry_id"] == v.id, "the card has nothing to ask about"
    assert "samsara" in match["sources"]

    # And through the normalizer the endpoint actually returns.
    out = vr._normalize_detail(match)
    assert out["registry_id"] == v.id


@pytest.mark.asyncio
async def test_a_shared_unit_number_only_claims_its_own_company(
    pg_db, monkeypatch,
):
    """A unit number is a reusable LABEL.  With two live trucks named
    the same, a provider match may take identity only from the row in
    ITS company — the mistake that mis-linked four devices."""
    from features.vehicles import router as vr

    acct = (await pg_db.create_account("Shared Name Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "SN-1",
         "telematics_ref": "ref-sn-ptg"},
        {"company_code": "OSY", "unit_number": "SN-1",
         "telematics_ref": "ref-sn-osy"},
    ], source="samsara")
    rows = {v.company_code: v for v in await pg_db.list_vehicles(acct)}

    async def _provider(_aid, _name, company=None):
        return [{"name": "SN-1", "_org": "OSY"}]
    monkeypatch.setattr(vr, "_svc_vehicle_detail", _provider)

    async def _tenant(_aid):
        return pg_db
    monkeypatch.setattr(vr, "_get_tenant_db", _tenant)

    (match,) = await vr._resolve_vehicle(
        "SN-1", None, {"account_id": acct}, allowed=[])
    assert match["registry_id"] == rows["OSY"].id
    assert match["registry_id"] != rows["PTG"].id


# ── The permission split took nothing from anybody ──────────────────


def test_the_document_flags_were_seeded_to_the_access_they_replaced():
    """Vehicle documents used to ride ``can_manage_vehicles`` (manage)
    and ``can_faults`` OR the own-truck half of the vehicles pair (view).
    The verb/scope flip folded that pair into one view verb with width
    elsewhere, so the derivation the original test replayed cannot be
    expressed any more — the seeds are pinned by ROLE instead.  Moving a
    role in or out of either set is a conscious edit here, never drift."""
    from capabilities.permissions.roles import ROLE_PERMISSIONS
    view = sorted(r.value for r, f in ROLE_PERMISSIONS.items() if f.can_view_vehicle_docs)
    manage = sorted(r.value for r, f in ROLE_PERMISSIONS.items() if f.can_manage_vehicle_docs)
    assert view == ['admin', 'dispatcher', 'driver', 'fleet', 'owner', 'safety']
    assert manage == ['admin', 'fleet', 'owner']
    for role, f in ROLE_PERMISSIONS.items():
        assert f.can_manage_vehicle_docs == f.can_manage_vehicles, (
            f"{role.value}: document MANAGE drifted from can_manage_vehicles")

def test_the_endpoints_ask_for_the_document_flags_not_the_vehicle_ones():
    """The point of the split: filing a truck's papers must not require
    the grant that renames and archives trucks."""
    import inspect

    from features.vehicles.documents import router as doc

    src = inspect.getsource(doc)
    assert 'require_permission("can_manage_vehicle_docs")' in src
    assert 'require_permission("can_view_vehicle_docs")' in src
    assert 'require_permission("can_manage_vehicles")' not in src, (
        "a documents route still rides the rename/archive grant")
    assert 'require_permission_any(' not in src, (
        "the view gate went back to borrowing other features' flags")


# ── /vehicles/documents must outrank /vehicles/{vehicle_name} ───────


def test_the_fleet_list_route_is_mounted_before_the_parametric_one():
    """``/vehicles/documents`` is one segment, so only mount order keeps
    the vehicles router's ``/vehicles/{vehicle_name}`` from answering it
    with a truck named "documents".  That trap once hid /vehicles/config;
    this fails in CI instead of in production."""
    import interfaces.api.app as app_mod

    paths = [getattr(r, "path", "") for r in app_mod.app.routes]
    assert "/api/vehicles/documents" in paths, "the fleet list route is gone"
    assert paths.index("/api/vehicles/documents") < paths.index(
        "/api/vehicles/{vehicle_name}"
    ), "the parametric vehicle route now shadows the documents list"


# ── Everything about one truck lives under that truck ──────────────


def test_a_trucks_papers_and_repairs_are_siblings():
    """The layout the owner asked for: one folder answers "what has
    unit 110 cost me, and is its insurance current?".  Documents used
    to BE the truck folder, which left no room for anything beside
    them."""
    from features.vehicles.documents.paths import vehicle_docs_bucket
    from features.work_orders.paths import work_order_folder

    docs = vehicle_docs_bucket("PTG INC", "110")
    wo = work_order_folder(
        company_folder="PTG INC", work_order_id=128, vehicle_name="110",
        service_date="2026-04-12", vendor_name="Bobs Diesel",
    )
    assert docs == "PTG INC/vehicles/110/documents"
    assert wo.startswith("PTG INC/vehicles/110/work-orders/")
    # Same parent — that IS the feature.
    assert docs.rsplit("/", 1)[0] == wo.rsplit("/", 2)[0]


def test_a_work_order_with_no_truck_keeps_the_dated_tree():
    """Shop supplies have no truck to live under.  The dated tree is
    what that tree is now for — NOT the _generic pen, which means "no
    company could be established" and would misfile a known company's
    invoice as an orphan."""
    from features.work_orders.paths import work_order_folder

    for empty in ("", "   "):
        p = work_order_folder(
            company_folder="PTG INC", work_order_id=131, vehicle_name=empty,
            service_date="2026-04-12", vendor_name="NAPA",
        )
        assert p == "PTG INC/work-orders/2026/04-april/WO-00131_2026-04-12_NAPA"
        assert "_generic" not in p
        # slugify() answers "unknown" for an empty string; a truck by
        # that name must never appear in the tree.
        assert "/vehicles/" not in p


def test_the_truck_token_leaves_the_folder_name():
    """It is the parent now; repeating it read as a typo."""
    from features.work_orders.paths import work_order_folder

    p = work_order_folder(
        company_folder="PTG INC", work_order_id=128, vehicle_name="221",
        service_date="2026-04-12", vendor_name="Bobs",
    )
    assert "_truck221" not in p and p.count("221") == 1


@pytest.mark.asyncio
async def test_archiving_the_same_truck_twice_in_one_day_still_moves_it(pg_db):
    """Archive → restore → archive lands on the SAME dated path, and
    ``shutil.move`` refuses an existing destination (or nests the source
    inside it).  The move then returned False, the rows kept pointing at
    a live folder with no truck, and nothing said so.  Merging is the
    only honest answer: the second archive must not be a silent no-op.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.paths import resolve_company_folder
    from features.vehicles.documents.paths import vehicle_docs_bucket
    from features.vehicles.folder_archive import (
        archive_vehicle_folder, restore_vehicle_folder,
    )

    acct = (await pg_db.create_account("Twice Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "TW-1",
         "telematics_ref": "ref-tw1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    store = await get_object_storage_for_account(acct, pg_db)
    cf = await resolve_company_folder(pg_db, acct, "PTG")
    live = vehicle_docs_bucket(cf, "TW-1")
    store.put(live, "reg.pdf", b"%PDF one")
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="registration", bucket=live,
        object_key="reg.pdf", file_name="reg.pdf", file_size=8)

    first = await archive_vehicle_folder(pg_db, acct, v.id)
    assert first, "first archive must move the truck"
    assert await restore_vehicle_folder(pg_db, acct, v.id)
    second = await archive_vehicle_folder(pg_db, acct, v.id)
    assert second == first, "the same day means the same dated folder"

    (d,) = await pg_db.list_vehicle_documents(acct, v.id)
    assert d.bucket == f"{second}/documents"
    assert store.get(d.bucket, "reg.pdf") == b"%PDF one", (
        "the bytes must survive the round trip, not just the row")
