"""An empty NARROWED read is a quiet truck, not a cold warehouse.

``readers.get_safety_events`` falls back to live Samsara when the
warehouse comes back empty — the cold-start guard.  That reasoning
only holds for an UNFILTERED read: with no filter, some truck in the
account almost always has an event in a 7-30 day window, so empty
really did mean the pipe had never run.

Once callers could narrow to one truck, empty stopped meaning that.
The commonest question there is "any safety events for truck 231?" and
the commonest honest answer is "none" — which would have re-fetched
the ENTIRE account from Samsara, live, on exactly the case the
narrowing was built to make cheap.

One COUNT tells the two apart, and it errs toward falling back: a
count we cannot take must never become a clean week.
"""

from __future__ import annotations

import pytest

import features.vehicles.warehouse.readers as readers


class _Tenant:
    def __init__(self, rows, count, count_raises=False):
        self._rows, self._count = rows, count
        self._count_raises = count_raises
        self.counted = 0

    async def get_safety_events_warehouse(self, account_id, **kw):
        return list(self._rows)

    async def count_safety_events_in_window(self, account_id, days=90):
        self.counted += 1
        if self._count_raises:
            raise RuntimeError("warehouse unreachable")
        return self._count


@pytest.fixture
def _wired(monkeypatch):
    monkeypatch.setattr(readers, "_enabled", lambda: True)
    calls = {"live": 0}

    async def _live():
        calls["live"] += 1
        return [{"event_id": "from-samsara"}]

    def _install(tenant):
        async def _get(account_id):
            return tenant
        monkeypatch.setattr(readers, "get_tenant_db", _get)
        return calls, _live

    return _install


@pytest.mark.asyncio
async def test_a_clean_truck_does_not_refetch_the_account(_wired):
    tenant = _Tenant(rows=[], count=57)          # warehouse is warm
    calls, live = _wired(tenant)

    out = await readers.get_safety_events(
        1, days=7, vehicle_id="sam_42", include_unidentified=True,
        samsara_fallback=live,
    )

    assert out == []
    assert calls["live"] == 0, "a quiet truck triggered a live account fetch"
    assert tenant.counted == 1


@pytest.mark.asyncio
async def test_a_cold_warehouse_still_falls_back(_wired):
    tenant = _Tenant(rows=[], count=0)           # nothing ingested yet
    calls, live = _wired(tenant)

    out = await readers.get_safety_events(
        1, days=7, vehicle_id="sam_42", include_unidentified=True,
        samsara_fallback=live,
    )

    assert calls["live"] == 1
    assert out == [{"event_id": "from-samsara"}]


@pytest.mark.asyncio
async def test_a_count_we_cannot_take_falls_back(_wired):
    """Never answer "clean" off a failed check."""
    tenant = _Tenant(rows=[], count=0, count_raises=True)
    calls, live = _wired(tenant)

    await readers.get_safety_events(
        1, days=7, vehicle_id="sam_42", samsara_fallback=live)
    assert calls["live"] == 1


@pytest.mark.asyncio
async def test_an_unfiltered_empty_read_is_unchanged(_wired):
    """No filter → empty still means cold, and costs no extra COUNT."""
    tenant = _Tenant(rows=[], count=57)
    calls, live = _wired(tenant)

    await readers.get_safety_events(1, days=7, samsara_fallback=live)

    assert calls["live"] == 1
    assert tenant.counted == 0


@pytest.mark.asyncio
async def test_rows_found_never_reach_the_count(_wired):
    tenant = _Tenant(rows=[{"event_id": "e1"}], count=0)
    calls, live = _wired(tenant)

    out = await readers.get_safety_events(
        1, days=7, vehicle_id="sam_42", samsara_fallback=live)

    assert out == [{"event_id": "e1"}]
    assert calls["live"] == 0 and tenant.counted == 0
