"""Closed maintenance history stays in the database.

Every reader that derives urgency throws completed / cancelled / done
rows away in Python.  The AI read them anyway — twice on every chat
turn, once for the context snapshot and once for whichever maintenance
tool the model called — so the cost of an answer about today's open
work grew with the account's entire service history.

Two halves, both guarded here: the store really does filter (the SQL),
and the callers really do ask it to (the kwargs).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from adapters.storage.maintenance import CLOSED_TASK_STATUSES


# ── The store's own filter ────────────────────────────────────────

@pytest_asyncio.fixture
async def mdb(pg_db):
    yield pg_db


@pytest.mark.asyncio
async def test_open_only_leaves_every_closed_spelling_behind(mdb):
    acct = await mdb.create_account("Shop Co")
    ids = {}
    for status in ("pending", "overdue", "in_progress",
                   *sorted(CLOSED_TASK_STATUSES)):
        tid = await mdb.add_maintenance_task(
            acct.id, "OSY", "231", "oil_change", f"task-{status}",
        )
        await mdb.update_maintenance_status(tid, status, account_id=acct.id)
        ids[status] = tid

    everything = await mdb.get_maintenance_tasks(acct.id)
    open_rows = await mdb.get_maintenance_tasks(acct.id, open_only=True)

    assert len(everything) == len(ids)
    got = {r["id"] for r in open_rows}
    assert got == {ids["pending"], ids["overdue"], ids["in_progress"]}
    for closed in CLOSED_TASK_STATUSES:
        assert ids[closed] not in got, f"{closed!r} survived open_only"


@pytest.mark.asyncio
async def test_a_task_with_no_status_is_open(mdb):
    """Unknown and empty statuses stay OPEN — the Python filters treat
    them that way, and a row nobody has classified is not finished."""
    acct = await mdb.create_account("Shop Co 2")
    tid = await mdb.add_maintenance_task(
        acct.id, "OSY", "231", "oil_change", "never touched",
    )
    rows = await mdb.get_maintenance_tasks(acct.id, open_only=True)
    assert tid in {r["id"] for r in rows}


# ── The callers ───────────────────────────────────────────────────

class _RecordingDB:
    """Remembers whether it was asked for open work or for everything."""

    def __init__(self):
        self.calls: list[dict] = []

    async def get_maintenance_tasks(self, account_id, status=None,
                                    vehicle_name=None, open_only=False):
        self.calls.append({"vehicle_name": vehicle_name,
                           "open_only": open_only})
        return []

    async def list_vehicles(self, account_id, **kw):
        return []

    async def get_vehicle_state(self, account_id, **kw):
        return []


@pytest.fixture
def _account_today(monkeypatch):
    import features.maintenance.service as svc

    async def _today(account_id):
        from datetime import date
        return date(2026, 9, 13)

    monkeypatch.setattr(svc, "account_today", _today)


@pytest.mark.asyncio
async def test_the_account_summary_asks_for_open_work(_account_today):
    from features.maintenance.ai_tool import get_maintenance_summary
    db = _RecordingDB()
    await get_maintenance_summary({}, None, account_id=7, db=db)
    assert db.calls and db.calls[0]["open_only"] is True


@pytest.mark.asyncio
async def test_the_per_vehicle_tool_asks_for_open_work(_account_today,
                                                       monkeypatch):
    import features.maintenance.ai_tool as mod

    async def _resolve(db, account_id, tool_args):
        return None, None

    monkeypatch.setattr(mod, "resolve_for_tool", _resolve)
    db = _RecordingDB()
    await mod.get_vehicle_maintenance(
        {"vehicle_name": "231"}, None, account_id=7, db=db)
    assert db.calls and db.calls[0]["open_only"] is True
    assert db.calls[0]["vehicle_name"] == "231"
