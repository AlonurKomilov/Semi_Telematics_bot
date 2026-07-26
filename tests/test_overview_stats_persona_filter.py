"""Integration test for the persona-aware pending_alerts count on
/api/overview/stats.

Bug observed in production: every operational role (Safety, Dispatcher,
Fleet, HR) saw the same account-wide count on their Overview hero
card ("7,320 pending alerts" for a Safety user whose actual triage
workload was events + camera in the dozens).  Counts are now filtered
by the role's persona-relevant alert_types; Owner / Admin remain on
the cross-cutting view.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def overview_app(pg_db, monkeypatch):
    db = pg_db
    acct = await db.create_account("Persona-Filter Test Co")
    co = await db.add_company(acct.id, "PFT", "samsara_test_key", "PFT")
    # One user per operational persona so we can mint a JWT for each.
    owner = await db.create_user(950001, acct.id, role=Role.OWNER)
    admin = await db.create_user(950002, acct.id, role=Role.ADMIN)
    safety = await db.create_user(950003, acct.id, role=Role.SAFETY)
    dispatcher = await db.create_user(950004, acct.id, role=Role.DISPATCHER)
    fleet = await db.create_user(950005, acct.id, role=Role.FLEET)
    hr = await db.create_user(950006, acct.id, role=Role.HR)

    tokens = {
        "owner":      create_jwt(owner.telegram_id, acct.id, "owner"),
        "admin":      create_jwt(admin.telegram_id, acct.id, "admin"),
        "safety":     create_jwt(safety.telegram_id, acct.id, "safety"),
        "dispatcher": create_jwt(dispatcher.telegram_id, acct.id, "dispatcher"),
        "fleet":      create_jwt(fleet.telegram_id, acct.id, "fleet"),
        "hr":         create_jwt(hr.telegram_id, acct.id, "hr"),
    }

    # Stub out Samsara — no live fleet in tests.
    async def _empty_fleet(_account_id, company=None):
        return []
    import features.overview.router as fleet_mod
    monkeypatch.setattr(fleet_mod, "get_vehicles_for_map", _empty_fleet)

    # Per-process cache leak across tests — drain before each.
    fleet_mod._stats_cache.clear()

    # Seed alerts of each operational type + the owner-admin's
    # cross-cutting digest types so every role's strict-bound persona
    # filter has something to count.  Counts chosen distinct:
    #   2 system, 1 reescalate    (owner_admin persona)  = 3
    #   2 faults, 3 health        (fleet persona)        = 5
    #   4 events, 1 camera        (safety persona)       = 5
    #   2 geofence, 1 parking,
    #   3 fuel                    (dispatcher persona)   = 6
    #   1 doc_expiry              (hr persona)           = 1
    # Account-wide total = 20
    counts = {
        "system": 2, "reescalate": 1,
        "faults": 2, "health": 3,
        "events": 4, "camera": 1,
        "geofence": 2, "parking": 1, "fuel": 3,
        "doc_expiry": 1,
    }
    msg_id = 1000
    for at, n in counts.items():
        for i in range(n):
            # /overview/stats counts LOGICAL alerts from alert_history (one
            # row per truck+problem) — not delivery receipts, which are one
            # row per RECIPIENT and so inflated the card.  send_alert always
            # upserts history BEFORE creating any ack row, so the seed has to
            # do the same or it builds a shape production never produces.
            await db.upsert_alert_history(
                account_id=acct.id, alert_type=at,
                vehicle_id=f"v-{at}-{i}", vehicle_name=f"Truck {at}{i}",
                severity="warning",
            )
            await db.create_alert_ack(
                account_id=acct.id, alert_type=at,
                vehicle_id=f"v-{at}-{i}", vehicle_name=f"Truck {at}{i}",
                alert_key=f"{at}:{i}",
                message_id=msg_id, chat_id=1, sent_to=owner.telegram_id,
                severity="warning",
            )
            msg_id += 1

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client,
            "acct": acct, "company": co,
            "tokens": tokens,
            "counts": counts,
        }
    _cp._db = _old


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_owner_default_view_sees_only_owner_admin_alerts(overview_app):
    """Strict binding: Owner's default view (no X-View-As) has the
    owner_admin persona whose alert types are system/reescalate only.
    Owner sees ONLY those — NOT faults / events / parking etc.

    This is the executive 'all clear' signal: when the count is 0,
    there are no system-level escalations to triage.  Owner does
    operational triage by switching view to Fleet/Safety/Dispatch;
    see test_owner_with_view_as_fleet."""
    s = overview_app
    expected = s["counts"]["system"] + s["counts"]["reescalate"]
    r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["owner"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pending_alerts"] == expected
    # Critically NOT the account-wide total.
    assert body["pending_alerts"] != sum(s["counts"].values())


@pytest.mark.asyncio
async def test_admin_default_view_sees_only_owner_admin_alerts(overview_app):
    """Admin behaves like Owner under strict binding."""
    s = overview_app
    expected = s["counts"]["system"] + s["counts"]["reescalate"]
    r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["admin"]),
    )
    assert r.json()["pending_alerts"] == expected


@pytest.mark.asyncio
async def test_owner_with_view_as_fleet_sees_fleet_count(overview_app):
    """Owner switches view to Fleet → SPA sends X-View-As: fleet →
    backend resolves the active view as Fleet and returns Fleet's
    persona-filtered count.  Owner's underlying JWT role is still
    'owner'; only the active view changes."""
    s = overview_app
    expected = s["counts"]["faults"] + s["counts"]["health"]
    r = await s["client"].get(
        "/api/overview/stats",
        headers={**_hdr(s["tokens"]["owner"]), "X-View-As": "fleet"},
    )
    body = r.json()
    assert body["pending_alerts"] == expected


@pytest.mark.asyncio
async def test_owner_with_view_as_safety_sees_safety_count(overview_app):
    """Same Owner, different active view = different count."""
    s = overview_app
    expected = s["counts"]["events"] + s["counts"]["camera"]
    r = await s["client"].get(
        "/api/overview/stats",
        headers={**_hdr(s["tokens"]["owner"]), "X-View-As": "safety"},
    )
    assert r.json()["pending_alerts"] == expected


@pytest.mark.asyncio
async def test_non_switchable_role_view_as_is_ignored(overview_app):
    """A Safety user sending X-View-As: fleet should NOT see Fleet's
    count — only owner/admin can preview as another role.  Backend
    silently ignores the header (no 403; routine count endpoints
    shouldn't reject on a stale or spoofed header)."""
    s = overview_app
    safety_expected = s["counts"]["events"] + s["counts"]["camera"]
    r = await s["client"].get(
        "/api/overview/stats",
        headers={**_hdr(s["tokens"]["safety"]), "X-View-As": "fleet"},
    )
    assert r.json()["pending_alerts"] == safety_expected


@pytest.mark.asyncio
async def test_safety_sees_only_safety_persona_alerts(overview_app):
    """Safety persona = events + camera + 'event' alias.  The seeded
    rows include 4 events + 1 camera = 5."""
    s = overview_app
    expected = s["counts"]["events"] + s["counts"]["camera"]
    r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["safety"]),
    )
    body = r.json()
    assert body["pending_alerts"] == expected
    # Critically NOT the account-wide total.
    assert body["pending_alerts"] != sum(s["counts"].values())


@pytest.mark.asyncio
async def test_dispatcher_sees_only_dispatcher_persona_alerts(overview_app):
    """Dispatcher = geofence + parking + fuel."""
    s = overview_app
    expected = s["counts"]["geofence"] + s["counts"]["parking"] + s["counts"]["fuel"]
    r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["dispatcher"]),
    )
    assert r.json()["pending_alerts"] == expected


@pytest.mark.asyncio
async def test_fleet_sees_only_fleet_persona_alerts(overview_app):
    """Fleet = faults + health + scorecard + maintenance + samsara_sync."""
    s = overview_app
    expected = s["counts"]["faults"] + s["counts"]["health"]
    r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["fleet"]),
    )
    assert r.json()["pending_alerts"] == expected


@pytest.mark.asyncio
async def test_hr_sees_only_hr_persona_alerts(overview_app):
    """HR = documents + doc_expiry."""
    s = overview_app
    expected = s["counts"]["doc_expiry"]
    r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["hr"]),
    )
    assert r.json()["pending_alerts"] == expected


@pytest.mark.asyncio
async def test_persona_filter_does_not_change_fleet_counts(overview_app):
    """The persona filter only narrows pending_alerts.  faults /
    low_fuel / unsafe_parking / maintenance_due (the other KPI fields)
    have their own permission gates that already produced sensible
    role-specific values; the filter must not regress them.

    Note: under strict binding, Owner OMITS pending_alerts entirely
    (no operational types) while Safety has it.  The shared invariant
    is that the ``vehicles`` substructure (vehicle counts) is
    identical — the block is ROLE-NEUTRAL by contract, every role gets
    the same counts."""
    s = overview_app
    safety_r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["safety"]),
    )
    owner_r = await s["client"].get(
        "/api/overview/stats", headers=_hdr(s["tokens"]["owner"]),
    )
    assert safety_r.json().get("vehicles") == owner_r.json().get("vehicles")
    assert safety_r.json().get("vehicles") is not None
    # "fleet" is the deprecated alias of "vehicles" (pre-rename SPA
    # bundles) — must stay byte-identical until removed.
    assert safety_r.json().get("fleet") == safety_r.json().get("vehicles")
    assert owner_r.json().get("fleet") == owner_r.json().get("vehicles")
