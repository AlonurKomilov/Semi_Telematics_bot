"""The truck's own page says its paper has lapsed.

The expiry alert reaches Telegram at T-30/14/7/1/0 and the page then
said nothing — so anyone who opened unit 110 for an unrelated reason,
standing in front of the very thing the alert is about, learned
nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.vehicles import router as vr


def _iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


async def _truck_with_doc(pg_db, name: str, expires: str, ref: str):
    acct = (await pg_db.create_account(name)).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "CO-1", "telematics_ref": ref},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="insurance", bucket="b", object_key="i.pdf",
        file_name="i.pdf", expires_at=expires)
    return acct, v


@pytest.mark.asyncio
async def test_a_lapsed_paper_becomes_a_condition_on_the_page(pg_db):
    acct, v = await _truck_with_doc(pg_db, "Callout Exp Co", _iso(-9), "r-exp")
    out = await vr._document_callouts(acct, pg_db, ["r-exp"])
    assert len(out) == 1
    c = out[0]
    # `condition`, not a caveat: it stays true until a renewal is filed,
    # and being dismissable is exactly how it would be missed.
    assert c.key == "vehicle.document_expired"
    assert c.entity == "vehicle:r-exp"
    assert "EXPIRED" in c.params["what"]
    assert c.params["unit"] == "CO-1"


@pytest.mark.asyncio
async def test_a_paper_about_to_lapse_is_a_caveat_instead(pg_db):
    acct, v = await _truck_with_doc(pg_db, "Callout Soon Co", _iso(6), "r-soon")
    (c,) = await vr._document_callouts(acct, pg_db, ["r-soon"])
    assert c.key == "vehicle.document_expiring"
    assert c.params["days"] == 6


@pytest.mark.asyncio
async def test_a_paper_far_from_expiry_says_nothing(pg_db):
    """Same bucketing as the alert, reused — so the page and the message
    can never disagree about what "expiring" means."""
    acct, _ = await _truck_with_doc(pg_db, "Callout Quiet Co", _iso(120),
                                    "r-quiet")
    assert await vr._document_callouts(acct, pg_db, ["r-quiet"]) == []


@pytest.mark.asyncio
async def test_one_statement_per_truck_however_many_papers_lapse(pg_db):
    """Three lapsing documents is ONE thing to go and fix; three stacked
    callouts would bury the page they are meant to inform.  The worst
    one speaks."""
    acct, v = await _truck_with_doc(pg_db, "Callout Many Co", _iso(-3),
                                    "r-many")
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="registration", bucket="b", object_key="r.pdf",
        file_name="r.pdf", expires_at=_iso(-30))
    await pg_db.add_vehicle_document(
        acct, v.id, doc_type="cab_card", bucket="b", object_key="c.pdf",
        file_name="c.pdf", expires_at=_iso(5))

    out = await vr._document_callouts(acct, pg_db, ["r-many"])
    assert len(out) == 1, "one truck, one statement"
    assert out[0].params["doc_type"] == "registration", (
        "the worst one speaks — 30 days lapsed outranks 3")


@pytest.mark.asyncio
async def test_a_truck_not_on_this_page_is_not_reported(pg_db):
    acct, _ = await _truck_with_doc(pg_db, "Callout Scope Co", _iso(-1),
                                    "r-other")
    assert await vr._document_callouts(acct, pg_db, ["r-someone-else"]) == []
