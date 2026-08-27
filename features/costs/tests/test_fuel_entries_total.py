"""The fuel-entries endpoint states a total only when it can stand behind it.

``/costs/fuel`` returns the newest ``limit`` entries (50 by default).  For a
30-truck fleet that is roughly two days of fill-ups, and the dashboard was
pivoting them into "fuel spend by truck x month" — a cross-tab, so no rows
were on screen to notice the shortfall by, and no control existed to widen.

The grid can refuse that report, but only if the page declares the slice,
and the page can only declare it if the server says how many entries exist.
Hence ``total``.

The subtlety worth pinning: the two permission filters run in PYTHON, after
the SQL limit.  An account-wide aggregate therefore describes more than a
scoped caller can reach, so ``total`` is OMITTED for them rather than
approximated.  A consumer reads a missing total as "no claim"; a wrong one
it reads as truth.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    database = pg_db
    acct = await database.create_account("Fuel Co")
    await database.add_company(acct.id, "FC", "key_fc", "Fuel Co")
    owner = await database.create_user(8201, acct.id, role=Role.OWNER)
    driver = await database.create_user(8202, acct.id, role=Role.DRIVER)

    # Three fill-ups is plenty: the rule under test is about which callers
    # get a total, not about the arithmetic of counting.
    for i, day in enumerate(("2026-08-01", "2026-08-02", "2026-08-03")):
        await database.add_fuel_entry(
            acct.id, vehicle_name="T-1", company_code="FC",
            gallons=100.0, price_per_gallon=4.0,
            odometer_miles=1000.0 + i, date=day,
        )

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = database
    from interfaces.api.app import create_api
    app = create_api()
    yield {
        "app": app,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }
    _cp._db = _old


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


@pytest.mark.asyncio
async def test_unrestricted_caller_gets_the_account_total(seeded):
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/costs/fuel", headers=_h(seeded["token_owner"]))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    # ``count`` keeps its old meaning — what came back — so the two are
    # never confused for each other.
    assert body["count"] == len(body["entries"])


@pytest.mark.asyncio
async def test_a_vehicle_filter_withdraws_the_claim(seeded):
    """``?vehicle=`` narrows the rows but not the aggregate, so the
    account-wide number would describe a different set than the one
    returned.  Omitted, not adjusted."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/costs/fuel?vehicle=T-1", headers=_h(seeded["token_owner"]))
    assert r.status_code == 200
    assert "total" not in r.json()


@pytest.mark.asyncio
async def test_a_scoped_caller_gets_no_total_at_all(seeded):
    """A driver is scoped to assigned trucks by a filter that runs after
    the query.  Whatever they can see, the account-wide count is not it —
    and a number too large is worse than no number, because the grid would
    disable a report while quoting a figure the driver can never reach."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/costs/fuel", headers=_h(seeded["token_driver"]))
    if r.status_code == 403:
        pytest.skip("driver lacks can_fuel_cost in this permission matrix")
    assert r.status_code == 200
    assert "total" not in r.json()
