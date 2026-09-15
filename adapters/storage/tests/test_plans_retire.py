"""Retiring a plan in the store: the stamp, and the trial that skips it.

The route-level doors are pinned in interfaces/api/tests/test_system_plans.py.
These are the two the store answers on its own — the ones the route's
refusals mean nobody can reach through the API, which is exactly why
they are tested from underneath.
"""

import pytest

@pytest.mark.asyncio
async def test_a_trial_never_starts_on_a_retired_plan(pg_db):
    """Belt to the route's braces, tested at its own level.

    The retire route refuses the trial default outright, so through the
    API these two states can never meet — which is exactly why this is
    tested here instead.  A plan flagged both ways can still arrive by a
    hand-edited row or a restored backup, and a self-serve signup is a
    new account landing on a plan: the one thing "retired" means.
    """
    db = pg_db
    await db.upsert_plan("sold", label="Sold", included=["*"], trial_default=True)
    assert await db.trial_plan() == "sold"

    # the state the route will not let you reach, reached anyway
    await db.upsert_plan("sold", label="Sold", included=["*"],
                         retired_at="2026-09-15T00:00:00+00:00")
    assert (await db.get_plan("sold"))["trial_default"] is True
    assert (await db.get_plan("sold"))["retired"] is True
    assert await db.trial_plan() is None, "a signup does not start on a plan we stopped selling"


@pytest.mark.asyncio
async def test_retired_is_read_from_the_stamp_and_clears(pg_db):
    db = pg_db
    row = await db.upsert_plan("onoff", label="On Off", included=["*"])
    assert row["retired"] is False and row["retired_at"] == ""
    row = await db.upsert_plan("onoff", label="On Off", included=["*"],
                               retired_at="2026-09-15T00:00:00+00:00")
    assert row["retired"] is True and row["retired_at"].startswith("2026-09-15")
    # and a field left None keeps it, the way every other catalog field does
    row = await db.upsert_plan("onoff", label="Renamed", included=["*"])
    assert row["retired"] is True, "a rename does not put it back on sale"
    row = await db.upsert_plan("onoff", label="On Off", included=["*"], retired_at="")
    assert row["retired"] is False
