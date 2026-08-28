"""Tests for ``GET /api/alerts/pending/by-type``.

These counters sit above the alert board, so their whole job is to stay
true while the board below is filtered.  What must hold:

  * the counts are TOTALS — unaffected by any board filter, and
    unaffected by age (an old still-open alert is still open);
  * ``fault`` and ``faults`` are aliases and both get counted, so the
    "Open faults" card can sum them;
  * a type the active view may not see is OMITTED, never returned as 0
    ("nothing is wrong" is a different claim from "not yours to see");
  * driver isolation and persona scoping match /pending/count exactly.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    database = pg_db

    acct = await database.create_account("ByType Co")
    await database.add_company(acct.id, "CO", "key_co", "ByType Co")
    owner = await database.create_user(8001, acct.id, role=Role.OWNER)
    driver = await database.create_user(8002, acct.id, role=Role.DRIVER)

    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="T-DRIVER",
        assigned_by=0, is_primary=True,
    )

    # Fleet persona types: 3 faults (two under the 'fault' key, one under
    # the 'faults' alias) + 1 health.  Plus a 'fuel' alert, which belongs
    # to the DISPATCHER persona — it must not appear for a Fleet view.
    for vid, name, atype in (
        ("V1", "T-DRIVER", "fault"),
        ("V2", "T-OTHER", "fault"),
        ("V3", "T-OTHER", "faults"),
        ("V4", "T-OTHER", "health"),
        ("V5", "T-OTHER", "fuel"),
    ):
        await database.upsert_alert_history(
            account_id=acct.id, alert_type=atype,
            vehicle_id=vid, vehicle_name=name,
        )

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }

    _cp._db = _old_db


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _get(app, token, view=None):
    headers = _h(token)
    if view:
        headers["X-View-As"] = view
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/api/alerts/pending/by-type", headers=headers)
        assert r.status_code == 200
        return r.json()["counts"]


class TestByTypeCounts:
    async def test_fleet_view_counts_both_fault_aliases(self, seeded):
        """``fault`` and ``faults`` are the same concept on two wire keys.
        Both must be reported so the card can sum them to 3 — dropping
        either would understate open faults."""
        counts = await _get(seeded["app"], seeded["token_owner"], "fleet")
        assert counts.get("fault") == 2
        assert counts.get("faults") == 1
        assert counts.get("health") == 1

    async def test_type_outside_the_persona_is_omitted_not_zero(self, seeded):
        """'fuel' routes to Dispatcher.  For a Fleet view the key must be
        ABSENT — a 0 would render a calm "no low fuel" card, which is a
        claim this view has no standing to make."""
        counts = await _get(seeded["app"], seeded["token_owner"], "fleet")
        assert "fuel" not in counts
        # ...and the Dispatcher view, which owns it, does report it.
        dispatcher = await _get(seeded["app"], seeded["token_owner"], "dispatcher")
        assert dispatcher.get("fuel") == 1
        assert "fault" not in dispatcher

    async def test_owner_default_view_has_no_operational_counters(self, seeded):
        """Owner's own persona owns only the cross-cutting types (system,
        reescalate) — they review escalations rather than triaging trucks.
        Those come back as honest zeros, but NONE of the operational types
        appear, so the hero strip has no card to draw and renders nothing.
        """
        counts = await _get(seeded["app"], seeded["token_owner"])
        assert set(counts) == {"system", "reescalate"}
        assert all(v == 0 for v in counts.values())
        # The three operational cards key off these — all absent.
        for operational in ("fault", "faults", "health", "fuel"):
            assert operational not in counts

    async def test_driver_sees_only_their_own_truck(self, seeded):
        """Driver scope ignores persona (they get everything about their
        own truck) but must not leak another truck's alerts."""
        counts = await _get(seeded["app"], seeded["token_driver"])
        assert counts == {"fault": 1}

    async def test_counts_ignore_age(self, seeded):
        """An alert whose first_seen is ancient is still OPEN.  The board's
        date window never bounds the open queue, and these totals must
        agree with it."""
        db, acct = seeded["db"], seeded["acct"]
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        await db._db.execute(
            "UPDATE alert_history SET first_seen = ?, last_seen = ? "
            "WHERE account_id = ? AND vehicle_id = ?",
            (old, old, acct.id, "V1"),
        )
        counts = await _get(seeded["app"], seeded["token_owner"], "fleet")
        assert counts.get("fault") == 2      # the 400-day-old row still counts

    async def test_acknowledging_removes_it_from_the_count(self, seeded):
        db, acct = seeded["db"], seeded["acct"]
        rows = await db.get_active_alert_history_for_account(acct.id)
        target = next(r for r in rows if r["alert_type"] == "health")
        await db.acknowledge_alert_history(target["id"], 8001, account_id=acct.id)

        counts = await _get(seeded["app"], seeded["token_owner"], "fleet")
        assert counts.get("health") == 0     # counted, and now genuinely zero
        assert counts.get("fault") == 2      # untouched

    async def test_requires_auth(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get("/api/alerts/pending/by-type")
            assert r.status_code in (401, 403, 422)
