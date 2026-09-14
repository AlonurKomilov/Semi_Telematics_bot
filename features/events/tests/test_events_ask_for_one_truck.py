"""A question about one truck stops reading the whole account.

``get_vehicle_events`` pulled every safety event in the account's
window — up to 30 days, every truck, each row decoding its own raw
Samsara blob — and then kept one truck's in Python.

The narrowing is deliberately a SUPERSET of what the identity ladder
admits, not a replacement for it: a row that carries a provider id is
settled in SQL, but a row WITHOUT one falls to the unit name, and that
decision cannot be made in the query.  Dropping those rows would
answer a safety question with silence, which reads as a clean week —
the exact failure test_ai_events_identity.py already guards.
"""

from __future__ import annotations

import pytest

from features.events.ai_tool import get_vehicle_events


class _V:
    def __init__(self, vid, ref, unit, company=""):
        self.id, self.telematics_ref = vid, ref
        self.unit_number, self.company_code = unit, company
        self.is_active = True


class _DB:
    def __init__(self, rows):
        self._rows = rows

    async def list_vehicles(self, account_id, **kw):
        return list(self._rows)


def _ev(eid, vehicle_id, name):
    return {"event_id": eid, "vehicle_id": vehicle_id, "vehicle_name": name,
            "event_name": "Harsh Brake", "driver_name": "D",
            "time": "2026-09-01", "g_force": 1.2}


@pytest.fixture
def _recording(monkeypatch):
    """Record what the tool asked the store for."""
    import features.events.ai_tool as mod
    asked: dict = {}

    def _install(rows):
        async def _svc(account_id, days=7, company=None, vehicle_id=None):
            asked["vehicle_id"] = vehicle_id
            asked["days"] = days
            return list(rows)
        monkeypatch.setattr(mod, "_svc_events", _svc, raising=False)
        return asked

    return _install


@pytest.mark.asyncio
async def test_the_trucks_provider_id_reaches_the_store(_recording):
    asked = _recording([_ev(1, "sam_42", "231")])
    out = await get_vehicle_events(
        {"vehicle_name": "231", "days": 14}, None,
        account_id=1, db=_DB([_V(42, "sam_42", "231")]))

    assert asked["vehicle_id"] == "sam_42"
    assert asked["days"] == 14
    assert out["total_events"] == 1


@pytest.mark.asyncio
async def test_an_unresolvable_truck_still_reads_account_wide(_recording):
    """Retired, unregistered or mistyped — the name path needs the
    account's rows, so nothing is narrowed away from it."""
    asked = _recording([_ev(1, "", "231")])
    out = await get_vehicle_events(
        {"vehicle_name": "231", "days": 7}, None,
        account_id=1, db=_DB([]))

    assert asked["vehicle_id"] is None
    assert out["total_events"] == 1


@pytest.mark.asyncio
async def test_a_truck_the_registry_knows_without_a_provider_id(_recording):
    """A manually-created truck has no telematics ref — there is
    nothing to narrow ON, so the read stays account-wide."""
    asked = _recording([_ev(1, "", "231")])
    await get_vehicle_events(
        {"vehicle_name": "231", "days": 7}, None,
        account_id=1, db=_DB([_V(42, "", "231")]))

    assert asked["vehicle_id"] is None


@pytest.mark.asyncio
async def test_the_renamed_truck_guard_still_holds(_recording):
    """The narrowing must not re-open the bug next door: a provider
    rename keeps the events, decided by id rather than by label."""
    _recording([_ev(1, "sam_42", "229 Idris Ahmed")])
    out = await get_vehicle_events(
        {"vehicle_name": "229", "days": 7}, None,
        account_id=1, db=_DB([_V(42, "sam_42", "229")]))

    assert out["total_events"] == 1


# ── The predicate itself, against the real database ───────────────

@pytest.mark.asyncio
async def test_the_store_keeps_rows_no_provider_id_can_claim(pg_db):
    """The superset property, in SQL: narrowing to one truck must keep
    the rows whose provider id is empty, because the caller's ladder
    decides those by name."""
    acct = await pg_db.create_account("Events Co")
    await pg_db.insert_safety_events(acct.id, [
        {"samsara_event_id": "e1", "vehicle_id": "sam_42",
         "vehicle_name": "231", "occurred_at": "2099-01-01T00:00:00+00:00"},
        {"samsara_event_id": "e2", "vehicle_id": "sam_99",
         "vehicle_name": "104", "occurred_at": "2099-01-01T00:00:00+00:00"},
        {"samsara_event_id": "e3", "vehicle_id": "",
         "vehicle_name": "231", "occurred_at": "2099-01-01T00:00:00+00:00"},
    ])

    narrowed = await pg_db.get_safety_events_warehouse(
        acct.id, days=3650, vehicle_id="sam_42", include_unidentified=True)
    assert {r["samsara_event_id"] for r in narrowed} == {"e1", "e3"}

    # The strict form every other caller uses is unchanged.
    strict = await pg_db.get_safety_events_warehouse(
        acct.id, days=3650, vehicle_id="sam_42")
    assert {r["samsara_event_id"] for r in strict} == {"e1"}
