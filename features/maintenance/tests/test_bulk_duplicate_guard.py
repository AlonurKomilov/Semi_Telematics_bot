"""The bulk-create duplicate rule, finally applied to humans.

The fault auto-creator has refused to duplicate an open same-type task
since it existed ("only creates a task if one doesn't already exist,
pending/overdue, same vehicle and type") — while the human bulk form
had no check at all: a hundred selected vehicles meant a hundred
tasks, duplicates included.  These pin the two halves that close it:

  * the PREFLIGHT tells the form who is already covered, with the
    server's numbers — the client's own task list is capped at one
    page and would under-count on a large account;
  * ``skip_duplicates`` is re-checked AT CREATE TIME, so a task
    created between preflight and submit is still skipped and the
    confirmed answer cannot rot in the gap.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def api(pg_db):
    db = pg_db
    acct = await db.create_account("Bulk Guard Co")
    await db.add_company(acct.id, "BG", "key_bg", "Bulk Guard")
    owner = await db.create_user(970001, acct.id, role=Role.OWNER)

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client, "db": db, "acct": acct,
            "token": create_jwt(owner.telegram_id, acct.id, "owner"),
        }
    _cp._db = _old


def _hdr(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def _seed_open_task(db, acct_id: int, vehicle: str, task_type="oil_change",
                          status="pending"):
    task_id = await db.add_maintenance_task(
        account_id=acct_id, company_code="BG", vehicle_name=vehicle,
        task_type=task_type, description="seeded for the guard",
        due_date="2027-01-01",
    )
    if status != "pending":
        await db.update_maintenance_status(task_id, status, account_id=acct_id)
    return task_id


BODY = {
    "task_type": "oil_change",
    "description": "guard test bulk",
    "due_date": "2027-02-01",
}


@pytest.mark.asyncio
async def test_preflight_names_exactly_the_covered_vehicles(api):
    s = api
    await _seed_open_task(s["db"], s["acct"].id, "T-1")               # pending
    await _seed_open_task(s["db"], s["acct"].id, "T-2", status="overdue")
    await _seed_open_task(s["db"], s["acct"].id, "T-3", status="completed")
    # A DIFFERENT type on T-4 must not count — same vehicle, other work.
    await _seed_open_task(s["db"], s["acct"].id, "T-4", task_type="brake_service")

    r = await s["client"].post(
        "/api/maintenance/tasks/bulk/preflight",
        headers=_hdr(s["token"]),
        json={"task_type": "oil_change",
              "vehicle_names": ["T-1", "T-2", "T-3", "T-4", "T-5"]},
    )
    assert r.status_code == 200
    # pending and overdue are covered; completed is history, another
    # type is other work, T-5 has nothing.
    assert r.json() == {"duplicates": ["T-1", "T-2"]}


@pytest.mark.asyncio
async def test_skip_duplicates_skips_at_create_time(api):
    s = api
    await _seed_open_task(s["db"], s["acct"].id, "T-1")
    r = await s["client"].post(
        "/api/maintenance/tasks/bulk/create",
        headers=_hdr(s["token"]),
        json={**BODY, "vehicle_names": ["T-1", "T-6", "T-7"],
              "skip_duplicates": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == ["T-1"]
    assert {c["vehicle_name"] for c in body["created"]} == {"T-6", "T-7"}
    assert body["failed"] == []


@pytest.mark.asyncio
async def test_create_anyway_still_creates_the_duplicate(api):
    """'Create anyway' is a real choice, not a decoy — the flag off
    means the server does exactly what it always did."""
    s = api
    await _seed_open_task(s["db"], s["acct"].id, "T-1")
    r = await s["client"].post(
        "/api/maintenance/tasks/bulk/create",
        headers=_hdr(s["token"]),
        json={**BODY, "vehicle_names": ["T-1"], "skip_duplicates": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == []
    assert len(body["created"]) == 1


@pytest.mark.asyncio
async def test_the_gap_between_preflight_and_submit_cannot_rot(api):
    """A task created AFTER the preflight but BEFORE the submit is
    still skipped — the create re-checks rather than trusting the
    answer the user confirmed seconds ago."""
    s = api
    r = await s["client"].post(
        "/api/maintenance/tasks/bulk/preflight",
        headers=_hdr(s["token"]),
        json={"task_type": "oil_change", "vehicle_names": ["T-9"]},
    )
    assert r.json() == {"duplicates": []}          # clean at preflight
    await _seed_open_task(s["db"], s["acct"].id, "T-9")   # the gap
    r = await s["client"].post(
        "/api/maintenance/tasks/bulk/create",
        headers=_hdr(s["token"]),
        json={**BODY, "vehicle_names": ["T-9"], "skip_duplicates": True},
    )
    assert r.json()["skipped"] == ["T-9"]
