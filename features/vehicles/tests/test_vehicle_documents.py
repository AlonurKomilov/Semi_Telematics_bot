"""Vehicle documents — a truck's paperwork survives the truck retiring.

The whole point of the folder: registration, title, insurance and
annual inspections belong to the TRUCK, live where a compliance
officer browses (``{COMPANY}/vehicles/{unit}/``), follow it into the
archive tree when it retires, and come home when it is restored.
"""
from __future__ import annotations

import pytest

from features.work_orders.storage import (
    vehicle_docs_archive_bucket,
    vehicle_docs_bucket,
)


def test_buckets_are_readable_and_traversal_safe():
    assert vehicle_docs_bucket("PTG INC", "6862") == "PTG INC/vehicles/6862"
    assert vehicle_docs_archive_bucket("PTG INC", "6862", "2026-08-28") == (
        "PTG INC/vehicles/_archive/2026-08-28/6862")
    # A unit number is user data on a path.  Neutralised, not rejected:
    # the separators become underscores, so it stays ONE component.
    hostile = vehicle_docs_bucket("X", "../../etc")
    assert "/../" not in hostile and not hostile.startswith("..")
    assert hostile.count("/") == 2  # X / vehicles / <one component>


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
    from features.vehicles.documents import (
        move_documents_on_archive, move_documents_on_restore,
    )

    acct = (await pg_db.create_account("Docs Move Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "M-1",
         "telematics_ref": "ref-m1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)

    # A real object in the real (test-pinned) store.
    from adapters.storage.object_storage import get_object_storage_for_account
    from features.work_orders.storage import (
        resolve_company_folder, vehicle_docs_bucket,
    )
    store = await get_object_storage_for_account(acct, pg_db)
    company_folder = await resolve_company_folder(pg_db, acct, "PTG")
    live = vehicle_docs_bucket(company_folder, "M-1")
    store.put(live, "title_x.pdf", b"%PDF-1.4 test")
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="title", bucket=live,
        object_key="title_x.pdf", file_name="title.pdf", file_size=13,
    )

    archived_bucket = await move_documents_on_archive(pg_db, acct, v.id)
    assert archived_bucket and "/_archive/" in archived_bucket
    (d,) = await pg_db.list_vehicle_documents(acct, v.id)
    assert d.bucket == archived_bucket, "rows must follow the folder"
    assert store.get(archived_bucket, "title_x.pdf") == b"%PDF-1.4 test"

    home = await move_documents_on_restore(pg_db, acct, v.id)
    assert home == live, "restore must bring the paperwork home"
    (d,) = await pg_db.list_vehicle_documents(acct, v.id)
    assert d.bucket == live
    assert store.get(live, "title_x.pdf") == b"%PDF-1.4 test"


@pytest.mark.asyncio
async def test_moving_a_truck_with_no_documents_is_a_cheap_noop(pg_db):
    from features.vehicles.documents import move_documents_on_archive

    acct = (await pg_db.create_account("Docs Noop Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "N-1",
         "telematics_ref": "ref-n1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    assert await move_documents_on_archive(pg_db, acct, v.id) is None
