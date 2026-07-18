"""/safety/events explicit-end windows (``end=YYYY-MM-DD``).

The picker's two-click range mode is only honest if the API actually
filters to [end-days, end] — these tests pin that: precise inclusion
on both edges, future-end clamping to today, and the 422 guard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


def _iso_days_ago(n: int, hour: int = 12) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=n)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


@pytest_asyncio.fixture
async def events_app(pg_db, monkeypatch):
    db = pg_db
    acct = await db.create_account("Events Window Co")
    owner = await db.create_user(920001, acct.id, role=Role.OWNER)

    # Warehouse reads ON so the endpoint reads the seeded rows instead
    # of falling back to live Samsara.
    import infra.config as config
    monkeypatch.setattr(config, "WAREHOUSE_READS_ENABLED", True, raising=False)

    await db.insert_safety_events(acct.id, [
        {"samsara_event_id": "evt-old", "event_type": "harshBrake",
         "occurred_at": _iso_days_ago(10), "vehicle_name": "101", "company_code": ""},
        {"samsara_event_id": "evt-mid", "event_type": "harshBrake",
         "occurred_at": _iso_days_ago(5), "vehicle_name": "102", "company_code": ""},
        {"samsara_event_id": "evt-new", "event_type": "harshBrake",
         "occurred_at": _iso_days_ago(1), "vehicle_name": "103", "company_code": ""},
    ])

    import infra.platform as _cp
    old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    transport = ASGITransport(app=create_api())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client,
            "hdr": {"Authorization": f"Bearer {create_jwt(owner.telegram_id, acct.id, 'owner')}"},
        }
    _cp._db = old


def _ids(body: dict) -> set[str]:
    return {e["event_id"] for e in body["events"]}


@pytest.mark.asyncio
async def test_default_window_ends_today(events_app):
    s = events_app
    r = await s["client"].get("/api/safety/events?days=90", headers=s["hdr"])
    assert r.status_code == 200
    assert _ids(r.json()) == {"evt-old", "evt-mid", "evt-new"}
    assert r.json()["end"] is None


@pytest.mark.asyncio
async def test_explicit_end_filters_both_edges(events_app):
    s = events_app
    # Window: 5 days ending 6 days ago → [11d ago .. 6d ago] — contains
    # ONLY the 10-days-ago event; both newer rows are outside.
    end = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
    r = await s["client"].get(
        f"/api/safety/events?days=5&end={end}", headers=s["hdr"],
    )
    assert r.status_code == 200
    assert _ids(r.json()) == {"evt-old"}
    assert r.json()["end"] == end

    # Shift the window to cover the middle event only.
    end2 = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    r = await s["client"].get(
        f"/api/safety/events?days=4&end={end2}", headers=s["hdr"],
    )
    assert _ids(r.json()) == {"evt-mid"}


@pytest.mark.asyncio
async def test_future_end_clamps_to_today_and_old_end_422s(events_app):
    s = events_app
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    r = await s["client"].get(
        f"/api/safety/events?days=90&end={future}", headers=s["hdr"],
    )
    assert r.status_code == 200
    assert _ids(r.json()) == {"evt-old", "evt-mid", "evt-new"}
    assert r.json()["end"] is None            # clamped = default window

    ancient = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    r = await s["client"].get(
        f"/api/safety/events?days=7&end={ancient}", headers=s["hdr"],
    )
    assert r.status_code == 422
