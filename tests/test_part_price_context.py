"""Own-history price context — "is this price normal?" without needing
three consenting fleets.

Market ranges are structurally unavailable until several accounts share
overlapping parts data; this answers the same question from data every
account already has.  Pins the rules that keep it honest: a single
prior purchase is not a range, void work orders don't count, the
window is real, and it never crosses accounts.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from adapters.storage.parts_catalog import _percentile, part_name_key
from interfaces.api.auth import create_jwt


def test_percentile_matches_the_market_rollup_shape():
    """Own-history and market bands must be computed the same way, or
    showing them side by side compares two different maths."""
    v = [10.0, 20.0, 30.0, 40.0]
    assert _percentile(v, 0.25) == 17.5
    assert _percentile(v, 0.75) == 32.5
    assert _percentile([5.0], 0.25) == 5.0
    assert _percentile([], 0.5) == 0.0


@pytest_asyncio.fixture
async def acct(db):
    return (await db.create_account("Price Ctx Co")).id


async def _buy(db, acct, name, unit, *, date="2026-07-01",
               vendor="Shop A", status="completed", payment="unpaid"):
    wo = await db.add_work_order(acct, "", "234", vendor, service_date=date,
                                 status=status, payment_status=payment)
    part = await db.resolve_or_create_part(acct, name)
    await db.add_work_order_part(
        wo, part_name=name, part_id=part["id"], quantity=1, unit_cost=unit,
        total_cost=unit,
    )
    return wo


@pytest.mark.asyncio
async def test_band_from_own_purchases(db, acct):
    for price in (90, 95, 100, 110):
        await _buy(db, acct, "Brake Pad Set", price)
    ctx = await db.part_price_context(acct, ["Brake Pad Set"])
    c = ctx[part_name_key("Brake Pad Set")]
    assert c["buys"] == 4
    assert c["low"] == 93.75 and c["high"] == 102.5     # p25 / p75
    assert c["cheapest_price"] == 90


@pytest.mark.asyncio
async def test_one_purchase_is_not_a_range(db, acct):
    """One prior data point is a price, not a band — flagging against
    it would be noise, so the part is simply absent."""
    await _buy(db, acct, "Rare Widget", 500)
    assert await db.part_price_context(acct, ["Rare Widget"]) == {}


@pytest.mark.asyncio
async def test_lookup_is_name_normalized(db, acct):
    for price in (10, 20):
        await _buy(db, acct, "Oil Filter", price)
    ctx = await db.part_price_context(acct, ["  oil   FILTER "])
    assert part_name_key("Oil Filter") in ctx


@pytest.mark.asyncio
async def test_void_work_orders_are_excluded(db, acct):
    for price in (90, 95, 100):
        await _buy(db, acct, "Air Line", price)
    # A written-off invoice is not evidence of a normal price.
    await _buy(db, acct, "Air Line", 9999, payment="void")
    c = (await db.part_price_context(acct, ["Air Line"]))[part_name_key("Air Line")]
    assert c["buys"] == 3 and c["high"] < 200


@pytest.mark.asyncio
async def test_window_excludes_old_purchases(db, acct):
    for price in (90, 95):
        await _buy(db, acct, "Belt", price, date="2026-07-01")
    await _buy(db, acct, "Belt", 5, date="2019-01-01")     # ancient
    c = (await db.part_price_context(acct, ["Belt"], months=12))[part_name_key("Belt")]
    assert c["buys"] == 2 and c["cheapest_price"] == 90


@pytest.mark.asyncio
async def test_never_crosses_accounts(db, acct):
    other = (await db.create_account("Someone Else")).id
    for price in (10, 12):
        await _buy(db, other, "Shared Name Part", price)
    assert await db.part_price_context(acct, ["Shared Name Part"]) == {}


@pytest.mark.asyncio
async def test_last_price_is_the_most_recent(db, acct):
    await _buy(db, acct, "Coolant", 40, date="2026-01-01")
    await _buy(db, acct, "Coolant", 60, date="2026-07-01")
    c = (await db.part_price_context(acct, ["Coolant"]))[part_name_key("Coolant")]
    assert c["last_price"] == 60


@pytest.mark.asyncio
async def test_blank_and_unknown_names_are_safe(db, acct):
    assert await db.part_price_context(acct, []) == {}
    assert await db.part_price_context(acct, ["   "]) == {}
    assert await db.part_price_context(acct, ["Never Bought"]) == {}


# ── API ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def api(db):
    import infra.platform as cp
    cp._db = db
    a = await db.create_account("Price API Co")
    owner = await db.create_user(960001, a.id, role=Role.OWNER)
    driver = await db.create_user(960002, a.id, role=Role.DRIVER)
    from interfaces.api.app import create_api
    return {"app": create_api(), "acct": a.id,
            "owner": create_jwt(owner.telegram_id, a.id, "owner"),
            "driver": create_jwt(driver.telegram_id, a.id, "driver")}


@pytest.mark.asyncio
async def test_api_price_context(api, db):
    a = api["acct"]
    for price in (90, 110):
        await _buy(db, a, "Brake Pad Set", price)
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        # The literal path must not be shadowed by /{part_id}.
        r = await c.post("/api/parts/price-context",
                         headers={"Authorization": f"Bearer {api['owner']}"},
                         json={"names": ["Brake Pad Set", "Unknown Thing"]})
        assert r.status_code == 200, r.text
        ctx = r.json()["context"]
        assert part_name_key("Brake Pad Set") in ctx
        assert part_name_key("Unknown Thing") not in ctx

        r = await c.post("/api/parts/price-context",
                         headers={"Authorization": f"Bearer {api['driver']}"},
                         json={"names": ["Brake Pad Set"]})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_ai_proposal_flags_an_overpriced_line(db, acct):
    """Door B: the approval summary says so, because the user is
    approving the amounts."""
    from features.work_orders.ai_actions import create_work_order_action
    for price in (90, 95, 100):
        await _buy(db, acct, "Brake Pad Set", price)

    out = await create_work_order_action({
        "vehicle_name": "234",
        "parts": [{"name": "Brake Pad Set", "quantity": 1,
                   "unit_cost": 300, "total": 300}],
    }, None, acct, db)
    summary = out["artifacts"][0]["summary"]
    assert "above what you usually pay" in summary
    assert "Brake Pad Set" in summary


@pytest.mark.asyncio
async def test_ai_proposal_quiet_when_price_is_normal(db, acct):
    from features.work_orders.ai_actions import create_work_order_action
    for price in (90, 95, 100):
        await _buy(db, acct, "Brake Pad Set", price)
    out = await create_work_order_action({
        "vehicle_name": "234",
        "parts": [{"name": "Brake Pad Set", "quantity": 1,
                   "unit_cost": 95, "total": 95}],
    }, None, acct, db)
    assert "above what you usually pay" not in out["artifacts"][0]["summary"]


# ── Company isolation ───────────────────────────────────────────────
#
# An account is NOT one company.  A user assigned to Company A must not
# learn Company B's prices — and the response names the cheapest
# VENDOR, so a leak would disclose who the other company buys from.

async def _buy_for(db, acct, company, name, unit, *, vendor="Shop A",
                   vehicle="234", date="2026-07-01"):
    wo = await db.add_work_order(acct, company, vehicle, vendor,
                                 service_date=date, status="completed")
    part = await db.resolve_or_create_part(acct, name)
    await db.add_work_order_part(
        wo, part_name=name, part_id=part["id"], quantity=1,
        unit_cost=unit, total_cost=unit,
    )


@pytest.mark.asyncio
async def test_company_scope_excludes_the_other_company(db, acct):
    for price in (90, 95):
        await _buy_for(db, acct, "AAA", "Brake Pad Set", price, vendor="A Shop")
    for price in (500, 600):
        await _buy_for(db, acct, "BBB", "Brake Pad Set", price, vendor="B Secret Shop")

    key = part_name_key("Brake Pad Set")
    a_only = await db.part_price_context(
        acct, ["Brake Pad Set"], company_codes=["AAA"])
    assert a_only[key]["buys"] == 2
    assert a_only[key]["high"] < 200
    # The other company's VENDOR must not surface either.
    assert a_only[key]["cheapest_vendor"] == "A Shop"

    # Unrestricted (owner) still sees the whole account.
    everything = await db.part_price_context(acct, ["Brake Pad Set"])
    assert everything[key]["buys"] == 4


@pytest.mark.asyncio
async def test_company_scope_is_case_insensitive(db, acct):
    for price in (90, 95):
        await _buy_for(db, acct, "AAA", "Filter X", price)
    got = await db.part_price_context(
        acct, ["Filter X"], company_codes=["aaa"])
    assert got[part_name_key("Filter X")]["buys"] == 2


@pytest.mark.asyncio
async def test_empty_scope_fails_closed(db, acct):
    """A restriction that resolves to nothing means the caller sees
    NOTHING — never everything."""
    for price in (90, 95):
        await _buy_for(db, acct, "AAA", "Fail Closed Part", price)
    assert await db.part_price_context(
        acct, ["Fail Closed Part"], company_codes=[]) == {}
    assert await db.part_price_context(
        acct, ["Fail Closed Part"], vehicle_names=[]) == {}


@pytest.mark.asyncio
async def test_vehicle_scope_isolates_the_ai_path(db, acct):
    """The assistant carries its scope as vehicle names, so isolation
    rides that axis instead of company codes."""
    for price in (90, 95):
        await _buy_for(db, acct, "AAA", "Scoped Part", price, vehicle="234")
    for price in (700, 800):
        await _buy_for(db, acct, "BBB", "Scoped Part", price, vehicle="999")

    key = part_name_key("Scoped Part")
    scoped = await db.part_price_context(
        acct, ["Scoped Part"], vehicle_names=["234"])
    assert scoped[key]["buys"] == 2 and scoped[key]["high"] < 200
    # Case-insensitive on vehicle names too.
    assert await db.part_price_context(
        acct, ["Scoped Part"], vehicle_names=["  234 "]) == scoped


@pytest.mark.asyncio
async def test_ai_proposal_respects_vehicle_scope(db, acct):
    """Door B must not flag using another company's prices."""
    from features.work_orders.ai_actions import create_work_order_action
    for price in (700, 800):
        await _buy_for(db, acct, "BBB", "Cross Part", price, vehicle="999")

    out = await create_work_order_action({
        "vehicle_name": "234",
        "parts": [{"name": "Cross Part", "quantity": 1,
                   "unit_cost": 100, "total": 100}],
        # Scoped to truck 234 — truck 999's history is off-limits.
        "_scope_vehicles": ["234"],
    }, None, acct, db)
    assert "usually pay" not in out["artifacts"][0]["summary"]


@pytest.mark.asyncio
async def test_part_profile_is_company_scoped(db, acct):
    """The part profile carries per-VENDOR prices and dated purchases —
    an unfiltered read would tell a Company A user what Company B pays
    and to whom."""
    for price in (90, 95):
        await _buy_for(db, acct, "AAA", "Profile Part", price, vendor="A Shop")
    for price in (500, 600):
        await _buy_for(db, acct, "BBB", "Profile Part", price, vendor="B Secret Shop")
    part = await db.resolve_or_create_part(acct, "Profile Part")

    scoped = await db.part_analytics(part["id"], acct, company_codes=["AAA"])
    vendors = {v["vendor_name"] for v in scoped["by_vendor"]}
    assert vendors == {"A Shop"}                     # B's shop invisible
    assert len(scoped["purchases"]) == 2
    assert all(p["effective_unit_price"] < 200 for p in scoped["purchases"])

    # Unrestricted (owner) still sees the whole account.
    everything = await db.part_analytics(part["id"], acct)
    assert len(everything["purchases"]) == 4

    # Empty scope fails closed — an empty profile, never everything.
    closed = await db.part_analytics(part["id"], acct, company_codes=[])
    assert closed["purchases"] == [] and closed["by_vendor"] == []
    assert closed["name"] == part["name"]            # identity still resolves
