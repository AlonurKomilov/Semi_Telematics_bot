"""Parking visibility scope — the axes the original isolation suite left open.

``test_parking_driver_isolation.py`` pins the DRIVER/TRUCK axis well, but
every fixture in it uses a single company ("PC"), so nothing there could
catch a missing company filter.  These tests cover what that leaves:

  1. UNASSIGNED DRIVER.  A ``can_parking_vehicle`` caller whose truck list
     is EMPTY must see nothing.  The list endpoints used to guard with
     ``if trucks:`` — falsy for an unassigned driver — so the filter was
     skipped entirely and they received the whole account.  An empty list
     is ordinary, not exotic: a newly-invited driver, or one whose
     assignment was just removed (``get_user_vehicle_nums`` returns []
     with no driver_trucks rows and no truck_num fallback).  The single-
     event routes denied correctly, so list and detail disagreed about
     the same rows.

  2. COMPANY SCOPE ON EVERY READ.  ``/stats/summary`` and
     ``/{id}/map-image`` applied the truck check but never the company
     filter, so a company-restricted caller was refused an event's
     DETAIL while the photo of that same stop was served, and their
     summary counted other companies' trucks.

  3. THE NEW ROUTE.  ``/vehicle/{id}/history`` inherits the shared
     predicate rather than growing a seventh copy of it.

All three now route through ``features.parking.service.is_visible`` —
one predicate for lists and single rows alike, which is what stops them
drifting apart again.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import order is LOAD-BEARING, not cosmetic.
#
# ``features.parking`` and ``capabilities.alerting`` are mutually
# recursive: the parking package's __init__ eagerly imports check /
# ai_vision / formatting, each of which imports capabilities.alerting,
# whose own __init__ imports back ``from features.parking import
# check_unsafe_parking``.  Entering from the parking side first therefore
# raises "partially initialized module".
#
# Production never trips it because the app imports capabilities.alerting
# earlier in boot, and the other parking tests go through
# ``interfaces.api.app`` which does the same.  A test that imports
# ``features.parking.service`` cold is the one caller that enters from the
# wrong side.
#
# Importing alerting first is a WORKAROUND, not the fix.  The fix is to
# stop the alerting package's __init__ re-exporting parking symbols (four
# forward edges into alerting exist from this package), which is a
# separate cleanup with a much wider blast radius than this change.
import capabilities.alerting  # noqa: F401  (must precede features.parking)

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def scope_app(pg_db):
    """Two companies, and a driver with NO truck assigned."""
    database = pg_db

    acct = await database.create_account("Scope Co")
    await database.add_company(acct.id, "AAA", "key_aaa", "Alpha Co")
    await database.add_company(acct.id, "BBB", "key_bbb", "Beta Co")

    owner = await database.create_user(12001, acct.id, role=Role.OWNER)
    # Driver with a truck, and a driver with NONE — the unassigned case.
    driver = await database.create_user(12002, acct.id, role=Role.DRIVER)
    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="500",
        assigned_by=0, is_primary=True,
    )
    unassigned = await database.create_user(12003, acct.id, role=Role.DRIVER)

    ev_aaa = await database.upsert_parking_event(
        account_id=acct.id, vehicle_id="v500",
        vehicle_name="500", company_code="AAA",
        latitude=40.0, longitude=-74.0, address="Alpha shoulder",
        first_stopped="2026-04-01T10:00:00",
        duration_hours=3.0, location_class="unsafe",
    )
    ev_bbb = await database.upsert_parking_event(
        account_id=acct.id, vehicle_id="v900",
        vehicle_name="900", company_code="BBB",
        latitude=41.0, longitude=-75.0, address="Beta shoulder",
        first_stopped="2026-04-01T11:00:00",
        duration_hours=4.0, location_class="unsafe",
    )

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = database

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
        "token_unassigned": create_jwt(unassigned.telegram_id, acct.id, "driver"),
        "ev_aaa": ev_aaa["id"], "ev_bbb": ev_bbb["id"],
    }

    _cp._db = _old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestUnassignedDriverSeesNothing:
    """Empty truck list must mean NOTHING, never everything."""

    async def test_active_returns_no_rows(self, scope_app):
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/active",
                            headers=_h(scope_app["token_unassigned"]))
        assert r.status_code == 200
        assert r.json()["events"] == [], (
            "an unassigned driver received other trucks' parking events — "
            "the list guard fell back to 'no filter' on an empty truck list"
        )

    async def test_vehicle_history_returns_no_rows(self, scope_app):
        """The empty-truck-list deny must reach the history route too.

        Distinct from ``test_vehicle_history_denies_other_truck``: that one
        has a truck and asks for the WRONG one, this one has NO trucks at
        all — the case where the old ``if trucks:`` guard skipped filtering
        entirely.
        """
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/vehicle/v500/history",
                            headers=_h(scope_app["token_unassigned"]))
        assert r.status_code == 200
        assert r.json()["events"] == []

    async def test_stats_counts_nothing(self, scope_app):
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/stats/summary",
                            headers=_h(scope_app["token_unassigned"]))
        assert r.status_code == 200
        assert r.json()["total_parked"] == 0

    async def test_assigned_driver_still_sees_own_truck(self, scope_app):
        """The deny must not be so broad it blanks a legitimate driver."""
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/active",
                            headers=_h(scope_app["token_driver"]))
        names = {e["vehicle_name"] for e in r.json()["events"]}
        assert names == {"500"}


class TestNewRoutesAreScoped:
    async def test_vehicle_history_denies_other_truck(self, scope_app):
        """Passing another truck's vehicle_id must not return its record."""
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/vehicle/v900/history",
                            headers=_h(scope_app["token_driver"]))
        assert r.status_code == 200
        assert r.json()["events"] == []

    async def test_vehicle_history_returns_own_truck(self, scope_app):
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/vehicle/v500/history",
                            headers=_h(scope_app["token_driver"]))
        assert r.status_code == 200
        assert {e["vehicle_name"] for e in r.json()["events"]} == {"500"}

    async def test_active_route_is_reachable_not_swallowed(self, scope_app):
        """``/active`` must not be captured by the ``/{event_id}`` route.

        Both are single-segment; FastAPI matches in declaration order, so
        a later declaration would make this 422 ("active" is not an int).
        Guarded on ``/active`` because it is now the only single-segment
        literal left on this router — ``/events`` used to hold this guard
        and was removed with the endpoint.
        """
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/active",
                            headers=_h(scope_app["token_owner"]))
        assert r.status_code == 200, r.text
        assert "events" in r.json()


class TestOwnerUnrestricted:
    """No company restriction => sees both companies (the control case:
    proves the tests above fail for scope reasons, not empty data)."""

    async def test_owner_sees_both_companies(self, scope_app):
        async with AsyncClient(
            transport=ASGITransport(app=scope_app["app"]),
            base_url="http://t",
        ) as c:
            r = await c.get("/api/parking/active",
                            headers=_h(scope_app["token_owner"]))
        names = {e["vehicle_name"] for e in r.json()["events"]}
        assert names == {"500", "900"}


class TestPredicateUnit:
    """The predicate itself — no DB, no app."""

    def test_empty_truck_list_denies(self):
        from features.parking.service import is_visible
        ev = {"vehicle_name": "500", "company_code": "AAA"}
        assert is_visible(ev, company_codes=[], truck_names=[]) is False

    def test_none_truck_list_means_not_truck_scoped(self):
        from features.parking.service import is_visible
        ev = {"vehicle_name": "500", "company_code": "AAA"}
        assert is_visible(ev, company_codes=[], truck_names=None) is True

    def test_odd_named_unit_matches_by_ID_not_by_substring(self):
        """"107" must still reach "Truck-107A" — the fleet does name units
        that way, and hiding a driver's own truck would be a regression.

        It reaches it through the IDENTITY ladder now, not a text prefix.
        The substring that used to carry this also matched "1107" and
        "2107", which on a visibility wall is a disclosure — the same
        defect corrected across alerting, work orders, maintenance and
        events.  The id rung keeps the legitimate case and drops the leak.
        """
        from features.parking.service import is_visible
        from capabilities.permissions.vehicle_scope import VehicleScope
        scope = VehicleScope(
            external_ids=frozenset({"sam_107"}), names=frozenset({"107"}),
        )
        own = {"vehicle_name": "Truck-107A", "vehicle_id": "sam_107",
               "company_code": "AAA"}
        assert is_visible(own, company_codes=[], truck_names=["107"],
                          scope=scope) is True
        # The over-match the substring came with is gone.
        other = {"vehicle_name": "1107", "vehicle_id": "sam_1107",
                 "company_code": "AAA"}
        assert is_visible(other, company_codes=[], truck_names=["107"],
                          scope=scope) is False

    def test_company_restriction_denies_other_company(self):
        from features.parking.service import is_visible
        ev = {"vehicle_name": "900", "company_code": "BBB"}
        assert is_visible(ev, company_codes=["AAA"], truck_names=None) is False

    def test_empty_company_list_is_unrestricted(self):
        from features.parking.service import is_visible
        ev = {"vehicle_name": "900", "company_code": "BBB"}
        assert is_visible(ev, company_codes=[], truck_names=None) is True
