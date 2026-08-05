"""The company wall, in the two features that never had one.

Team Management owns two axes — which COMPANIES a user may see, and
which VEHICLES.  The company axis is meant to be universal, and an audit
of every feature router found three that applied it nowhere: cameras,
inspections and driver_pay.

driver_pay turned out to be self-scoped (``can_driver_pay_view_own`` for
drivers; ``can_driver_pay_admin`` is held by no company-restricted role),
so it needs no wall.  The other two did:

* ``camera_checks`` carries no company column and 24k rows spanned 6
  companies, so a company-restricted fleet or safety user could read
  every company's dashcam frames.  It went unnoticed only because the
  company-restricted users today are a dispatcher and two drivers, and
  none of them holds ``can_cameras``.
* ``driver_inspections`` likewise carries no company column, and the one
  company-restricted dispatcher DOES hold ``can_inspections_all``.
"""

from __future__ import annotations

import pytest

from interfaces.api.deps import filter_by_company_map


class TestCameraCompanyWall:
    """Exercises the ROUTER's ``_company_scoped``, not the primitive.

    An earlier version of these tests called ``filter_by_company_map``
    directly with hand-built rows — which would have passed unchanged if
    the wall were deleted from the routes entirely.  A test that survives
    the removal of the thing it guards is not a guard.
    """

    CHECKS = [
        {"id": 1, "vehicle_id": "v-a", "vehicle_name": "107"},
        {"id": 2, "vehicle_id": "v-b", "vehicle_name": "220"},
        {"id": 3, "vehicle_id": "v-ghost", "vehicle_name": "999"},
    ]

    @staticmethod
    def _patch(monkeypatch, companies, veh_map={"v-a": "AAA", "v-b": "BBB"}):
        from features.cameras import router as cr

        async def _codes(user):
            return companies

        async def _map(account_id, tenant_db):
            return veh_map

        monkeypatch.setattr(cr, "get_user_company_codes", _codes)
        monkeypatch.setattr(cr, "vehicle_company_map", _map)
        return cr

    @pytest.mark.asyncio
    async def test_restricted_caller_sees_only_their_company(self, monkeypatch):
        cr = self._patch(monkeypatch, ["AAA"])
        got = await cr._company_scoped(self.CHECKS, {"account_id": 1}, None)
        assert [c["id"] for c in got] == [1, 3]   # 3 unresolved -> kept

    @pytest.mark.asyncio
    async def test_unrestricted_caller_sees_everything(self, monkeypatch):
        cr = self._patch(monkeypatch, [])
        got = await cr._company_scoped(self.CHECKS, {"account_id": 1}, None)
        assert len(got) == 3

    @pytest.mark.asyncio
    async def test_the_image_route_agrees_with_the_list(self, monkeypatch):
        """A check hidden from the list must not be fetchable by its id —
        the id is sequential, and the image route returns the frame
        itself, so a disagreement here is the actual disclosure."""
        cr = self._patch(monkeypatch, ["AAA"])
        listed = await cr._company_scoped(self.CHECKS, {"account_id": 1}, None)
        hidden = [c for c in self.CHECKS if c not in listed]
        assert hidden, "fixture must hide at least one check"
        for c in hidden:
            assert not await cr._company_scoped([c], {"account_id": 1}, None)

    @pytest.mark.asyncio
    async def test_cold_map_fails_OPEN_not_closed(self, monkeypatch):
        """A read failure yields {} — showing everything beats hiding a
        fleet's whole camera history behind a transient outage."""
        cr = self._patch(monkeypatch, ["AAA"], veh_map={})
        got = await cr._company_scoped(self.CHECKS, {"account_id": 1}, None)
        assert len(got) == 3

    @pytest.mark.asyncio
    async def test_keyed_on_id_because_names_collide_across_companies(
        self, monkeypatch,
    ):
        """Truck "103" can exist in two companies; the id cannot."""
        cr = self._patch(monkeypatch, ["AAA"])
        rows = [{"vehicle_id": "v-a", "vehicle_name": "103"},
                {"vehicle_id": "v-b", "vehicle_name": "103"}]
        got = await cr._company_scoped(rows, {"account_id": 1}, None)
        assert [r["vehicle_id"] for r in got] == ["v-a"]


class TestInspectionCompanyWall:
    """Inspections resolve company through the DRIVER, and the wall goes
    into the query — the endpoint is server-paginated, so filtering the
    returned page would leave ``total`` counting rows the caller may not
    see."""

    @pytest.mark.asyncio
    async def test_empty_allow_list_denies_all(self, pg_db):
        """`[]` (restricted, but no driver in my companies) must return
        nothing.  It is a DIFFERENT answer from None (unrestricted), and
        `[]` is falsy — so a truthiness check here would be a silent grant.

        The account MUST hold rows for this to mean anything: an earlier
        version asserted against an empty account, so it passed whether
        the wall denied or was skipped entirely.
        """
        acct = 90211
        await pg_db.create_inspection(acct, 601, "C1")
        assert (await pg_db.list_inspections_for_account(acct))["total"] == 1

        out = await pg_db.list_inspections_for_account(acct, only_user_ids=[])
        assert out["total"] == 0, "[] must DENY, not fall through to unrestricted"
        assert out["items"] == []

    @pytest.mark.asyncio
    async def test_none_is_unrestricted(self, pg_db):
        acct = 90212
        await pg_db.create_inspection(acct, 701, "D1")
        out = await pg_db.list_inspections_for_account(acct, only_user_ids=None)
        assert out["total"] == 1

    @pytest.mark.asyncio
    async def test_the_wall_runs_in_SQL_so_total_matches_the_page(self, pg_db):
        """The endpoint is server-paginated, so the wall has to be in the
        query: filtering the returned page would leave ``total`` counting
        rows the caller may not see, and the page numbers pointing at
        gaps.  This seeds two drivers and asserts BOTH halves narrow."""
        acct = 90210
        for uid, veh in ((501, "A1"), (501, "A2"), (502, "B1")):
            await pg_db.create_inspection(acct, uid, veh)

        everyone = await pg_db.list_inspections_for_account(acct)
        assert everyone["total"] == 3

        mine = await pg_db.list_inspections_for_account(
            acct, only_user_ids=[501],
        )
        assert mine["total"] == 2, "COUNT must honour the wall"
        assert len(mine["items"]) == 2, "the page must honour it too"
        assert {i["user_id"] for i in mine["items"]} == {501}

        # Multi-id lists exercise the placeholder expansion.
        both = await pg_db.list_inspections_for_account(
            acct, only_user_ids=[501, 502],
        )
        assert both["total"] == 3
