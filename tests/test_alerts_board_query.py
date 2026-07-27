"""The board's filters now run on the SERVER, so they must mean what the
grid's controls imply.

The board holds a capped page of a much larger queue, which is why these
moved server-side at all: a client-side narrowing filters the loaded rows
and reports that as the answer for the whole set.  What that buys is only
real if the server honours the same shapes the UI offers:

  * the Type / Severity menus are MULTI-select, so a list must become an
    IN, not silently match nothing (or only the first value);
  * one search box covers vehicle AND location, because it replaced two
    controls that each told half the truth;
  * the Status tabs show counts, and a count that disagrees with the list
    it labels is worse than no count.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    database = pg_db
    acct = await database.create_account("Board Co")
    await database.add_company(acct.id, "CO", "key_co", "Board Co")
    owner = await database.create_user(9101, acct.id, role=Role.OWNER)

    # Distinct types, severities and locations so every filter dimension
    # can be told apart by its result set.
    seed = [
        ("V1", "T-100", "fault",   "critical", "Battle Creek, MI"),
        ("V2", "T-200", "fault",   "warning",  "Oklahoma City, OK"),
        ("V3", "T-300", "health",  "warning",  "Battle Creek, MI"),
        ("V4", "T-400", "fuel",    "info",     "Dallas, TX"),
        ("V5", "T-500", "parking", "critical", "Dallas, TX"),
    ]
    for vid, name, atype, sev, loc in seed:
        await database.upsert_alert_history(
            account_id=acct.id, alert_type=atype, vehicle_id=vid,
            vehicle_name=name, severity=sev, location=loc,
        )

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = database
    from interfaces.api.app import create_api
    app = create_api()

    yield {"app": app, "db": database, "acct": acct,
           "token": create_jwt(owner.telegram_id, acct.id, "owner")}
    _cp._db = _old


async def _get(app, token, path):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get(path, headers={"Authorization": f"Bearer {token}"},
                        )
        assert r.status_code == 200, r.text
        return r.json()


async def _pending(app, token, query=""):
    return await _get(app, token, f"/api/alerts/pending?page_size=100&{query}")


class TestMultiValueFilters:
    async def test_single_type_still_behaves_as_before(self, seeded):
        data = await _pending(seeded["app"], seeded["token"], "alert_type=fault")
        assert {a["alert_type"] for a in data["alerts"]} == {"fault"}
        assert data["count"] == 2

    async def test_comma_list_becomes_an_IN(self, seeded):
        """The grid's Type menu is multi-select.  Ticking Fault AND Health
        has to be one query — matching neither, or only the first, would
        make the menu lie about what it selected."""
        data = await _pending(seeded["app"], seeded["token"],
                              "alert_type=fault,health")
        assert {a["alert_type"] for a in data["alerts"]} == {"fault", "health"}
        assert data["count"] == 3

    async def test_multi_severity(self, seeded):
        data = await _pending(seeded["app"], seeded["token"],
                              "severity=critical,info")
        assert {a["severity"] for a in data["alerts"]} == {"critical", "info"}
        assert data["count"] == 3

    async def test_type_and_severity_compose(self, seeded):
        data = await _pending(seeded["app"], seeded["token"],
                              "alert_type=fault,parking&severity=critical")
        assert {a["vehicle_name"] for a in data["alerts"]} == {"T-100", "T-500"}

    async def test_whitespace_and_empties_are_tolerated(self, seeded):
        """The value round-trips through a URL and a join; a stray comma
        must not silently match nothing."""
        data = await _pending(seeded["app"], seeded["token"],
                              "alert_type=fault,,%20health%20")
        assert data["count"] == 3

    async def test_a_filter_of_nothing_narrows_to_nothing(self, seeded):
        """A value that was PROVIDED but reduces to no values must return
        an empty board — NOT fall through to "no filter" and hand back
        every type, which is the opposite of what was asked."""
        data = await _pending(seeded["app"], seeded["token"], "alert_type=,,,")
        assert data["count"] == 0
        assert data["alerts"] == []


class TestSearchIsLiteral:
    async def test_percent_matches_itself_not_everything(self, seeded, pg_db):
        """'%' is a LIKE wildcard.  Unescaped, an operator typing one
        would silently select the entire queue while believing they had
        narrowed it."""
        await pg_db.upsert_alert_history(
            account_id=seeded["acct"].id, alert_type="fuel",
            vehicle_id="V9", vehicle_name="T-900", severity="info",
            location="Lot 50% full",
        )
        data = await _pending(seeded["app"], seeded["token"], "q=50%25")
        assert [a["vehicle_name"] for a in data["alerts"]] == ["T-900"]

    async def test_underscore_is_literal_too(self, seeded, pg_db):
        await pg_db.upsert_alert_history(
            account_id=seeded["acct"].id, alert_type="fuel",
            vehicle_id="V8", vehicle_name="T_800", severity="info",
            location="Depot",
        )
        # '_' matches any single char in LIKE; unescaped this would also
        # match "T-800"-style names.
        data = await _pending(seeded["app"], seeded["token"], "q=T_800")
        assert [a["vehicle_name"] for a in data["alerts"]] == ["T_800"]


class TestOneSearchBox:
    async def test_matches_vehicle_name(self, seeded):
        data = await _pending(seeded["app"], seeded["token"], "q=T-300")
        assert [a["vehicle_name"] for a in data["alerts"]] == ["T-300"]

    async def test_matches_location_too(self, seeded):
        """The whole point of the merge: location used to be searchable
        only within the rows already loaded, i.e. some of your alerts."""
        data = await _pending(seeded["app"], seeded["token"], "q=Battle")
        assert {a["vehicle_name"] for a in data["alerts"]} == {"T-100", "T-300"}

    async def test_is_case_insensitive(self, seeded):
        data = await _pending(seeded["app"], seeded["token"], "q=dallas")
        assert data["count"] == 2

    async def test_count_agrees_with_the_rows(self, seeded):
        """The pager and the truncation notice read ``count``; if it were
        computed without the search the board would claim rows it isn't
        showing."""
        data = await _pending(seeded["app"], seeded["token"], "q=Battle")
        assert data["count"] == len(data["alerts"]) == 2

    async def test_legacy_vehicle_param_still_works(self, seeded):
        data = await _pending(seeded["app"], seeded["token"], "vehicle=T-400")
        assert [a["vehicle_name"] for a in data["alerts"]] == ["T-400"]


class TestSegmentCounts:
    async def test_counts_match_the_list_they_label(self, seeded):
        app, token = seeded["app"], seeded["token"]
        counts = (await _get(app, token, "/api/alerts/pending/segment-counts"))["counts"]
        assert counts["active"] == 5
        assert counts["acknowledged"] == 0
        assert counts["all"] == 5
        # The tab badge and the list under it must not disagree.
        listed = await _pending(app, token, "ack_state=active")
        assert listed["count"] == counts["active"]

    async def test_counts_follow_the_other_filters(self, seeded):
        """Switching tabs must change ONLY the ack-state, so each tab
        answers "how many, if I switched to you, with everything else as
        it is"."""
        counts = (await _get(
            seeded["app"], seeded["token"],
            "/api/alerts/pending/segment-counts?alert_type=fault",
        ))["counts"]
        assert counts["active"] == 2
        assert counts["all"] == 2

    async def test_acknowledging_moves_a_row_between_tabs(self, seeded):
        app, token, db, acct = (
            seeded["app"], seeded["token"], seeded["db"], seeded["acct"])
        rows = await db.get_active_alert_history_for_account(acct.id)
        await db.acknowledge_alert_history(rows[0]["id"], 9101, account_id=acct.id)

        counts = (await _get(app, token, "/api/alerts/pending/segment-counts"))["counts"]
        assert counts["active"] == 4
        assert counts["acknowledged"] == 1
        assert counts["all"] == 5      # 'all' spans both, never double-counts

    async def test_requires_auth(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get("/api/alerts/pending/segment-counts")
            assert r.status_code in (401, 403, 422)


class TestSegmentCountsUnderRestrictedScope:
    """The counts and the list are computed by DIFFERENT code paths for a
    scoped user (both fall out of SQL into Python filtering).  A badge
    larger than the rows behind it would be a privacy tell as much as a
    UI bug — it would report alerts the viewer can't see."""

    async def test_driver_badge_never_exceeds_what_they_can_see(self, seeded, pg_db):
        acct = seeded["acct"]
        driver = await pg_db.create_user(9102, acct.id, role=Role.DRIVER)
        await pg_db.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="T-100",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(driver.telegram_id, acct.id, "driver")

        counts = (await _get(
            seeded["app"], token, "/api/alerts/pending/segment-counts"))["counts"]
        listed = await _pending(seeded["app"], token, "ack_state=active")
        assert counts["active"] == listed["count"] == len(listed["alerts"])
        # Only their own truck, not the account's five alerts.
        assert counts["active"] == 1

    async def test_company_restricted_badge_matches_its_list(self, seeded, pg_db):
        acct = seeded["acct"]
        await pg_db.add_company(acct.id, "OTHER", "key_other", "Other Co")
        user = await pg_db.create_user(9103, acct.id, role=Role.FLEET)
        token = create_jwt(user.telegram_id, acct.id, "fleet")

        counts = (await _get(
            seeded["app"], token, "/api/alerts/pending/segment-counts"))["counts"]
        listed = await _pending(seeded["app"], token, "ack_state=active")
        assert counts["active"] == listed["count"]
