"""The quantity we bill for is ours to compute, not the payer's to state.

``POST /billing/update-vehicles`` used to take a ``vehicle_count`` in the
request body and write it straight to the subscription. Anyone holding
``can_manage_billing`` — owner, admin and accounting, by default — could
therefore set their own billed fleet to zero and pay nothing, leaving no
trace: subscription writes are not audited.

The endpoint's docstring claimed the scheduler called it after a Samsara
sync. Nothing called it. A probe against production on 2026-09-08 reached
it and got 200.

These tests hold the line at the seam that matters: whatever the caller
sends, the number that lands in the subscription is the one WE computed.
"""

from __future__ import annotations

import pytest

from capabilities.platform.billing import router as billing_router


class _FakeDB:
    """Records what the endpoint writes, and what it was asked to count.

    Only the registry's billable count exists here on purpose: the
    endpoint must have no other source to fall back on.
    """

    def __init__(self, billable=None, fail=False):
        self._billable = billable
        self._fail = fail
        self.written: list[dict] = []
        self.asked: list[int] = []

    async def count_billable_vehicles(self, account_id):
        self.asked.append(account_id)
        if self._fail:
            raise RuntimeError("registry unavailable")
        return self._billable

    async def update_subscription(self, account_id, **fields):
        self.written.append({"account_id": account_id, **fields})


USER = {"account_id": 10000001, "role": "owner", "user_id": 1}


# ── the security property ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_caller_cannot_choose_the_billed_quantity():
    """The whole point: a zero from the caller must not become a zero bill."""
    db = _FakeDB(billable=189)

    result = await billing_router.update_vehicle_count(user=USER, platform_db=db)

    assert result == {"ok": True, "vehicle_count": 189}
    assert db.written == [{"account_id": 10000001, "vehicle_count": 189}]


@pytest.mark.asyncio
async def test_the_endpoint_takes_no_request_body_at_all():
    """A body cannot be trusted, so the signature must not accept one.

    Guards against a well-meaning re-introduction of ``VehicleCountRequest``:
    the parameter list is the contract, and a body parameter here is the
    bug returning.
    """
    import inspect

    params = inspect.signature(billing_router.update_vehicle_count).parameters
    assert set(params) == {"user", "platform_db"}, (
        f"unexpected parameters: {list(params)}")
    assert not hasattr(billing_router, "VehicleCountRequest"), (
        "the request model is back — the caller can state the quantity again")


# ── the count comes from the right source ─────────────────────────

@pytest.mark.asyncio
async def test_registry_is_the_source_of_truth():
    db = _FakeDB(billable=42)
    assert (await billing_router.update_vehicle_count(
        user=USER, platform_db=db))["vehicle_count"] == 42
    assert db.asked == [USER["account_id"]]


@pytest.mark.asyncio
async def test_an_empty_registry_is_zero_not_a_fallback():
    """The registry is the only source.  A telematics warehouse used to
    stand in when the registry had no rows; that made the bill a
    function of the provider again (a paused integration billed nothing
    three days later), so an account with no registered trucks is
    billed for none."""
    db = _FakeDB(billable=0)
    assert (await billing_router.update_vehicle_count(
        user=USER, platform_db=db))["vehicle_count"] == 0


# ── a failed read must not rewrite the number ─────────────────────

@pytest.mark.asyncio
async def test_a_registry_failure_leaves_the_stored_count_alone():
    """503 rather than a guess.

    Writing 0 because we could not read would hand the customer a free
    month; writing anything else would be invention. The stored value is
    the last one we could actually justify.
    """
    from fastapi import HTTPException

    db = _FakeDB(fail=True)
    with pytest.raises(HTTPException) as got:
        await billing_router.update_vehicle_count(user=USER, platform_db=db)

    assert got.value.status_code == 503
    assert db.written == [], "a failed read must not touch the subscription"


# ── the gate still reads the account's own matrix ─────────────────

def test_the_gate_is_the_delegatable_permission_not_a_hardcoded_role():
    """An owner may hand billing to another role tomorrow.

    ``require_permission`` resolves through ``get_user_permissions`` —
    the ACCOUNT's matrix plus the is_manager overlay — so a delegation
    made in the permissions page takes effect here with no code change.
    A hardcoded role list would silently ignore it.
    """
    import inspect

    src = inspect.getsource(billing_router)
    assert '_billing_admin = require_permission("can_manage_billing")' in src
    dep = inspect.signature(billing_router.update_vehicle_count).parameters["user"].default
    assert dep.dependency is billing_router._billing_admin
