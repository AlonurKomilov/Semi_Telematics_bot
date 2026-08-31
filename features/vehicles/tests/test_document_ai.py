"""What the assistant may say and do about a truck's paperwork.

The read tool exists for the one question the Documents page cannot
answer by looking — which trucks have NOTHING on file.  The write
action exists so a cab card photographed on a phone files itself, and
its whole safety rests on never guessing which truck it belongs to.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.vehicles.documents import ai_actions, ai_tool


def _iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


async def _fleet(pg_db, name: str):
    acct = (await pg_db.create_account(name)).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "A-1", "telematics_ref": "a1"},
        {"company_code": "PTG", "unit_number": "A-2", "telematics_ref": "a2"},
    ], source="samsara")
    return acct, {v.unit_number: v for v in await pg_db.list_vehicles(acct)}


@pytest.mark.asyncio
async def test_it_answers_the_question_the_page_cannot(pg_db):
    """Absence.  Everything else is visible by scrolling; "which trucks
    have no insurance" needs the roster compared against the papers."""
    acct, v = await _fleet(pg_db, "AI Missing Co")
    await pg_db.add_vehicle_document(
        acct, v["A-1"].id, doc_type="insurance", bucket="b",
        object_key="i.pdf", expires_at=_iso(200))

    out = await ai_tool.get_vehicle_documents_status(
        {"missing_type": "insurance"}, None, account_id=acct, db=pg_db)
    assert out["missing_count"] == 1
    assert out["vehicles"] == ["A-2"]


@pytest.mark.asyncio
async def test_it_leads_with_what_is_already_wrong(pg_db):
    acct, v = await _fleet(pg_db, "AI Order Co")
    await pg_db.add_vehicle_document(
        acct, v["A-1"].id, doc_type="registration", bucket="b",
        object_key="r.pdf", expires_at=_iso(-20))
    await pg_db.add_vehicle_document(
        acct, v["A-2"].id, doc_type="cab_card", bucket="b",
        object_key="c.pdf", expires_at=_iso(10))

    out = await ai_tool.get_vehicle_documents_status(
        {}, None, account_id=acct, db=pg_db)
    assert out["expired_count"] == 1 and out["expiring_count"] == 1
    assert out["documents"][0]["vehicle"] == "A-1", "worst first"
    assert "EXPIRED" in out["documents"][0]["status"]


@pytest.mark.asyncio
async def test_the_action_refuses_to_guess_the_truck(pg_db):
    """A cab card filed against the wrong tractor is worse than none,
    because it reads as done."""
    out = await ai_actions.file_vehicle_document_action(
        {"doc_type": "cab_card"}, None, account_id=1, db=pg_db)
    assert "error" in out and "which vehicle" in out["error"].lower()


@pytest.mark.asyncio
async def test_the_action_proposes_rather_than_files(pg_db):
    out = await ai_actions.file_vehicle_document_action(
        {"vehicle_name": "A-1", "doc_type": "insurance",
         "expires_at": _iso(300), "source_files": ["ins.jpg"]},
        None, account_id=1, db=pg_db)
    # A proposal, never a write — the human approves first.
    assert out.get("proposal") or out.get("action") or "summary" in str(out)
    assert "insurance" in str(out).lower()


@pytest.mark.asyncio
async def test_an_unreadable_date_is_dropped_not_guessed(pg_db):
    """Same rule as the extractor: an empty field costs a keystroke, a
    plausible wrong one costs a missed expiry."""
    norm = ai_actions._normalize(
        {"vehicle_name": "A-1", "expires_at": "01/31/2027"})
    assert norm["expires_at"] == ""


@pytest.mark.asyncio
async def test_an_invented_doc_type_becomes_other_not_a_422(pg_db):
    norm = ai_actions._normalize(
        {"vehicle_name": "A-1", "doc_type": "smog thing"})
    assert norm["doc_type"] == "other"


@pytest.mark.asyncio
async def test_the_executor_names_where_the_client_uploads(pg_db):
    acct, v = await _fleet(pg_db, "AI Exec Co")
    out = await ai_actions._execute_file_vehicle_document(
        {"vehicle_name": "A-1", "doc_type": "cab_card",
         "expires_at": _iso(400), "source_files": ["cab.jpg"]},
        acct, {}, pg_db)
    assert out["created"] is True
    # It creates NOTHING — the document IS the file, and the file is on
    # the user's device.  This just says where to send it.
    assert out["target_id"] == str(v["A-1"].id)
    assert out["upload_path"].endswith(f"/registry/{v['A-1'].id}/documents")
    assert out["source_files"] == ["cab.jpg"]


@pytest.mark.asyncio
async def test_a_shared_unit_number_is_a_question_not_a_coin_toss(pg_db):
    """Door numbers are reused across companies.  Two live trucks with
    one number must produce a question, never a guess."""
    acct = (await pg_db.create_account("AI Ambig Co")).id
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "S-1", "telematics_ref": "s1"},
        {"company_code": "OSY", "unit_number": "S-1", "telematics_ref": "s2"},
    ], source="samsara")
    out = await ai_actions._execute_file_vehicle_document(
        {"vehicle_name": "S-1"}, acct, {}, pg_db)
    assert out["created"] is False
    assert "more than one" in out["message"].lower()
