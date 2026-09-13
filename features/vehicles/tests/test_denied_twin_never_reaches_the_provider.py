"""Guard: a truck the caller is DENIED is never asked of the provider.

The leak this closes: the AI gate admits a vehicle-specific tool by
NAME, and a name cannot split same-numbered twins across companies. So a
caller scoped to OSY's "103" passed the gate while asking about "103 at
G1". The resolver narrowed to G1's row, the scope filter rejected it,
and `resolve_one` returned None — the same value a truck the registry
has never heard of returns. Callers read that as "the registry cannot
say" and fall back to asking the provider about the company the MODEL
named, so the answer came back describing the other company's truck.

These tests assert the thing that actually matters: the provider call
never happens. A unit test on the resolver alone would pass against a
version that still leaked, because the leak is in what the caller does
with None.
"""

import pytest

from features.vehicles.tests.test_resolve_twins import G1, OSY, _DB

PINNED = {
    "vehicle_name": "103",
    "company": "G1",
    "_scope_vehicles": ["103"],
    "_scope_identities": [[42, "sam-42", "103"]],   # OSY's 103, not G1's
}


@pytest.mark.asyncio
async def test_location_refuses_and_never_calls_the_provider(monkeypatch):
    import features.live_map.ai_tool as mod

    called = []

    async def _detail(account_id, name, company=None, **kw):
        called.append(company)
        return {"name": name, "location": {"city": "LEAKED"}}

    monkeypatch.setattr(mod, "_svc_detail", _detail, raising=False)
    res = await mod.get_vehicle_location(
        dict(PINNED), None, account_id=1, db=_DB([OSY, G1]))

    assert not called, (
        f"the provider was asked about company {called!r} for a truck the "
        "caller's vehicle access excludes"
    )
    assert res.get("error"), res


@pytest.mark.asyncio
async def test_faults_refuses_and_never_calls_the_provider(monkeypatch):
    import features.vehicles.faults.ai_tool as mod

    called = []

    async def _detail(account_id, name, company=None, **kw):
        called.append(company)
        return {"name": name, "fault_codes": {}}

    monkeypatch.setattr(mod, "_svc_detail", _detail, raising=False)
    res = await mod.get_vehicle_faults(
        dict(PINNED), None, account_id=1, db=_DB([OSY, G1]))

    assert not called, f"provider asked about {called!r} for a denied twin"
    assert res.get("error"), res


@pytest.mark.asyncio
async def test_the_caller_s_own_twin_still_resolves(monkeypatch):
    """The refusal must not cost the caller their OWN truck."""
    import features.live_map.ai_tool as mod

    called = []

    async def _detail(account_id, name, company=None, **kw):
        called.append(company)
        return {"name": name, "location": {"city": "Chicago"}}

    monkeypatch.setattr(mod, "_svc_detail", _detail, raising=False)
    args = {**PINNED, "company": "OSY"}
    res = await mod.get_vehicle_location(args, None, account_id=1, db=_DB([OSY, G1]))

    assert called == ["OSY"], called
    assert not res.get("error"), res
