"""A truck's paper announces itself before it lapses — not after.

The expiry date was captured at upload and then said nothing: an
expired insurance certificate rendered in the same grey as a current
one.  These pin the two things that make the alert useful rather than
noisy — the buckets, and the claim-once ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from features.vehicles.documents.expiration import (
    BUCKETS, classify, describe,
)

TODAY = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _doc(doc_id: int, expires: str, doc_type: str = "insurance") -> dict:
    return {
        "id": doc_id, "vehicle_id": 7, "doc_type": doc_type,
        "file_name": "x.pdf", "expires_at": expires,
        "unit_number": "110", "company_code": "PTG",
    }


def test_the_buckets_match_the_driver_ones():
    """The two document families must speak on the same schedule — a
    carrier hearing about a driver's medical card at T-30 and a truck's
    registration at T-45 would read as a bug in one of them.  Imported
    HERE rather than in production code: the coupling costs nothing in
    a test and would couple two features anywhere else."""
    from features.drivers.expiration import BUCKETS as DRIVER_BUCKETS

    assert BUCKETS == DRIVER_BUCKETS


def test_a_document_speaks_only_at_its_bucket_edges():
    # T-30 exactly → the 30 bucket.
    (e,) = classify([_doc(1, "2026-09-29")], TODAY)
    assert (e.days_left, e.bucket) == (30, 30)
    # T-31 → nothing yet.  This is what keeps it from being a nag.
    assert classify([_doc(1, "2026-09-30")], TODAY) == []
    # T-8 falls into the 14 bucket, not 7 — the next edge it crosses.
    (e,) = classify([_doc(1, "2026-09-07")], TODAY)
    assert e.bucket == 14


def test_past_due_collapses_onto_one_bucket():
    """Overdue must not open a new bucket per day overdue, or a lapsed
    registration would alert every morning forever."""
    a = classify([_doc(1, "2026-08-20")], TODAY)[0]
    b = classify([_doc(1, "2026-01-01")], TODAY)[0]
    assert a.bucket == b.bucket == 0
    assert a.days_left < 0 and b.days_left < 0


def test_an_unreadable_expiry_is_silent_not_urgent():
    """A typo in a date field is a typo.  Inventing an alert from it
    would train people to distrust the ones that matter."""
    assert classify([_doc(1, "not-a-date")], TODAY) == []
    assert classify([_doc(1, "")], TODAY) == []


def test_the_wording_says_what_an_operator_would_say():
    assert "EXPIRED 10d ago" in describe(classify([_doc(1, "2026-08-20")], TODAY)[0])
    assert "TODAY" in describe(classify([_doc(1, "2026-08-30")], TODAY)[0])
    assert "tomorrow" in describe(classify([_doc(1, "2026-08-31")], TODAY)[0])
    assert "Insurance" in describe(classify([_doc(1, "2026-09-29")], TODAY)[0])


@pytest.mark.asyncio
async def test_the_ledger_claims_each_bucket_once(pg_db):
    acct = (await pg_db.create_account("Doc Expiry Co")).id
    assert await pg_db.record_vehicle_doc_notification(1, 30) is True
    assert await pg_db.record_vehicle_doc_notification(1, 30) is False, (
        "a second scheduler tick must not re-send the same notice")
    # A different bucket for the same doc still speaks.
    assert await pg_db.record_vehicle_doc_notification(1, 7) is True
    assert acct  # fixture sanity


@pytest.mark.asyncio
async def test_an_archived_trucks_paperwork_never_speaks(pg_db):
    """Archiving stops every alert about a truck, and a document alert
    is an alert.  Filtered in the QUERY so no caller can forget."""
    acct = (await pg_db.create_account("Doc Archive Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "EX-1",
         "telematics_ref": "ref-ex1"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="insurance", bucket="b", object_key="k",
        expires_at="2026-09-05")

    assert len(await pg_db.get_expiring_vehicle_documents(acct)) == 1
    await pg_db.deactivate_vehicle(acct, v.id)
    assert await pg_db.get_expiring_vehicle_documents(acct) == []
