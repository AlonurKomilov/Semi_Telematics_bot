"""Guard: the row cap runs AFTER the caller's vehicle scope, not before.

get_alert_history pushed its LIMIT into SQL and filtered by scope in
Python afterwards. The ORDER BY is account-wide — severity, then
recency — so the rows SQL chose were the ACCOUNT's worst, the filter
removed the ones belonging to other trucks, and the caller was told what
survived.

A driver scoped to one truck in a busy account therefore received the
fleet's most severe alerts, had every one of them dropped, and read
"no alerts on your truck" while their own truck carried open warnings
below the cut. The count reported was the post-filter number, with
nothing to say rows had been cut before the filter ever ran.

The REST board already handled this correctly: for a driver- or
company-scoped caller it calls the same storage method with no limit and
paginates in Python.
"""

import pytest

from capabilities.alerting.ai_tool import get_alert_history


class _DB:
    """Records whether a LIMIT reached SQL, and serves severity-ordered
    rows the way the real ORDER BY would."""

    def __init__(self, rows):
        self._rows = rows
        self.limit_seen = "unset"

    async def get_active_alert_history_for_account_paged(
        self, account_id, *, alert_type=None, vehicle_substring=None,
        severity=None, ack_state="active", limit=None, **kw,
    ):
        self.limit_seen = limit
        return list(self._rows) if limit is None else list(self._rows)[:limit]


def _rows():
    """25 critical alerts on other trucks, then the caller's own three.

    Exactly the shape that made the old code answer zero: the caller's
    rows sort below a full page of the account's worst.
    """
    other = [
        {"id": i, "vehicle_name": f"OTHER-{i}", "severity": "critical",
         "alert_type": "fault", "message": "stop lamp"}
        for i in range(25)
    ]
    mine = [
        {"id": 100 + i, "vehicle_name": "MINE-1", "severity": "warning",
         "alert_type": "fuel", "message": "low fuel"}
        for i in range(3)
    ]
    return other + mine


@pytest.mark.asyncio
async def test_a_scoped_caller_sees_their_own_alerts_not_an_empty_page():
    db = _DB(_rows())
    res = await get_alert_history(
        {"_scope_vehicles": ["MINE-1"], "limit": 25}, None,
        account_id=1, db=db,
    )
    assert db.limit_seen is None, (
        "a scoped caller must not have the cap applied in SQL — it picks "
        "the account's worst rows before scope ever runs"
    )
    assert res["count"] == 3, res
    assert {a["vehicle"] for a in res["alerts"]} == {"MINE-1"}


@pytest.mark.asyncio
async def test_an_unrestricted_caller_still_gets_the_cheap_sql_path():
    db = _DB(_rows())
    res = await get_alert_history({"limit": 10}, None, account_id=1, db=db)
    assert db.limit_seen == 10, "no scope means the cap belongs in SQL"
    assert res["count"] == 10


@pytest.mark.asyncio
async def test_truncation_is_stated_rather_than_implied():
    """A capped answer that looks complete is worse than a short one."""
    many = [
        {"id": i, "vehicle_name": "MINE-1", "severity": "warning",
         "alert_type": "fuel", "message": "low fuel"}
        for i in range(40)
    ]
    db = _DB(many)
    res = await get_alert_history(
        {"_scope_vehicles": ["MINE-1"], "limit": 25}, None,
        account_id=1, db=db,
    )
    assert res["count"] == 25
    assert res["matched"] == 40
    assert res["truncated"] is True


@pytest.mark.asyncio
async def test_an_empty_scope_still_fails_closed():
    db = _DB(_rows())
    res = await get_alert_history(
        {"_scope_vehicles": [], "limit": 25}, None, account_id=1, db=db,
    )
    assert res["count"] == 0
