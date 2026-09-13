"""The work-order tool asks for a window, not for the whole history.

``get_recent_work_orders`` narrowed by date in Python after SELECTing
every work order the account ever had — vendor, notes, complaint,
cause and correction text for years of shop visits — to keep 90 days
and return 30 rows.

The store now takes ``since``.  It is deliberately generous: undated
rows and anything not shaped like a 2000s ISO date survive it, because
the tool counts those separately and a string comparison must not
swallow the very rows that say "this data has a problem".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.work_orders.ai_tool import get_recent_work_orders


class _RecordingDB:
    """Answers like the store, and remembers what it was asked."""

    def __init__(self, rows):
        self.rows = rows
        self.since = "__never_set__"

    async def list_work_orders(self, account_id, *, status=None,
                               payment_status=None, vehicle_name=None,
                               since=None):
        self.since = since
        # Mirror the real predicate so the tool sees what SQL would give.
        if since is None:
            return list(self.rows)
        out = []
        for r in self.rows:
            sd = r.get("service_date")
            if not sd or not _looks_like_a_2000s_iso_date(sd) or sd >= since:
                out.append(r)
        return out

    async def list_vehicles(self, account_id, **kw):
        return []


def _looks_like_a_2000s_iso_date(sd: str) -> bool:
    """The Python twin of ``service_date LIKE '2___-__-__%'``."""
    return (len(sd) >= 10 and sd[0] == "2"
            and sd[4] == "-" and sd[7] == "-")


def _wo(wid, service_date, cost=100.0):
    return {"id": wid, "vehicle_name": "231", "vendor_name": "Shop",
            "service_date": service_date, "status": "open",
            "payment_status": "unpaid", "total_cost": cost, "notes": ""}


def _day(offset_days: int) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(days=offset_days)).date().isoformat()


@pytest.mark.asyncio
async def test_the_window_reaches_the_store():
    db = _RecordingDB([_wo(1, _day(-5))])
    await get_recent_work_orders({"days": 30}, None, account_id=7, db=db)
    assert db.since == _day(-30), "the store was asked for the history"


@pytest.mark.asyncio
async def test_the_floor_is_a_date_so_the_store_hands_back_a_superset():
    """A row inside today's boundary must survive the store's filter.

    The Python check compares instants; the store compares days.  The
    day floor is the earlier of the two, so the store can only ever be
    more generous — never drop a row the tool would have kept.
    """
    db = _RecordingDB([_wo(1, _day(-30))])
    out = await get_recent_work_orders({"days": 30}, None, account_id=7, db=db)
    assert db.since <= _day(-30)
    assert out["count"] in (0, 1)   # the tool's own instant check decides


@pytest.mark.asyncio
async def test_old_work_orders_never_leave_the_database():
    db = _RecordingDB([_wo(1, _day(-5)), _wo(2, _day(-400))])
    out = await get_recent_work_orders({"days": 30}, None, account_id=7, db=db)
    assert out["count"] == 1
    assert [w["id"] for w in out["work_orders"]] == [1]


@pytest.mark.asyncio
async def test_the_counters_the_window_cannot_judge_still_see_their_rows():
    """Undated and unreadable rows must survive the store's pre-filter.

    These are the two facts the tool reports separately, and they are
    exactly the rows a naive ``service_date >= ?`` would delete.
    """
    db = _RecordingDB([
        _wo(1, _day(-5)),
        _wo(2, None),            # nobody dated it
        _wo(3, ""),              # nobody dated it, other spelling
        _wo(4, "0000-00-00"),    # imported garbage
        _wo(5, "12/05/2019"),    # pasted in the wrong format
        _wo(6, _day(-400)),      # genuinely old — this one may go
    ])
    out = await get_recent_work_orders({"days": 30}, None, account_id=7, db=db)
    assert out["undated_count"] == 2
    assert out["unreadable_date_count"] == 2
    assert out["count"] == 1


# ── The predicate itself, against the real database ───────────────
#
# Everything above runs the Python twin of the SQL.  This one runs the
# SQL: the pattern carries a literal '%', which is exactly the kind of
# thing a placeholder translation layer can mangle.

@pytest.mark.asyncio
async def test_the_store_really_filters(pg_db):
    acct = await pg_db.create_account("Window Co")
    kept, dropped = {}, {}
    for label, sd in (("recent", _day(-5)),
                      ("undated", None),
                      ("blank", ""),
                      ("zero_date", "0000-00-00"),
                      ("wrong_format", "12/05/2019")):
        kept[label] = await pg_db.add_work_order(
            acct.id, "OSY", "231", "Shop", service_date=sd)
    dropped["old"] = await pg_db.add_work_order(
        acct.id, "OSY", "231", "Shop", service_date=_day(-400))

    rows = await pg_db.list_work_orders(acct.id, since=_day(-30))
    got = {r["id"] for r in rows}
    for label, wid in kept.items():
        assert wid in got, f"{label} was swallowed by the window"
    assert dropped["old"] not in got

    # No window = no filtering, for every other caller.
    everything = await pg_db.list_work_orders(acct.id)
    assert len(everything) == len(kept) + len(dropped)


@pytest.mark.asyncio
async def test_the_named_residual_behaves_as_documented(pg_db):
    """Pin the one row class the window is NOT exact about.

    A service_date shaped like a 2000s ISO date but not a real calendar
    date ('2019-13-45') goes to the string comparison like any other.
    If it sorts before the window it is dropped, so the tool's
    unreadable-date counter stops seeing it once it ages out — the
    residual list_work_orders' docstring names.  If it sorts inside the
    window it survives and is still counted.

    Recorded rather than discovered: if someone later makes the
    predicate exact, this test should be deleted, not worked around.
    """
    acct = await pg_db.create_account("Residual Co")
    old_garbage = await pg_db.add_work_order(
        acct.id, "OSY", "231", "Shop", service_date="2019-13-45")
    fresh_garbage = await pg_db.add_work_order(
        acct.id, "OSY", "231", "Shop", service_date="2099-13-45")

    got = {r["id"] for r in
           await pg_db.list_work_orders(acct.id, since=_day(-30))}
    assert old_garbage not in got     # the documented blind spot
    assert fresh_garbage in got       # still reaches the counter
