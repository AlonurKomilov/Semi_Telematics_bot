"""The oversight card must describe the pager, not its own opinion.

`/alerts/escalations` reports what the re-escalation job is doing. It
used to restate the job's rules in its own words, and the two drifted:
the card counted every severity and every alert type and knew nothing
about claims, so on the live account it told the owner that 222 alerts
were "unclaimed > 60m — re-pinging" while the pager — critical, claimed-
free, and only the escalating types — was touching almost none of them.

A summary that overstates what a safety mechanism is doing is worse than
no summary: it reads as coverage. These pin the agreement itself, so the
next rule added to one side cannot quietly skip the other.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

pytestmark = pytest.mark.asyncio


async def _mk(db, acct, *, atype="fault", severity="critical",
              vid="v1", name="100", subkey="e:1", age_days=3):
    """One aged alert — old enough that the pager's first-reminder
    window has long passed."""
    from datetime import datetime, timedelta, timezone
    row = await db.upsert_alert_history(
        acct, atype, vid, name, alert_subkey=subkey, severity=severity)
    old = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    await db._db.execute(
        "UPDATE alert_history SET first_seen = ?, last_seen = ? WHERE id = ?",
        (old, old, int(row["id"])))
    await db._db.commit()
    return int(row["id"])


class TestPagerScopeIsShared:
    """``get_pager_scope_rows`` and the job's candidate query read one
    predicate (``PAGER_SCOPE_SQL``).  What the card counts and what the
    pager chases therefore cannot disagree about severity, status or
    claims — the three the old card got wrong."""

    async def test_the_card_population_excludes_what_the_pager_ignores(self, pg_db):
        acct = (await pg_db.create_account("Oversight Co")).id
        user = await pg_db.create_user(telegram_id=9700, account_id=acct)

        paged = await _mk(pg_db, acct, vid="p1", subkey="e:paged")
        warning = await _mk(pg_db, acct, severity="warning",
                            vid="p2", subkey="e:warn")
        claimed = await _mk(pg_db, acct, vid="p3", subkey="e:claimed")
        await pg_db.claim_alert(acct, user.id, claimed)

        scope = {int(r["id"]) for r in await pg_db.get_pager_scope_rows(acct)}
        assert paged in scope
        assert warning not in scope      # warnings stopped paging
        assert claimed not in scope      # a claim IS an owner

    async def test_it_agrees_with_the_job_candidate_query(self, pg_db):
        """The two queries answer different questions (one filters by
        attempt count, one does not), but on rows with no attempts yet
        they must return the same set — that overlap IS the shared
        predicate, and it is what the card's honesty rests on."""
        acct = (await pg_db.create_account("Agreement Co")).id
        user = await pg_db.create_user(telegram_id=9701, account_id=acct)
        await _mk(pg_db, acct, vid="a1", subkey="e:a1")
        await _mk(pg_db, acct, vid="a2", subkey="e:a2")
        await _mk(pg_db, acct, severity="warning", vid="a3", subkey="e:a3")
        claimed = await _mk(pg_db, acct, vid="a4", subkey="e:a4")
        await pg_db.claim_alert(acct, user.id, claimed)

        card = {int(r["id"]) for r in await pg_db.get_pager_scope_rows(acct)}
        job = {int(r["id"]) for r in
               await pg_db.get_active_unacked_history_for_reescalation(
                   acct, "9999-01-01T00:00:00", max_attempts=4)}
        assert card == job

    async def test_a_resolved_alert_leaves_the_scope(self, pg_db):
        acct = (await pg_db.create_account("Resolved Scope Co")).id
        aid = await _mk(pg_db, acct, vid="r1", subkey="e:r1")
        await pg_db.acknowledge_alert_history(aid, 9702, account_id=acct)
        scope = {int(r["id"]) for r in await pg_db.get_pager_scope_rows(acct)}
        assert aid not in scope


class TestTheCardsTwoNumbers:
    """past_due = the pager is chasing it.  breached = the pager gave up
    and nobody claimed it.  Exclusive, because a row past the cap is not
    being re-pinged and counting it as such is the same overstatement in
    miniature."""

    async def _summary(self, pg_db, acct):
        from capabilities.alerting.router import escalation_summary
        return await escalation_summary(
            user={"account_id": acct, "sub": "1", "uid": 1},
            tenant_db=pg_db)

    async def test_only_escalating_types_are_counted(self, pg_db):
        """The type gate lives in config and the job applies it in
        Python; the card applies the same constant.  A critical alert of
        a non-escalating type never pages, so reporting it as
        're-pinging' would be a claim about a message nobody sends."""
        from infra.config import REESCALATE_ALERT_TYPES
        acct = (await pg_db.create_account("Types Co")).id
        qualified = next(iter(REESCALATE_ALERT_TYPES))
        await _mk(pg_db, acct, atype=qualified, vid="t1", subkey="e:t1")
        await _mk(pg_db, acct, atype="parking", vid="t2", subkey="e:t2")

        out = await self._summary(pg_db, acct)
        assert out["past_due_count"] == 1

    async def test_a_claimed_alert_disappears_from_past_due(self, pg_db):
        """The behaviour the owner will actually watch for: pressing
        'Work on it' should visibly reduce this number."""
        from infra.config import REESCALATE_ALERT_TYPES
        acct = (await pg_db.create_account("Claim Drops Co")).id
        user = await pg_db.create_user(telegram_id=9703, account_id=acct)
        qualified = next(iter(REESCALATE_ALERT_TYPES))
        aid = await _mk(pg_db, acct, atype=qualified, vid="c1", subkey="e:c1")

        assert (await self._summary(pg_db, acct))["past_due_count"] == 1
        await pg_db.claim_alert(acct, user.id, aid)
        assert (await self._summary(pg_db, acct))["past_due_count"] == 0

    async def test_past_due_and_breached_never_double_count(self, pg_db):
        from infra.config import REESCALATE_ALERT_TYPES, REESCALATE_MAX_ATTEMPTS
        acct = (await pg_db.create_account("Buckets Co")).id
        qualified = next(iter(REESCALATE_ALERT_TYPES))
        chasing = await _mk(pg_db, acct, atype=qualified,
                            vid="b1", subkey="e:b1")
        capped = await _mk(pg_db, acct, atype=qualified,
                           vid="b2", subkey="e:b2")
        await pg_db._db.execute(
            "UPDATE alert_history SET reescalate_count = ? WHERE id = ?",
            (REESCALATE_MAX_ATTEMPTS, capped))
        await pg_db._db.commit()

        out = await self._summary(pg_db, acct)
        assert out["past_due_count"] == 1        # only the one still chased
        assert out["breached_count"] == 1        # only the one given up on
        assert chasing != capped

    async def test_warnings_are_absent_from_both_numbers(self, pg_db):
        """The single biggest source of the old overstatement."""
        from infra.config import REESCALATE_ALERT_TYPES
        acct = (await pg_db.create_account("No Warnings Co")).id
        qualified = next(iter(REESCALATE_ALERT_TYPES))
        for i in range(5):
            await _mk(pg_db, acct, atype=qualified, severity="warning",
                      vid=f"w{i}", subkey=f"e:w{i}")
        out = await self._summary(pg_db, acct)
        assert out["past_due_count"] == 0
        assert out["breached_count"] == 0
        assert out["by_persona"] == {}
