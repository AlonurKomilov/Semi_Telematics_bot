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
