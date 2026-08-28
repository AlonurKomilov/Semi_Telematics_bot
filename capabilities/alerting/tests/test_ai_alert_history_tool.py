"""The get_alert_history AI tool must read the SAME table the dashboard's
Alerts page reads (``alert_history``), not the ack-tracking table.

Pins the HIGH bug from the tool-accuracy audit: the old read hit
``alert_acknowledgments``, which has no severity / occurrence_count /
first_seen / location columns — so 'show critical alerts' CRASHED the
tool (unknown column in WHERE), and every listed alert claimed severity
"info" no matter how critical it really was.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.alerting.ai_tool import get_alert_history
from capabilities.permissions.roles import Role


async def _seed(pg_db):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account("Alert Tool Co")
    await pg_db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id,
        role=Role.OWNER,
        display_name="Owner",
    )
    await pg_db.upsert_alert_history(
        acct.id, "fault", "v-228", "Truck 228",
        last_detail="Engine coolant temp high", severity="critical",
        location="I-94, Battle Creek MI",
    )
    await pg_db.upsert_alert_history(
        acct.id, "fuel", "v-233", "Truck 233",
        last_detail="Fuel below 15%", severity="warning",
    )
    return acct.id


class TestAlertHistoryTool:
    async def test_severity_filter_works_instead_of_crashing(self, pg_db):
        acct = await _seed(pg_db)
        res = await get_alert_history(
            {"severity": "critical"}, None, account_id=acct, db=pg_db,
        )
        assert "error" not in res
        assert res["count"] == 1
        a = res["alerts"][0]
        assert a["vehicle"] == "Truck 228"
        assert a["severity"] == "critical"          # not the old always-"info"
        assert a["occurrence_count"] == 1
        assert a["location"] == "I-94, Battle Creek MI"
        assert a["detail"] == "Engine coolant temp high"
        assert a["first_seen"]                       # real column, not blank

    async def test_unfiltered_lists_all_severities(self, pg_db):
        acct = await _seed(pg_db)
        res = await get_alert_history({}, None, account_id=acct, db=pg_db)
        sevs = sorted(a["severity"] for a in res["alerts"])
        assert sevs == ["critical", "warning"]

    async def test_status_active_and_acknowledged_split(self, pg_db):
        acct = await _seed(pg_db)
        # Acknowledge the fuel alert (history-side status flip).
        await pg_db._db.execute(
            "UPDATE alert_history SET status = 'acknowledged'"
            " WHERE account_id = ? AND alert_type = 'fuel'", (acct,),
        )
        await pg_db._db.commit()

        active = await get_alert_history(
            {"status": "active"}, None, account_id=acct, db=pg_db,
        )
        acked = await get_alert_history(
            {"status": "acknowledged"}, None, account_id=acct, db=pg_db,
        )
        assert [a["vehicle"] for a in active["alerts"]] == ["Truck 228"]
        assert [a["vehicle"] for a in acked["alerts"]] == ["Truck 233"]
