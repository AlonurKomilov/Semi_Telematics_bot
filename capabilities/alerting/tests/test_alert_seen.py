"""The seen ledger — who VIEWED an alert, demanded of no one.

Seen and Acknowledge are different facts and the tests pin the
difference: seen is exposure (passive, append-only, first-seen wins),
acknowledge is responsibility (active, stops the re-page).  The one
rule that must never soften is that a seen row changes NOTHING about
escalation — a critical that stopped paging because someone scrolled
past it would be a safety regression.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")


async def _seed(pg_db, tg=9950):
    acct = await pg_db.create_account("Seen Co")
    user = await pg_db.create_user(telegram_id=tg, account_id=acct.id)
    alert = await pg_db.upsert_alert_history(
        acct.id, "fuel", "veh-1", "132", alert_subkey="t1:fuel:17")
    return acct.id, user.id, int(alert["id"])


class TestTheLedger:

    async def test_a_view_is_recorded_once_and_only_once(self, pg_db):
        acct, uid, aid = await _seed(pg_db)
        assert await pg_db.mark_alerts_seen(acct, uid, [aid]) == 1
        # Re-viewing changes nothing: the fact cannot become truer.
        assert await pg_db.mark_alerts_seen(acct, uid, [aid]) == 0
        seen = await pg_db.get_seen_for_alerts(acct, [aid])
        assert [v["user_id"] for v in seen[aid]] == [uid]

    async def test_a_foreign_or_invented_id_inserts_nothing(self, pg_db):
        """The tenancy wall.  Ids are joined against this account's own
        alert_history, so the caller can only ever record views of what
        their account owns — an id from another tenant, or one that does
        not exist, silently records nothing rather than poisoning the
        ledger or confirming the id exists."""
        acct, uid, aid = await _seed(pg_db, tg=9951)
        other = await pg_db.create_account("Other Co")
        other_alert = await pg_db.upsert_alert_history(
            other.id, "fuel", "veh-9", "999", alert_subkey="t2:fuel:9")
        marked = await pg_db.mark_alerts_seen(
            acct, uid, [int(other_alert["id"]), 99999999, aid])
        assert marked == 1                      # only our own
        assert await pg_db.get_seen_for_alerts(other.id,
                                               [int(other_alert["id"])]) == {}

    async def test_two_viewers_read_in_the_order_they_looked(self, pg_db):
        acct, uid, aid = await _seed(pg_db, tg=9952)
        second = await pg_db.create_user(telegram_id=9953, account_id=acct)
        await pg_db.mark_alerts_seen(acct, uid, [aid])
        await pg_db.mark_alerts_seen(acct, second.id, [aid])
        seen = await pg_db.get_seen_for_alerts(acct, [aid])
        assert [v["user_id"] for v in seen[aid]] == [uid, second.id]

    async def test_seen_never_touches_acknowledgment(self, pg_db):
        """The line that must not move: a seen row is invisible to the
        ack machinery.  acknowledged_at stays empty, status stays
        active, and therefore the re-escalation job — which keys on
        exactly those columns — keeps paging."""
        acct, uid, aid = await _seed(pg_db, tg=9954)
        await pg_db.mark_alerts_seen(acct, uid, [aid])
        cur = await pg_db._db.execute(
            "SELECT status, acknowledged_at, acknowledged_by "
            "  FROM alert_history WHERE id = ?", (aid,))
        row = dict(await cur.fetchone())
        assert row["status"] == "active"
        assert not row["acknowledged_at"]
        assert not row["acknowledged_by"]

    async def test_the_batch_is_capped(self, pg_db):
        """200 ids per write, mirroring the inbox mark-read cap — the
        board batches a visible page, never the account."""
        acct, uid, aid = await _seed(pg_db, tg=9955)
        # 500 ids in, only the real one exists; no error, no unbounded work.
        marked = await pg_db.mark_alerts_seen(
            acct, uid, [aid] + list(range(1000000, 1000499)))
        assert marked <= 1


class TestWorkingOn:
    """The claim — voluntary, multi-person, and what stops the pager.

    Working-on replaced Acknowledge as the user verb (owner decision
    2026-08-30): nobody is asked to press it, an employee claims a task
    because they judge it theirs.  These pin the three promises: claims
    add rather than replace, the tenancy wall holds, and a claimed
    alert leaves the escalation candidate list while an unclaimed
    critical stays on it.
    """

    async def test_two_claims_join_rather_than_replace(self, pg_db):
        acct, uid, aid = await _seed(pg_db, tg=9960)
        second = await pg_db.create_user(telegram_id=9961, account_id=acct)
        assert await pg_db.claim_alert(acct, uid, aid) is True
        assert await pg_db.claim_alert(acct, second.id, aid) is True
        # Re-claiming is idempotent, not an error.
        assert await pg_db.claim_alert(acct, uid, aid) is False
        workers = await pg_db.get_workers_for_alerts(acct, [aid])
        assert [w["user_id"] for w in workers[aid]] == [uid, second.id]

    async def test_a_foreign_id_claims_nothing(self, pg_db):
        acct, uid, _ = await _seed(pg_db, tg=9962)
        other = await pg_db.create_account("Other Work Co")
        theirs = await pg_db.upsert_alert_history(
            other.id, "fault", "veh-x", "777", alert_subkey="w:SPN1")
        assert await pg_db.claim_alert(acct, uid, int(theirs["id"])) is False
        assert await pg_db.get_workers_for_alerts(
            other.id, [int(theirs["id"])]) == {}

    async def test_a_claim_silences_the_pager_an_unclaimed_critical_stays(self, pg_db):
        """The pager's job is finding an owner; a claim IS an owner.
        And the same query is where warning-level paging ended: 420
        reminders went to warnings and none was ever answered, so only
        criticals page at all now."""
        acct, uid, _ = await _seed(pg_db, tg=9963)
        claimed = await pg_db.upsert_alert_history(
            acct, "fault", "veh-a", "201", alert_subkey="e:SPN9",
            severity="critical")
        unclaimed = await pg_db.upsert_alert_history(
            acct, "fault", "veh-b", "202", alert_subkey="e:SPN10",
            severity="critical")
        warning = await pg_db.upsert_alert_history(
            acct, "fault", "veh-c", "203", alert_subkey="e:SPN11",
            severity="warning")
        await pg_db.claim_alert(acct, uid, int(claimed["id"]))

        rows = await pg_db.get_active_unacked_history_for_reescalation(
            acct, "9999-01-01T00:00:00", max_attempts=4)
        ids = {int(r["id"]) for r in rows}
        assert int(unclaimed["id"]) in ids          # still searching for an owner
        assert int(claimed["id"]) not in ids        # has one — silent
        assert int(warning["id"]) not in ids        # warnings never page

    async def test_a_claim_still_never_touches_acknowledgment(self, pg_db):
        """Same invariant as seen: the ledger records hands, the ack
        columns stay whatever they were — legacy acks and auto-resolve
        keep their own meaning untouched."""
        acct, uid, aid = await _seed(pg_db, tg=9964)
        await pg_db.claim_alert(acct, uid, aid)
        cur = await pg_db._db.execute(
            "SELECT status, acknowledged_at, acknowledged_by "
            "  FROM alert_history WHERE id = ?", (aid,))
        row = dict(await cur.fetchone())
        assert row["status"] == "active" and not row["acknowledged_at"]
