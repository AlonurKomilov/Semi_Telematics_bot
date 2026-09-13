"""Guard: the ack agrees with what the read just showed.

get_alert_history admits rows by the identity ladder. acknowledge_alerts
— propose AND executor — compared lowercased NAMES. The two disagreed in
both directions:

* RENAME. A driver assigned "229" is shown "alert 4821 — fault on
  229 Idris Ahmed" (the provider renamed the truck; the ladder's rung 2
  matched on the provider id). They say "acknowledge it", and the tool
  answers "1 of these alerts is on vehicle(s) outside your access" —
  about their own truck, on an alert it had just listed.
* TWIN. Two companies each run a "103". A caller scoped to one of them
  acks by name, and the executor's SQL `LOWER(vehicle_name) IN ('103')`
  matches BOTH rows, clearing the other company's alert.

The propose path uses the same shared helper as the read now, and the
SQL carries the ladder's precedence: a row that HAS a provider id is
decided by it, and only a row without one falls back to the name.
"""

import pytest


async def _acct(pg_db):
    a = await pg_db.create_account("Ack Ladder Co")
    return a.id


async def _alert(pg_db, account_id, name, vehicle_id=""):
    """An active alert_history row. ``vehicle_id`` is the PROVIDER id —
    the rung the read path decides by and the ack path ignored."""
    row = await pg_db.upsert_alert_history(
        account_id, "fault", vehicle_id, name,
        last_detail="stop lamp", severity="critical",
    )
    return int(row["id"])


class TestTheAckSqlFollowsTheLadder:
    """Driven straight at the storage method — that is where the twin
    was cleared, and where a stale proposal is re-checked."""

    @pytest.mark.asyncio
    async def test_a_row_with_a_provider_id_is_decided_by_it(self, pg_db):
        acct = await _acct(pg_db)
        aid = await _alert(pg_db, acct, "103", vehicle_id="sam_99")
        # The caller owns sam_42's "103"; this row is the twin.
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct,
            allowed_vehicle_names=["103"], allowed_vehicle_ids=["sam_42"])
        assert got is None, (
            "name matching cleared the other company's alert — the row "
            "carries a provider id and it says this is not our truck"
        )

    @pytest.mark.asyncio
    async def test_the_callers_own_row_is_cleared(self, pg_db):
        acct = await _acct(pg_db)
        aid = await _alert(pg_db, acct, "103", vehicle_id="sam_42")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct,
            allowed_vehicle_names=["103"], allowed_vehicle_ids=["sam_42"])
        assert got is not None

    @pytest.mark.asyncio
    async def test_a_renamed_truck_is_still_the_callers_own(self, pg_db):
        """The label changed; the provider id did not."""
        acct = await _acct(pg_db)
        aid = await _alert(pg_db, acct, "229 Idris Ahmed", vehicle_id="sam_60")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct,
            allowed_vehicle_names=["229"], allowed_vehicle_ids=["sam_60"])
        assert got is not None, (
            "the ack was refused on an alert the read had just shown"
        )

    @pytest.mark.asyncio
    async def test_a_row_without_a_provider_id_falls_back_to_the_name(self, pg_db):
        acct = await _acct(pg_db)
        aid = await _alert(pg_db, acct, "103", vehicle_id="")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct,
            allowed_vehicle_names=["103"], allowed_vehicle_ids=["sam_42"])
        assert got is not None, (
            "a row with no provider id has only its name to be judged by"
        )

    @pytest.mark.asyncio
    async def test_scoped_to_nothing_still_fails_closed(self, pg_db):
        acct = await _acct(pg_db)
        aid = await _alert(pg_db, acct, "103", vehicle_id="sam_42")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct, allowed_vehicle_names=[], allowed_vehicle_ids=[])
        assert got is None
