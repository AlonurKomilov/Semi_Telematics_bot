"""Who touched this truck's paperwork, and when.

A surface with a delete button and compliance stakes had no answer to
"who removed the insurance certificate" — uploads and deletes wrote
nothing anywhere.  These pin that every document act lands on the
TRUCK's trail, which is the list an operator already opens, rather than
a second trail they would have to know to look for.
"""
from __future__ import annotations

import pytest


async def _one_truck(pg_db, name: str):
    acct = (await pg_db.create_account(name)).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "T-1",
         "telematics_ref": f"ref-{name}"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    return acct, v


@pytest.mark.asyncio
async def test_every_document_act_is_recorded_against_the_truck(
    pg_db, monkeypatch,
):
    from features.vehicles.documents import router as doc

    acct, v = await _one_truck(pg_db, "Trail Co")
    recorded: list[tuple] = []

    async def _capture(db, account_id, actor, action, entity_type,
                       entity_id, **kw):
        recorded.append((action, entity_type, entity_id, kw.get("note", "")))

    monkeypatch.setattr(
        "capabilities.activity_trail.recorder.record_simple", _capture)

    await doc._trail(pg_db, acct, {"account_id": acct, "sub": "1", "uid": 1},
                     v.id, "document.upload", "Uploaded insurance: ins.pdf",
                     doc_id=7)

    assert recorded, "the upload wrote nothing to the trail"
    action, entity_type, entity_id, note = recorded[0]
    assert action == "document.upload"
    # The TRUCK's trail, not a documents-only one — one list answers
    # "what happened to unit 110?".
    assert entity_type == "vehicle" and entity_id == v.id
    assert "ins.pdf" in note


@pytest.mark.asyncio
async def test_a_lost_trail_line_never_fails_the_act(pg_db, monkeypatch):
    """An audit line is worth having and not worth failing an upload the
    operator watched succeed.  Best-effort — but the warning is logged,
    because a delete nobody recorded is the case this exists for."""
    from features.vehicles.documents import router as doc

    acct, v = await _one_truck(pg_db, "Trail Fail Co")

    async def _boom(*a, **kw):
        raise RuntimeError("trail store down")

    monkeypatch.setattr(
        "capabilities.activity_trail.recorder.record_simple", _boom)

    # Must not raise.
    await doc._trail(pg_db, acct, {"account_id": acct, "sub": "1", "uid": 1},
                     v.id, "document.delete", "Deleted title: t.pdf")


@pytest.mark.asyncio
async def test_archiving_a_paper_keeps_it_and_moves_it_inside_the_truck(pg_db):
    """Archive is not delete: the row survives with status 'archived',
    the bytes move to the truck's own documents/_archive/, and the card
    stops listing it because the card answers "what is current"."""
    from features.vehicles.documents.paths import (
        vehicle_docs_archive_bucket, vehicle_docs_bucket,
    )

    acct, v = await _one_truck(pg_db, "Archive Paper Co")
    live = vehicle_docs_bucket("PTG", "T-1")
    d = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="registration", bucket=live,
        object_key="reg.pdf", file_name="reg.pdf", file_size=10)

    archived = await pg_db.archive_vehicle_document(
        acct, d.id, bucket=vehicle_docs_archive_bucket("PTG", "T-1"))
    assert archived is not None

    # Gone from the card …
    assert await pg_db.list_vehicle_documents(acct, v.id) == []
    # … but still there, and still inside its own truck.
    row = await pg_db.get_vehicle_document(acct, d.id)
    assert row.status == "archived"
    assert row.bucket == "PTG/vehicles/T-1/documents/_archive"
    assert "/vehicles/_archive/" not in row.bucket, (
        "archiving a PAPER must not file it where a retired TRUCK goes")


@pytest.mark.asyncio
async def test_a_paper_cannot_be_archived_twice(pg_db):
    acct, v = await _one_truck(pg_db, "Archive Twice Co")
    d = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="title", bucket="b", object_key="t.pdf")
    assert await pg_db.archive_vehicle_document(acct, d.id, bucket="x")
    assert await pg_db.archive_vehicle_document(acct, d.id, bucket="x") is None


# ── Archived papers: findable, restorable, and out of everything else ──


@pytest.mark.asyncio
async def test_an_archived_paper_is_findable_when_asked_for(pg_db):
    """Archive was sold on "it still proves the truck was legal for that
    period".  A document nobody can retrieve proves nothing, so the
    listing has to be able to bring it back."""
    acct, v = await _one_truck(pg_db, "Find Archived Co")
    d = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="registration", bucket="b", object_key="r.pdf",
        file_name="r.pdf")
    await pg_db.archive_vehicle_document(acct, d.id, bucket="b/_archive")

    assert await pg_db.list_vehicle_documents(acct, v.id) == []
    (back,) = await pg_db.list_vehicle_documents(
        acct, v.id, include_archived=True)
    assert back.id == d.id and back.status == "archived"


@pytest.mark.asyncio
async def test_a_paper_archived_by_mistake_comes_back(pg_db):
    """An archive with no way back is the trap delete-only was."""
    acct, v = await _one_truck(pg_db, "Restore Paper Co")
    d = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="insurance", bucket="live", object_key="i.pdf",
        file_name="i.pdf")
    await pg_db.archive_vehicle_document(acct, d.id, bucket="live/_archive")

    assert await pg_db.restore_vehicle_document(acct, d.id, bucket="live")
    (row,) = await pg_db.list_vehicle_documents(acct, v.id)
    assert row.status == "active" and row.bucket == "live"
    # Restoring twice is a no-op, not a second move.
    assert await pg_db.restore_vehicle_document(acct, d.id, bucket="live") is None


@pytest.mark.asyncio
async def test_the_shared_query_stays_active_by_default(pg_db):
    """THE REGRESSION THIS EXISTS FOR.  The DOT binder, the expiry
    callouts and the AI tool all read this one query.  Widening its
    default — which I did once and caught by reading, not by testing —
    feeds superseded papers to all three, and the callouts would then
    warn about a truck that already has its replacement on file.

    Only a caller that OFFERS an archived tab may ask for them.
    """
    acct, v = await _one_truck(pg_db, "Default Scope Co")
    live = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="insurance", bucket="b", object_key="new.pdf",
        file_name="new.pdf", expires_at="2027-01-31")
    old = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="insurance", bucket="b", object_key="old.pdf",
        file_name="old.pdf", expires_at="2024-01-31")
    await pg_db.archive_vehicle_document(acct, old.id, bucket="b/_archive")

    default = await pg_db.list_account_vehicle_documents(acct)
    assert [r["id"] for r in default] == [live.id], (
        "a superseded paper reached the binder / callouts / AI tool")

    asked = await pg_db.list_account_vehicle_documents(
        acct, include_archived=True)
    assert {r["id"] for r in asked} == {live.id, old.id}
    assert {r["status"] for r in asked} == {"active", "archived"}


@pytest.mark.asyncio
async def test_an_expired_paper_that_was_archived_raises_no_alert(pg_db):
    """The end the regression would have reached: yesterday's lapsed
    registration must not page anyone once this year's is filed."""
    acct, v = await _one_truck(pg_db, "Silent Archived Co")
    old = await pg_db.add_vehicle_document(
        acct, v.id, doc_type="registration", bucket="b", object_key="o.pdf",
        file_name="o.pdf", expires_at="2024-01-31")
    assert len(await pg_db.get_expiring_vehicle_documents(acct)) == 1

    await pg_db.archive_vehicle_document(acct, old.id, bucket="b/_archive")
    assert await pg_db.get_expiring_vehicle_documents(acct) == []
