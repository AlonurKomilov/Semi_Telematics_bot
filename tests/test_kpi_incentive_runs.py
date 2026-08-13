"""Incentive runs: generation → adjustment → exception → finalize.

The lifecycle the customer's settlement sheet lives through, against a
real tenant DB.  Loads are monkeypatched (their own suite owns loads IO);
everything downstream — snapshot, grouping, recompute, cap enforcement,
immutability — runs for real.
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

PERIOD = {"period_start": "2026-07-01", "period_end": "2026-07-28"}

# Two dispatchers, two companies, three trucks — enough to prove
# grouping, windows and the no-target row.
LOADS = [
    # Anna / OSY / 225 — two loads, strong RPM, window 07-03..07-20
    {"status": "delivered", "dispatcher_user_id": 11,
     "dispatcher_name": "Anna", "company_code": "OSY",
     "vehicle_unit": "225", "total_rate": 9_000, "loaded_miles": 4_000,
     "empty_miles": 200, "pickup_date": "2026-07-03",
     "delivery_date": "2026-07-06"},
    {"status": "delivered", "dispatcher_user_id": 11,
     "dispatcher_name": "Anna", "company_code": "OSY",
     "vehicle_unit": "225", "total_rate": 8_000, "loaded_miles": 3_600,
     "empty_miles": 300, "pickup_date": "2026-07-15",
     "delivery_date": "2026-07-20"},
    # Anna / OSY / 226 — cancelled: must not count
    {"status": "canceled", "dispatcher_user_id": 11,
     "dispatcher_name": "Anna", "company_code": "OSY",
     "vehicle_unit": "226", "total_rate": 5_000, "loaded_miles": 2_000,
     "empty_miles": 100, "pickup_date": "2026-07-05",
     "delivery_date": "2026-07-07"},
    # Boris / NOTGT / 301 — company WITHOUT a target
    {"status": "delivered", "dispatcher_user_id": 12,
     "dispatcher_name": "Boris", "company_code": "NOTGT",
     "vehicle_unit": "301", "total_rate": 7_000, "loaded_miles": 3_000,
     "empty_miles": 200, "pickup_date": "2026-07-10",
     "delivery_date": "2026-07-12"},
]

LADDER = {
    "model": "ladder",
    "calc_cadence": "weekly",
    "exception_cap_pct": 2.0,
    "floor_weekly_gross": 7000.0,
    "floor_rpm": 1.9,
    "tiers": [
        {"min_rpm": 2.0, "pct": 1.0},
        {"requires_target": True, "pct": 1.0},
        {"requires_target": True, "min_rpm": 2.0, "pct": 1.5},
    ],
}


@pytest_asyncio.fixture
async def seeded(pg_db, monkeypatch):
    db = pg_db
    acct = await db.create_account("Runs Co")
    owner = await db.create_user(9701, acct.id, role=Role.OWNER)
    disp = await db.create_user(9702, acct.id, role=Role.DISPATCHER)
    osy = await db.add_company(acct.id, "OSY", display_name="OSY LLC")

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()

    async def _loads(account_id, **kw):
        return [dict(l) for l in LOADS]

    from features.kpi.dispatch import runs as runs_mod
    monkeypatch.setattr(runs_mod.loads_service, "get_loads", _loads)

    yield {
        "app": app, "db": db, "acct": acct, "osy": osy,
        "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "disp": create_jwt(disp.telegram_id, acct.id, "dispatcher"),
    }
    _cp._db = _old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _configure(c, seeded):
    r = await c.put("/api/kpi/config/incentives", json=LADDER,
                    headers=_h(seeded["owner"]))
    assert r.status_code == 200, r.text
    r = await c.put("/api/kpi/config/incentives/targets",
                    json={"targets": {str(seeded["osy"].id): 8000.0}},
                    headers=_h(seeded["owner"]))
    assert r.status_code == 200, r.text


class TestLifecycle:
    async def test_unconfigured_account_cannot_create_a_run(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                             headers=_h(seeded["owner"]))
            assert r.status_code == 422
            assert "not configured" in r.json()["detail"]

    async def test_generation_groups_windows_and_computes(self, seeded):
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            r = await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                             headers=_h(seeded["owner"]))
            assert r.status_code == 201, r.text
            run = r.json()
            rows = {x["vehicle_unit"]: x for x in run["rows"]}

            # Cancelled load's truck 226 never became a row.
            assert set(rows) == {"225", "301"}

            t225 = rows["225"]
            # Window from load dates, clamped to the period; 18 days incl.
            assert (t225["window_start"], t225["window_end"]) == (
                "2026-07-03", "2026-07-20")
            assert t225["total_days"] == 18
            # 17,000 gross / 8,100 mi -> rpm 2.10; target 8000/7*18 =
            # 20,571.43 -> NOT met; ladder row 1 (rpm>=2, no target) -> 1%.
            assert t225["base_gross"] == 17_000
            assert t225["rpm"] == 2.10
            assert t225["adjusted_target"] == 20_571.43
            assert t225["pct"] == 1.0
            assert t225["kpi_dollars"] == 170.00
            assert t225["confirmed_dollars"] == 170.00

            # No target for NOTGT: pays 0, target stays NULL — visible.
            t301 = rows["301"]
            assert t301["weekly_target"] is None
            assert t301["pct"] == 0.0

            assert run["payouts"] == {"Anna": 170.00, "Boris": 0.00}

    async def test_inactive_days_lower_the_bar_and_recompute(self, seeded):
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            row = next(x for x in run["rows"] if x["vehicle_unit"] == "225")
            # 4 inactive days: 14 active -> target 16,000; 17,000 >= it,
            # rpm 2.10 -> the requires_target 1.5% tier now applies.
            r = await c.patch(
                f"/api/kpi/dispatch/runs/{run['id']}/rows/{row['id']}",
                json={"inactive_days": 4, "inactive_reason": "repair"},
                headers=_h(seeded["owner"]))
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["adjusted_target"] == 16_000.00
            assert out["pct"] == 1.5
            assert out["confirmed_dollars"] == 255.00

    async def test_exception_needs_reason_and_respects_the_cap(self, seeded):
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            row = next(x for x in run["rows"] if x["vehicle_unit"] == "225")
            url = (f"/api/kpi/dispatch/runs/{run['id']}"
                   f"/rows/{row['id']}/exception")

            r = await c.post(url, json={"override_pct": 1.5, "reason": ""},
                             headers=_h(seeded["owner"]))
            assert r.status_code == 422
            assert "reason" in r.json()["detail"]

            r = await c.post(url, json={"override_pct": 2.5,
                                        "reason": "new MC"},
                             headers=_h(seeded["owner"]))
            assert r.status_code == 422
            assert "cap" in r.json()["detail"]

            r = await c.post(url, json={"override_pct": 1.5,
                                        "reason": "new MC"},
                             headers=_h(seeded["owner"]))
            assert r.status_code == 200, r.text
            assert r.json()["confirmed_dollars"] == 255.00   # 17,000 x 1.5%

    async def test_finalized_runs_are_never_repriced(self, seeded):
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            row = next(x for x in run["rows"] if x["vehicle_unit"] == "225")
            r = await c.post(f"/api/kpi/dispatch/runs/{run['id']}/finalize",
                             headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.json()["status"] == "finalized"

            r = await c.patch(
                f"/api/kpi/dispatch/runs/{run['id']}/rows/{row['id']}",
                json={"inactive_days": 3},
                headers=_h(seeded["owner"]))
            assert r.status_code == 409
            assert "never re-priced" in r.json()["detail"]

    async def test_the_snapshot_outlives_a_config_change(self, seeded):
        """The contract: a draft computed under the old rules keeps them.
        The live config changes to a 5% flat tier; the run's rows still
        recompute under the SNAPSHOT's 1%/1.5% ladder."""
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            row = next(x for x in run["rows"] if x["vehicle_unit"] == "225")

            await c.put("/api/kpi/config/incentives",
                        json={**LADDER, "tiers": [{"pct": 5.0}]},
                        headers=_h(seeded["owner"]))
            r = await c.patch(
                f"/api/kpi/dispatch/runs/{run['id']}/rows/{row['id']}",
                json={"inactive_days": 4},
                headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.json()["pct"] == 1.5      # snapshot, not the new 5%

    async def test_dispatchers_cannot_reach_runs(self, seeded):
        """Payout amounts are compensation data — can_kpi (which a
        dispatcher may hold) must not open this; can_kpi_incentives
        does, and a dispatcher does not carry it by seed."""
        async with await _client(seeded["app"]) as c:
            r = await c.get("/api/kpi/dispatch/runs",
                            headers=_h(seeded["disp"]))
            assert r.status_code == 403

    async def test_a_zero_row_names_its_reason(self, seeded):
        """UX rule: every zero in a money column carries its reason.
        NOTGT's truck pays 0 because its company has no bar."""
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            t301 = next(x for x in run["rows"] if x["vehicle_unit"] == "301")
            assert t301["zero_reason"] == "no_target"
            t225 = next(x for x in run["rows"] if x["vehicle_unit"] == "225")
            assert t225["zero_reason"] == ""        # it pays — no reason

    async def test_run_loads_lists_each_rows_loads_and_reports_drift(self, seeded):
        """The board view's data: loads keyed by row id, cancelled
        excluded; a load edited/added AFTER generation shows as drift
        instead of silently disagreeing with the snapshot."""
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            r = await c.get(f"/api/kpi/dispatch/runs/{run['id']}/loads",
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200, r.text
            body = r.json()
            row225 = next(x for x in run["rows"] if x["vehicle_unit"] == "225")
            assert len(body["rows"][str(row225["id"])]) == 2   # cancelled 226 absent
            assert body["drift"] == []
            assert body["unmatched_loads"] == 0

            LOADS.append({
                "status": "delivered", "dispatcher_user_id": 11,
                "dispatcher_name": "Anna", "company_code": "OSY",
                "vehicle_unit": "225", "total_rate": 1_000,
                "loaded_miles": 300, "empty_miles": 0,
                "pickup_date": "2026-07-22", "delivery_date": "2026-07-23",
            })
            try:
                r = await c.get(f"/api/kpi/dispatch/runs/{run['id']}/loads",
                                headers=_h(seeded["owner"]))
                assert row225["id"] in r.json()["drift"]
            finally:
                LOADS.pop()

    async def test_day_marks_derive_the_count_and_respect_the_window(self, seeded):
        """The board's per-day inactive marks: the count and summary
        reason DERIVE from the list, an out-of-window date is refused,
        and the typed-number path clears the list (coarse tool wins)."""
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            row = next(x for x in run["rows"] if x["vehicle_unit"] == "225")
            url = f"/api/kpi/dispatch/runs/{run['id']}/rows/{row['id']}"

            marks = [{"date": f"2026-07-{d:02d}", "reason": "repair"}
                     for d in (5, 6, 7)] + [{"date": "2026-07-08",
                                             "reason": "home time"}]
            r = await c.patch(url, json={"inactive_dates": marks},
                              headers=_h(seeded["owner"]))
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["inactive_days"] == 4
            assert out["inactive_reason"] == "repair, home time"
            assert [m["date"] for m in out["inactive_dates"]][:2] == [
                "2026-07-05", "2026-07-06"]
            # Same math as typing 4: 14 active days -> 16,000 -> 1.5%.
            assert out["adjusted_target"] == 16_000.00
            assert out["pct"] == 1.5

            r = await c.patch(url, json={"inactive_dates": [
                {"date": "2026-09-01", "reason": "repair"}]},
                headers=_h(seeded["owner"]))
            assert r.status_code == 422
            assert "outside the row's window" in r.json()["detail"]

            r = await c.patch(url, json={"inactive_days": 2},
                              headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.json()["inactive_dates"] == []
            assert r.json()["inactive_days"] == 2

    async def test_csv_export_carries_the_sheet_and_the_totals(self, seeded):
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            r = await c.get(f"/api/kpi/dispatch/runs/{run['id']}/export.csv",
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/csv")
            assert "draft" in r.headers["content-disposition"]
            body = r.text
            assert "Anna" in body and "225" in body
            assert "TOTAL" in body and "170.0" in body

    async def test_monthly_payouts_roll_up_finalized_runs_only(self, seeded):
        """Weekly calc -> monthly payout: a run belongs to the month its
        period ENDS in, and drafts never count toward a payout total."""
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()

            r = await c.get("/api/kpi/dispatch/payouts?month=2026-07",
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.json()["payouts"] == {}          # still a draft

            await c.post(f"/api/kpi/dispatch/runs/{run['id']}/finalize",
                         headers=_h(seeded["owner"]))
            r = await c.get("/api/kpi/dispatch/payouts?month=2026-07",
                            headers=_h(seeded["owner"]))
            body = r.json()
            assert body["payouts"]["Anna"] == 170.00
            assert body["total"] == 170.00
            assert [x["id"] for x in body["runs"]] == [run["id"]]

            r = await c.get("/api/kpi/dispatch/payouts?month=2026-13",
                            headers=_h(seeded["owner"]))
            assert r.status_code == 422

    async def test_me_shows_only_my_rows_and_only_finalized(self, seeded):
        """Self-scoped: plain auth, own rows, finalized runs only —
        a dispatcher never sees a draft number that could still drop."""
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()

            # Anna's loads carry dispatcher_user_id 11 — link the
            # dispatcher login to that id via its user row.
            disp_user = await seeded["db"].get_user_by_telegram_id(9702)
            uid = disp_user.id
            for l in LOADS:
                if l["dispatcher_user_id"] == 11:
                    l["dispatcher_user_id"] = uid
            try:
                # Draft: nothing visible yet.
                r = await c.get("/api/kpi/dispatch/me",
                                headers=_h(seeded["disp"]))
                assert r.status_code == 200
                assert r.json()["runs"] == []

                await c.delete(f"/api/kpi/dispatch/runs/{run['id']}",
                               headers=_h(seeded["owner"]))
                run2 = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                     headers=_h(seeded["owner"]))).json()
                await c.post(
                    f"/api/kpi/dispatch/runs/{run2['id']}/finalize",
                    headers=_h(seeded["owner"]))
                r = await c.get("/api/kpi/dispatch/me",
                                headers=_h(seeded["disp"]))
                body = r.json()
                assert len(body["runs"]) == 1
                mine = body["runs"][0]
                assert mine["total"] == 170.00
                assert {x["vehicle_unit"] for x in mine["rows"]} == {"225"}
            finally:
                for l in LOADS:
                    if l["dispatcher_user_id"] == uid:
                        l["dispatcher_user_id"] = 11

    async def test_a_draft_can_be_discarded_a_paid_record_cannot(self, seeded):
        async with await _client(seeded["app"]) as c:
            await _configure(c, seeded)
            run = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                headers=_h(seeded["owner"]))).json()
            r = await c.delete(f"/api/kpi/dispatch/runs/{run['id']}",
                               headers=_h(seeded["owner"]))
            assert r.status_code == 204
            r = await c.get(f"/api/kpi/dispatch/runs/{run['id']}",
                            headers=_h(seeded["owner"]))
            assert r.status_code == 404

            run2 = (await c.post("/api/kpi/dispatch/runs", json=PERIOD,
                                 headers=_h(seeded["owner"]))).json()
            await c.post(f"/api/kpi/dispatch/runs/{run2['id']}/finalize",
                         headers=_h(seeded["owner"]))
            r = await c.delete(f"/api/kpi/dispatch/runs/{run2['id']}",
                               headers=_h(seeded["owner"]))
            assert r.status_code == 409
            assert "cannot be discarded" in r.json()["detail"]
