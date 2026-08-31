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
    """The tabs speak the seen/working vocabulary now (owner decision
    2026-08-30): New = nobody has looked, All = everything, mine_working
    = the caller's claims.  The invariants these guarded survive the
    rename — a badge must match its list, and tabs must not disagree
    about the same rows."""

    async def test_counts_match_the_list_they_label(self, seeded):
        app, token = seeded["app"], seeded["token"]
        counts = (await _get(app, token, "/api/alerts/pending/segment-counts"))["counts"]
        # Fresh account: nothing seen, nothing claimed.
        assert counts["new"] == 5
        assert counts["all"] == 5
        assert counts["mine_working"] == 0
        # The tab badge and the list under it must not disagree.
        listed = await _pending(app, token, "view=new")
        assert listed["count"] == counts["new"]

    async def test_counts_follow_the_other_filters(self, seeded):
        """Switching tabs must change ONLY the ack-state, so each tab
        answers "how many, if I switched to you, with everything else as
        it is"."""
        counts = (await _get(
            seeded["app"], seeded["token"],
            "/api/alerts/pending/segment-counts?alert_type=fault",
        ))["counts"]
        assert counts["new"] == 2
        assert counts["all"] == 2

    async def test_new_is_personal_a_colleagues_view_moves_nothing(self, seeded, pg_db):
        """New is PER-VIEWER (owner decision 2026-08-31), even though the
        Seen COLUMN is account-wide: a colleague reading the board must
        not empty everyone else's New tab.  So my own view moves a row
        out of MY New; someone else's view moves nothing of mine."""
        app, token, db, acct = (
            seeded["app"], seeded["token"], seeded["db"], seeded["acct"])
        rows = await db.get_active_alert_history_for_account(acct.id)

        # A colleague reads two alerts — my New is untouched.
        colleague = await pg_db.create_user(9109, acct.id)
        await db.mark_alerts_seen(
            acct.id, colleague.id, [int(rows[0]["id"]), int(rows[1]["id"])])
        counts = (await _get(app, token, "/api/alerts/pending/segment-counts"))["counts"]
        assert counts["new"] == 5

        # I read one — MY New drops, All never loses a row to a view.
        me = await pg_db.get_user_by_telegram_id(9101)
        await db.mark_alerts_seen(acct.id, me.id, [int(rows[0]["id"])])
        counts = (await _get(app, token, "/api/alerts/pending/segment-counts"))["counts"]
        assert counts["new"] == 4
        assert counts["all"] == 5

    async def test_the_badge_and_its_list_agree_on_every_tab(self, seeded, pg_db):
        """The mismatch that shipped: the tab badge said 4,699 while the
        list under it held 12,759 rows.  The list path passes its legacy
        ack_state default ("active") alongside ``view``, and the days
        window keyed on ack_state — so the counts endpoint (which passes
        ack_state="all") windowed while the list did not.  A badge and
        the list it labels must be the same query; this walks all three
        tabs, with a window, and an old resolved row seeded to expose
        any windowing split."""
        from datetime import datetime, timedelta, timezone
        app, token, acct = seeded["app"], seeded["token"], seeded["acct"]
        rows = await pg_db.get_active_alert_history_for_account(acct.id)
        old_one = next(r for r in rows if r["vehicle_id"] == "V5")
        await pg_db.acknowledge_alert_history(
            old_one["id"], 9101, account_id=acct.id)
        stale = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        await pg_db._db.execute(
            "UPDATE alert_history SET first_seen = ? WHERE id = ?",
            (stale, old_one["id"]))
        me = await pg_db.get_user_by_telegram_id(9101)
        # Claim a row that is NOT the one staled out of the window above —
        # a claim on a windowed-out row is correctly invisible on a
        # windowed tab, which is a different fact than the one pinned here.
        fresh = next(r for r in rows if r["vehicle_id"] != "V5")
        await pg_db.claim_alert(acct.id, me.id, int(fresh["id"]))

        counts = (await _get(
            app, token, "/api/alerts/pending/segment-counts?days=30"))["counts"]
        for view in ("new", "all", "mine_working"):
            listed = await _pending(app, token, f"view={view}&days=30")
            assert listed["count"] == counts[view], (
                f"view={view}: badge {counts[view]} != list {listed['count']}")
        # And "mine" genuinely means mine: exactly the one claim.
        assert counts["mine_working"] == 1

    async def test_requires_auth(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get("/api/alerts/pending/segment-counts")
            assert r.status_code in (401, 403, 422)


class TestAllIsTheUnionOfItsParts:
    """The three tabs sit side by side with their counts showing, so they
    have to add up.

    The date window bounds resolved history but never open work — an
    unacknowledged alert is open regardless of age.  Applied per-QUERY
    that made 'all' drop old open rows which 'active' kept, and the board
    read "Not acknowledged 3,984 · All 3,295": a total smaller than one of
    its own parts.  Each number was defensible alone; together they were
    nonsense.  The rule is per-ROW now.
    """

    async def test_an_old_open_alert_stays_in_all(self, seeded, pg_db):
        from datetime import datetime, timedelta, timezone
        acct = seeded["acct"]
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        await pg_db._db.execute(
            "UPDATE alert_history SET first_seen = ? "
            "WHERE account_id = ? AND vehicle_id = ?",
            (old, acct.id, "V1"),
        )
        # A 30-day window must not hide it from EITHER tab.
        active = await _pending(seeded["app"], seeded["token"],
                                "ack_state=active&days=30")
        every = await _pending(seeded["app"], seeded["token"],
                               "ack_state=all&days=30")
        assert "T-100" in {a["vehicle_name"] for a in active["alerts"]}
        assert "T-100" in {a["vehicle_name"] for a in every["alerts"]}

    async def test_counts_add_up(self, seeded, pg_db):
        """all == active + acknowledged, exactly."""
        from datetime import datetime, timedelta, timezone
        acct = seeded["acct"]
        rows = await pg_db.get_active_alert_history_for_account(acct.id)
        await pg_db.acknowledge_alert_history(rows[0]["id"], 9101, account_id=acct.id)
        # ...and age an open one past the window, the case that broke it.
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        await pg_db._db.execute(
            "UPDATE alert_history SET first_seen = ? "
            "WHERE account_id = ? AND vehicle_id = ?",
            (old, acct.id, "V5"),
        )

        counts = (await _get(
            seeded["app"], seeded["token"],
            "/api/alerts/pending/segment-counts?days=30"))["counts"]
        # Under the seen/working tabs the identity is containment, not
        # partition: New is a subset of All, and nothing is double-counted.
        assert counts["all"] >= counts["new"]
        assert counts["all"] >= counts["mine_working"]

    async def test_an_old_RESOLVED_alert_is_still_windowed_out(self, seeded, pg_db):
        """The window must keep doing its job on history — otherwise 'all'
        would grow without bound and the date control would be a lie in
        the other direction."""
        from datetime import datetime, timedelta, timezone
        acct = seeded["acct"]
        rows = await pg_db.get_active_alert_history_for_account(acct.id)
        target = next(r for r in rows if r["vehicle_id"] == "V4")
        await pg_db.acknowledge_alert_history(target["id"], 9101, account_id=acct.id)
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        await pg_db._db.execute(
            "UPDATE alert_history SET first_seen = ? "
            "WHERE account_id = ? AND vehicle_id = ?",
            (old, acct.id, "V4"),
        )
        every = await _pending(seeded["app"], seeded["token"],
                               "ack_state=all&days=30")
        assert "T-400" not in {a["vehicle_name"] for a in every["alerts"]}


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
        listed = await _pending(seeded["app"], token, "view=new")
        assert counts["new"] == listed["count"] == len(listed["alerts"])
        # Only their own truck, not the account's five alerts.
        assert counts["new"] == 1

    async def test_company_restricted_badge_matches_its_list(self, seeded, pg_db):
        acct = seeded["acct"]
        await pg_db.add_company(acct.id, "OTHER", "key_other", "Other Co")
        user = await pg_db.create_user(9103, acct.id, role=Role.FLEET)
        token = create_jwt(user.telegram_id, acct.id, "fleet")

        counts = (await _get(
            seeded["app"], token, "/api/alerts/pending/segment-counts"))["counts"]
        listed = await _pending(seeded["app"], token, "view=new")
        assert counts["new"] == listed["count"]


class TestServerSideSort:
    """Sorting moved to SQL so it can order the whole queue rather than
    the page in hand.  The column arrives from a query string, so the
    only safe shape is a KEY into an allow-list — never a name spliced
    into the statement."""

    async def test_default_is_triage_order(self, seeded):
        """No sort chosen → severity first, then recency.  That's the
        order an operator wants before expressing a preference."""
        data = await _pending(seeded["app"], seeded["token"])
        sevs = [a["severity"] for a in data["alerts"]]
        rank = {"critical": 0, "warning": 1, "info": 2}
        assert sevs == sorted(sevs, key=lambda x: rank[x])

    async def test_sorts_by_vehicle_both_ways(self, seeded):
        asc = await _pending(seeded["app"], seeded["token"],
                             "sort=vehicle_name&dir=asc")
        desc = await _pending(seeded["app"], seeded["token"],
                              "sort=vehicle_name&dir=desc")
        names = [a["vehicle_name"] for a in asc["alerts"]]
        assert names == sorted(names)
        assert [a["vehicle_name"] for a in desc["alerts"]] == sorted(names, reverse=True)

    async def test_severity_sorts_by_RANK_not_alphabetically(self, seeded):
        """Alphabetically 'critical' falls between 'info' and 'warning',
        which would bury the rows that matter in the middle."""
        data = await _pending(seeded["app"], seeded["token"],
                              "sort=severity&dir=asc")
        assert data["alerts"][0]["severity"] == "critical"

    async def test_unknown_column_falls_back_instead_of_erroring(self, seeded):
        """A stale bookmark should show the board, not a 422 — and must
        not reach SQL."""
        data = await _pending(seeded["app"], seeded["token"], "sort=nonsense")
        assert data["count"] == 5

    async def test_injection_attempt_is_inert(self, seeded):
        """The value is a key, not a fragment.  If it were interpolated
        this would change the statement; as a key it simply misses."""
        evil = "h.id; DROP TABLE alert_history--"
        data = await _pending(seeded["app"], seeded["token"],
                              f"sort={evil}")
        assert data["count"] == 5
        # ...and the table is still there.
        again = await _pending(seeded["app"], seeded["token"])
        assert again["count"] == 5

    async def test_paging_is_stable_across_a_tie(self, seeded):
        """Rows sharing a sort value must not shuffle between pages, or
        an operator paging through sees one row twice and another never.
        Every seeded row has a distinct type, so sort by a column they
        SHARE and check the pages partition the set."""
        p1 = await _get(seeded["app"], seeded["token"],
                        "/api/alerts/pending?sort=acknowledged_at&page_size=2&page=1")
        p2 = await _get(seeded["app"], seeded["token"],
                        "/api/alerts/pending?sort=acknowledged_at&page_size=2&page=2")
        p3 = await _get(seeded["app"], seeded["token"],
                        "/api/alerts/pending?sort=acknowledged_at&page_size=2&page=3")
        ids = [a["id"] for a in p1["alerts"] + p2["alerts"] + p3["alerts"]]
        assert len(ids) == len(set(ids)) == 5     # no repeats, none lost


class TestRealPages:
    async def test_page_size_bounds_the_response(self, seeded):
        data = await _get(seeded["app"], seeded["token"],
                          "/api/alerts/pending?page_size=2&page=1")
        assert len(data["alerts"]) == 2
        assert data["count"] == 5           # the TOTAL, not the page
        assert data["total_pages"] == 3

    async def test_last_page_is_partial_not_empty(self, seeded):
        data = await _get(seeded["app"], seeded["token"],
                          "/api/alerts/pending?page_size=2&page=3")
        assert len(data["alerts"]) == 1


class TestServerSideExport:
    """The board pages 25 rows at a time, so a client-side "export all"
    could only ever write what was on screen.  This endpoint exists so the
    file matches the QUERY, not the page — and carries the same scoping,
    because an export that widened a driver's view would leak."""

    async def _csv(self, app, token, query=""):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get(f"/api/alerts/pending/export?{query}",
                            headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, r.text
            return r

    async def test_exports_every_matching_row_not_one_page(self, seeded):
        r = await self._csv(seeded["app"], seeded["token"])
        lines = [l for l in r.text.strip().splitlines() if l]
        assert len(lines) == 6            # header + all 5 seeded rows
        assert lines[0].startswith("alert_id,severity,type,kind,vehicle")

    async def test_honours_the_board_filters(self, seeded):
        r = await self._csv(seeded["app"], seeded["token"], "alert_type=fault")
        lines = [l for l in r.text.strip().splitlines() if l]
        assert len(lines) == 3            # header + the 2 faults
        assert "T-300" not in r.text      # the health alert is excluded

    async def test_honours_the_search(self, seeded):
        r = await self._csv(seeded["app"], seeded["token"], "q=Battle")
        assert "T-100" in r.text and "T-300" in r.text
        assert "T-400" not in r.text

    async def test_is_a_csv_attachment(self, seeded):
        r = await self._csv(seeded["app"], seeded["token"])
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert ".csv" in r.headers["content-disposition"]

    async def test_driver_exports_only_their_own_truck(self, seeded, pg_db):
        """An export that widened scope would be a data leak, not a bug."""
        acct = seeded["acct"]
        driver = await pg_db.create_user(9301, acct.id, role=Role.DRIVER)
        await pg_db.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="T-100",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(driver.telegram_id, acct.id, "driver")
        r = await self._csv(seeded["app"], token)
        assert "T-100" in r.text
        for other in ("T-200", "T-300", "T-400", "T-500"):
            assert other not in r.text

    async def test_requires_auth(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get("/api/alerts/pending/export")
            assert r.status_code in (401, 403, 422)
