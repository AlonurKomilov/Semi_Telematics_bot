"""Operator API tests for /system/accounts/{id}/alert-routing/*.

Surface covered:
  GET    /alert-routing                              — read mode + persona map
  PUT    /alert-routing/mode                         — flip routing mode
  PUT    /alert-routing/personas/{persona}           — upsert binding
  PATCH  /alert-routing/personas/{persona}/active    — soft-disable / enable
  DELETE /alert-routing/personas/{persona}           — destructive remove

Auth is the existing ``require_system_owner`` gate (SYSTEM_OWNER_IDS
env allowlist).  We monkeypatch the parsed set per-test so the suite
doesn't depend on operator-env state.
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
async def system_app(pg_db, monkeypatch):
    """Spin up the FastAPI app with two seeded accounts and a JWT for a
    user the SYSTEM_OWNER_IDS gate accepts.  Returns the AsyncClient
    plus the helpers a test usually needs."""
    db = pg_db
    acct_a = await db.create_account("Operator Test Co A")
    acct_b = await db.create_account("Operator Test Co B")
    op = await db.create_user(900001, acct_a.id, role=Role.OWNER)
    non_op = await db.create_user(900002, acct_a.id, role=Role.OWNER)

    # Allowlist only the operator's telegram_id.
    import capabilities.iam.permissions as perms
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {op.telegram_id})

    op_token = create_jwt(op.telegram_id, acct_a.id, "owner")
    non_op_token = create_jwt(non_op.telegram_id, acct_a.id, "owner")

    # Wire infra.platform to the test DB so api/deps resolves correctly.
    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client,
            "db": db,
            "acct_a": acct_a,
            "acct_b": acct_b,
            "op_token": op_token,
            "non_op_token": non_op_token,
        }

    _cp._db = _old


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_alert_routing_default_for_new_account(system_app):
    s = system_app
    r = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["alert_routing_mode"] == "single_group"
    # All persona keys present, all unregistered.
    assert set(body["personas"].keys()) == {
        "owner_admin", "dispatcher", "safety", "fleet", "hr",
    }
    assert all(v is None for v in body["personas"].values())


@pytest.mark.asyncio
async def test_set_routing_mode_to_per_persona(system_app):
    s = system_app
    r = await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/mode",
        headers=_hdr(s["op_token"]),
        json={"mode": "per_persona_groups"},
    )
    assert r.status_code == 200
    assert r.json()["alert_routing_mode"] == "per_persona_groups"

    # Round-trip via GET.
    g = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert g.json()["alert_routing_mode"] == "per_persona_groups"


@pytest.mark.asyncio
async def test_set_routing_mode_invalid_rejected(system_app):
    s = system_app
    r = await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/mode",
        headers=_hdr(s["op_token"]),
        json={"mode": "garbage"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_upsert_persona_group_creates_active_row(system_app):
    s = system_app
    r = await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/dispatcher",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -1001234567890, "chat_title": "Dispatch"},
    )
    assert r.status_code == 200
    row = r.json()
    assert row["persona"] == "dispatcher"
    assert row["chat_id"] == -1001234567890
    assert row["chat_title"] == "Dispatch"
    assert row["is_active"] is True

    g = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert g.json()["personas"]["dispatcher"]["chat_id"] == -1001234567890


@pytest.mark.asyncio
async def test_upsert_overwrites_chat_id(system_app):
    s = system_app
    await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/fleet",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -111, "chat_title": "Fleet v1"},
    )
    r2 = await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/fleet",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -222, "chat_title": "Fleet v2"},
    )
    assert r2.status_code == 200
    assert r2.json()["chat_id"] == -222
    assert r2.json()["chat_title"] == "Fleet v2"


@pytest.mark.asyncio
async def test_upsert_invalid_persona_rejected(system_app):
    s = system_app
    r = await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/ceo",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -1, "chat_title": ""},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_set_active_toggle_roundtrip(system_app):
    s = system_app
    # Seed a row.
    await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/safety",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -333, "chat_title": "Safety"},
    )

    # Disable.
    r1 = await s["client"].patch(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/safety/active",
        headers=_hdr(s["op_token"]),
        json={"is_active": False},
    )
    assert r1.status_code == 200

    g = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert g.json()["personas"]["safety"]["is_active"] is False

    # Re-enable.
    r2 = await s["client"].patch(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/safety/active",
        headers=_hdr(s["op_token"]),
        json={"is_active": True},
    )
    assert r2.status_code == 200

    g2 = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert g2.json()["personas"]["safety"]["is_active"] is True


@pytest.mark.asyncio
async def test_enable_nonexistent_persona_returns_404(system_app):
    """Enabling a row that doesn't exist returns 404 so the UI knows to
    show the upsert form instead."""
    s = system_app
    r = await s["client"].patch(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/hr/active",
        headers=_hdr(s["op_token"]),
        json={"is_active": True},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_persona_group_is_permanent(system_app):
    s = system_app
    await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/hr",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -444, "chat_title": "HR"},
    )
    r = await s["client"].delete(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/hr",
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 200

    g = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert g.json()["personas"]["hr"] is None


@pytest.mark.asyncio
async def test_non_operator_cannot_call_endpoints(system_app):
    """Customer-side admin (Owner role) without SYSTEM_OWNER_IDS gets 403."""
    s = system_app
    r = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["non_op_token"]),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unknown_account_returns_404(system_app):
    s = system_app
    r = await s["client"].get(
        f"/api/system/accounts/999999/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_persona_groups_are_per_account_via_api(system_app):
    """Verify cross-account isolation: a binding written for acct A must
    not show up under acct B's routing config."""
    s = system_app
    await s["client"].put(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing/personas/dispatcher",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -111, "chat_title": "A"},
    )
    await s["client"].put(
        f"/api/system/accounts/{s['acct_b'].id}/alert-routing/personas/dispatcher",
        headers=_hdr(s["op_token"]),
        json={"chat_id": -222, "chat_title": "B"},
    )
    a = await s["client"].get(
        f"/api/system/accounts/{s['acct_a'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    b = await s["client"].get(
        f"/api/system/accounts/{s['acct_b'].id}/alert-routing",
        headers=_hdr(s["op_token"]),
    )
    assert a.json()["personas"]["dispatcher"]["chat_id"] == -111
    assert b.json()["personas"]["dispatcher"]["chat_id"] == -222
