"""Authoritative "which alerts are still active" check (get_active_alert_
history_by_ids) — the signal the live-banner watcher retires sticky
banners by, WITHOUT the capped-feed false-positive.

Pins: active rows come back, cleared rows don't, foreign-account ids are
excluded, and a long-running row (old first_seen) is still reported active
(no time window) so a days-old critical never falsely reads as resolved.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio
import pytest


@pytest_asyncio.fixture
async def db(pg_db):
    acct = await pg_db.create_account("Active-Among Co")
    yield pg_db, acct.id


async def _fire(database, account_id, vehicle_id, atype="fuel", sev="critical"):
    row = await database.upsert_alert_history(
        account_id, atype, vehicle_id, f"veh-{vehicle_id}",
        last_detail="low", severity=sev)
    return row["id"]


class TestActiveAmong:
    @pytest.mark.asyncio
    async def test_returns_only_active_ids(self, db):
        database, acct = db
        a = await _fire(database, acct, "v1")
        b = await _fire(database, acct, "v2")
        c = await _fire(database, acct, "v3")
        # Clear b (acknowledge → status != 'active').
        await database.clear_alert_history(acct, "fuel", "v2")

        rows = await database.get_active_alert_history_by_ids(acct, [a, b, c])
        got = {r["id"] for r in rows}
        assert a in got and c in got        # still active
        assert b not in got                 # cleared → not active

    @pytest.mark.asyncio
    async def test_excludes_other_accounts(self, db):
        database, acct = db
        other = (await database.create_account("Other Co")).id
        mine = await _fire(database, acct, "v1")
        theirs = await _fire(database, other, "v9")
        rows = await database.get_active_alert_history_by_ids(
            acct, [mine, theirs])
        got = {r["id"] for r in rows}
        assert got == {mine}                # never leaks another account's id

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty(self, db):
        database, acct = db
        assert await database.get_active_alert_history_by_ids(acct, []) == []

    @pytest.mark.asyncio
    async def test_old_row_still_reported_active(self, db):
        """A long-running alert (first_seen far in the past) is STILL active
        — the whole point vs the 7-day feed window."""
        database, acct = db
        a = await _fire(database, acct, "v1")
        await database._db.execute(
            "UPDATE alert_history SET first_seen = '2001-01-01T00:00:00', "
            "last_seen = '2001-01-01T00:00:00' WHERE id = ?", (a,))
        await database._db.commit()
        rows = await database.get_active_alert_history_by_ids(acct, [a])
        assert {r["id"] for r in rows} == {a}   # no time window → still active


# ── Router: GET /api/alerts/active-among ─────────────────────────────

import sys as _sys

from httpx import ASGITransport, AsyncClient  # noqa: E402
from adapters.storage import Role  # noqa: E402
from interfaces.api.auth import create_jwt  # noqa: E402


@pytest_asyncio.fixture
async def api(pg_db):
    acct = await pg_db.create_account("Among API Co")
    await pg_db.add_company(acct.id, "CO", "key_co", "Among Co")
    owner = await pg_db.create_user(7401, acct.id, role=Role.OWNER)
    driver = await pg_db.create_user(7402, acct.id, role=Role.DRIVER)
    await pg_db.assign_vehicle(user_id=driver.id, account_id=acct.id,
                               truck_num="T1", assigned_by=0, is_primary=True)
    # a1 on the driver's truck, a2 on another truck, a3 cleared.
    a1 = (await pg_db.upsert_alert_history(acct.id, "fault", "V1", "T1",
                                           severity="critical"))["id"]
    a2 = (await pg_db.upsert_alert_history(acct.id, "fault", "V2", "T2",
                                           severity="critical"))["id"]
    a3 = (await pg_db.upsert_alert_history(acct.id, "fault", "V3", "T3"))["id"]
    await pg_db.clear_alert_history(acct.id, "fault", "V3")

    import infra.platform as _cp
    old = _cp._db
    _cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    yield {"app": app, "acct": acct.id, "a1": a1, "a2": a2, "a3": a3,
           "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
           "driver": create_jwt(driver.telegram_id, acct.id, "driver")}
    _cp._db = old


def _hh(token):
    return {"Authorization": f"Bearer {token}"}


class TestActiveAmongRoute:
    async def test_owner_gets_active_subset(self, api):
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get(
                f"/api/alerts/active-among?ids={api['a1']},{api['a2']},{api['a3']}",
                headers={**_hh(api["owner"]), "X-View-As": "fleet"})
        assert r.status_code == 200
        got = set(r.json()["active_ids"])
        assert str(api["a1"]) in got and str(api["a2"]) in got  # active
        assert str(api["a3"]) not in got                        # cleared

    async def test_driver_scoped_to_own_truck(self, api):
        """A driver confirms only their OWN truck's alert — active-among
        can't become an oracle for another truck's alert id."""
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get(
                f"/api/alerts/active-among?ids={api['a1']},{api['a2']}",
                headers=_hh(api["driver"]))
        assert r.status_code == 200
        assert r.json()["active_ids"] == [str(api["a1"])]       # a2 (T2) hidden

    async def test_malformed_ids_never_crash(self, api):
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get(
                "/api/alerts/active-among?ids=--5,abc,,-1,999999999999999999999",
                headers={**_hh(api["owner"]), "X-View-As": "fleet"})
        assert r.status_code == 200                              # no 500
        assert r.json()["active_ids"] == []


class TestAlertByIdRoute:
    """GET /alerts/by-id/{id} — what the bell's ?alertId= deep link opens.

    Must reach an alert the BOARD can't show (outside its window, or already
    acknowledged) — that's the whole point — while never leaking one outside
    the caller's scope.
    """

    async def test_returns_an_alert_outside_any_window(self, api):
        db = api["app"]  # noqa: F841  (fixture wires infra.platform)
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get(f"/api/alerts/by-id/{api['a1']}",
                            headers={**_hh(api["owner"]), "X-View-As": "fleet"})
        assert r.status_code == 200
        assert str(r.json()["alert"]["id"]) == str(api["a1"])

    async def test_returns_a_cleared_alert_too(self, api):
        """A link to an alert a colleague just handled shows what happened
        rather than a dead end."""
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get(f"/api/alerts/by-id/{api['a3']}",
                            headers={**_hh(api["owner"]), "X-View-As": "fleet"})
        assert r.status_code == 200

    async def test_driver_cannot_read_another_trucks_alert(self, api):
        """Out-of-scope is a 404, identical to not-found — an id probe can't
        tell the two apart."""
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            own = await c.get(f"/api/alerts/by-id/{api['a1']}",
                              headers=_hh(api["driver"]))
            other = await c.get(f"/api/alerts/by-id/{api['a2']}",
                                headers=_hh(api["driver"]))
        assert own.status_code == 200        # their own truck (T1)
        assert other.status_code == 404      # T2 — not theirs

    async def test_unknown_id_is_404(self, api):
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get("/api/alerts/by-id/99999999",
                            headers={**_hh(api["owner"]), "X-View-As": "fleet"})
        assert r.status_code == 404


class TestTheBannerLearnsWhoHasIt:
    """``claimed_by`` — the stand-down signal the sticky banner lacked.

    A claim deliberately does not change status, so "still active?" alone
    left the pager's own face demanding an owner that had already been
    found: the board showed chips and the Telegram copy named the person,
    while the banner kept its filled "Work on it" button.
    """

    async def _ask(self, api, token, ids):
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.get(
                "/api/alerts/active-among?ids=" + ",".join(str(i) for i in ids),
                headers=_hh(token))
            assert r.status_code == 200, r.text
            return r.json()

    async def test_a_claim_is_reported_without_resolving_anything(self, api, pg_db):
        aid = int(api["a1"])
        before = await self._ask(api, api["owner"], [aid])
        assert str(aid) in before["active_ids"]
        assert before["claimed_by"] == {}                # nobody yet

        claimer = await pg_db.create_user(7410, api["acct"],
                                          display_name="Allen Klein")
        await pg_db.claim_alert(api["acct"], claimer.id, aid)

        after = await self._ask(api, api["owner"], [aid])
        # STILL ACTIVE — a claim is an owner, not a resolution.  The
        # banner must not vanish; it must stop demanding.
        assert str(aid) in after["active_ids"]
        assert after["claimed_by"][str(aid)] == "Allen Klein"

    async def test_several_hands_read_as_a_name_plus_a_count(self, api, pg_db):
        aid = int(api["a2"])
        a = await pg_db.create_user(7411, api["acct"], display_name="Allen Klein")
        b = await pg_db.create_user(7412, api["acct"], display_name="Sean Alex")
        await pg_db.claim_alert(api["acct"], a.id, aid)
        await pg_db.claim_alert(api["acct"], b.id, aid)

        out = await self._ask(api, api["owner"], [aid])
        assert out["claimed_by"][str(aid)] == "Allen Klein +1"

    async def test_a_name_never_travels_with_an_invisible_alert(self, api, pg_db):
        """The disclosure guard: names are derived only from the already
        scope-filtered set, so an alert the caller cannot see contributes
        neither an id nor a claimant.  The driver here is scoped to truck
        T1, so a2 (truck T2) is invisible to them — even though it IS
        claimed and IS active."""
        a2 = int(api["a2"])
        someone = await pg_db.create_user(7413, api["acct"],
                                          display_name="Not Visible")
        await pg_db.claim_alert(api["acct"], someone.id, a2)

        out = await self._ask(api, api["driver"], [a2])
        assert out["active_ids"] == []
        assert out["claimed_by"] == {}

    async def test_a_resolved_alert_still_retires_the_banner(self, api, pg_db):
        """The original behaviour must survive: claims are additive information,
        not a replacement for the active/resolved signal."""
        a3 = int(api["a3"])
        out = await self._ask(api, api["owner"], [a3])
        assert out["active_ids"] == []
