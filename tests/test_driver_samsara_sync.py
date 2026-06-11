"""Driver Module — Samsara identity-sync tests.

Two layers:
  * Pure detector (``features.drivers.samsara_sync.detect_mismatches``)
    — fed synthetic local + Samsara lists, asserts the right
    ``Mismatch`` rows come back.
  * API route (``GET /api/drivers/samsara``) — verifies the route
    shapes the response, marks already-linked drivers, and gracefully
    degrades when Samsara is unreachable.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from features.drivers.samsara_sync import (
    Mismatch, detect_mismatches, format_digest,
)
from interfaces.api.auth import create_jwt


# ── Pure detector tests ────────────────────────────────────────────


def _local(user_id: int, name: str, samsara_id: str = "", truck: str | None = None):
    """Lightweight stand-in for DriverProfile — only the four fields
    the detector reads need to be present."""
    return SimpleNamespace(
        user_id=user_id,
        display_name=name,
        samsara_driver_id=samsara_id or None,
        truck_num=truck,
    )


def _samsara(sid: str, name: str, org: str = "MAIN") -> dict:
    return {"id": sid, "name": name, "_org": org}


class TestDetectMismatches:
    def test_orphaned_link_when_samsara_id_missing(self):
        # Local says "linked to 444", but Samsara has no such driver.
        local = [_local(1, "Jane Doe", samsara_id="444", truck="107")]
        out = detect_mismatches(local, samsara_drivers=[_samsara("999", "Other")],
                                 static_assignments={})
        assert len(out) == 1
        assert out[0].kind == "orphaned"
        assert out[0].user_id == 1
        assert out[0].samsara_id == "444"

    def test_name_drift_when_names_differ(self):
        local = [_local(1, "Jane Doe", samsara_id="123")]
        out = detect_mismatches(
            local,
            samsara_drivers=[_samsara("123", "Jane Smith")],
            static_assignments={},
        )
        assert len(out) == 1
        assert out[0].kind == "name_drift"
        assert out[0].samsara_name == "Jane Smith"
        assert out[0].display_name == "Jane Doe"

    def test_clean_link_produces_no_row(self):
        local = [_local(1, "Jane Doe", samsara_id="123")]
        out = detect_mismatches(
            local,
            samsara_drivers=[_samsara("123", "Jane Doe")],
            static_assignments={},
        )
        assert out == []

    def test_name_match_is_case_and_whitespace_insensitive(self):
        local = [_local(1, "  jane doe  ", samsara_id="123")]
        out = detect_mismatches(
            local,
            samsara_drivers=[_samsara("123", "Jane Doe")],
            static_assignments={},
        )
        assert out == []

    def test_suggest_when_unlinked_but_samsara_drives_their_truck(self):
        local = [_local(1, "Jane Doe", samsara_id="", truck="107")]
        out = detect_mismatches(
            local,
            samsara_drivers=[_samsara("555", "Jane Doe")],
            static_assignments={"107": "Jane Doe"},
        )
        assert len(out) == 1
        assert out[0].kind == "suggest"
        assert out[0].samsara_id == "555"
        assert out[0].truck_num == "107"

    def test_suggest_skipped_when_no_truck(self):
        # No truck → no assignment lookup is possible.
        local = [_local(1, "Jane Doe", samsara_id="", truck=None)]
        out = detect_mismatches(
            local,
            samsara_drivers=[_samsara("555", "Jane Doe")],
            static_assignments={"107": "Jane Doe"},
        )
        assert out == []

    def test_multiple_kinds_in_one_pass(self):
        local = [
            _local(1, "Jane Doe", samsara_id="444"),                 # orphan
            _local(2, "John Smith", samsara_id="222"),               # drift
            _local(3, "Bob Brown", samsara_id="", truck="200"),      # suggest
            _local(4, "Carol White", samsara_id="333"),              # clean
        ]
        out = detect_mismatches(
            local,
            samsara_drivers=[
                _samsara("222", "Johnny Smith"),
                _samsara("333", "Carol White"),
                _samsara("888", "Bob Brown"),
            ],
            static_assignments={"200": "Bob Brown"},
        )
        kinds = sorted(m.kind for m in out)
        assert kinds == ["name_drift", "orphaned", "suggest"]


class TestFormatDigest:
    def test_empty_list_returns_empty_string(self):
        # Caller uses this as the "should I send anything" signal.
        assert format_digest([]) == ""

    def test_groups_by_kind_with_count_header(self):
        ms = [
            Mismatch("orphaned", 1, "Jane", "107", "444", None,   "orphan note"),
            Mismatch("name_drift", 2, "John", "200", "222", "Johnny", "drift note"),
            Mismatch("suggest", 3, "Bob", "300", "888", "Bob B", "suggest note"),
        ]
        text = format_digest(ms)
        assert "Orphaned" in text
        assert "Name mismatches" in text
        assert "Suggested links" in text
        # Header carries the total count
        assert "3 mismatch" in text


# ── API route test ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def app_ctx(pg_db):
    """Spin up an isolated DB + ASGI app + Samsara stub."""
    db = pg_db
    acct = await db.create_account("X")
    admin = await db.create_user(101, acct.id, role=Role.OWNER, display_name="Boss")
    drv = await db.create_user(201, acct.id, role=Role.DRIVER, display_name="Jane Doe")
    await db.update_driver_profile(drv.id, samsara_driver_id="123")

    tok_admin = create_jwt(admin.telegram_id, acct.id, "owner")
    tok_drv = create_jwt(drv.telegram_id, acct.id, "driver")

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = db

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": db, "acct": acct,
        "admin": admin, "drv": drv,
        "tok_admin": tok_admin, "tok_drv": tok_drv,
    }

    _cp._db = _old_db
    # db.close() handled by the pg_db fixture's own teardown


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestSamsaraDriversRoute:
    async def test_admin_gets_samsara_drivers_with_linked_marker(self, app_ctx):
        ctx = app_ctx
        fake = AsyncMock()
        fake.get_drivers = AsyncMock(return_value=[
            {"id": "123", "name": "Jane Doe", "username": "jane", "_org": "MAIN"},
            {"id": "456", "name": "Bob Brown", "username": "bob",  "_org": "MAIN"},
        ])
        with patch("infra.services.get_client", AsyncMock(return_value=fake)):
            async with AsyncClient(
                transport=ASGITransport(app=ctx["app"]), base_url="http://t",
            ) as c:
                r = await c.get(
                    "/api/drivers/samsara", headers=_h(ctx["tok_admin"]),
                )
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["count"] == 2
                by_id = {d["samsara_driver_id"]: d for d in body["drivers"]}
                # Linked driver carries the local user_id.
                assert by_id["123"]["linked_user_id"] == ctx["drv"].id
                # Unlinked driver has no link.
                assert by_id["456"]["linked_user_id"] is None

    async def test_samsara_unreachable_returns_soft_error(self, app_ctx):
        """When Samsara is down, the UI should still load — degrade
        to free-text entry rather than 500."""
        ctx = app_ctx
        fake = AsyncMock()
        fake.get_drivers = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("infra.services.get_client", AsyncMock(return_value=fake)):
            async with AsyncClient(
                transport=ASGITransport(app=ctx["app"]), base_url="http://t",
            ) as c:
                r = await c.get(
                    "/api/drivers/samsara", headers=_h(ctx["tok_admin"]),
                )
                assert r.status_code == 200
                body = r.json()
                assert body["drivers"] == []
                assert body["error"] == "samsara_unavailable"

    async def test_driver_role_forbidden(self, app_ctx):
        """Drivers don't get fleet-wide visibility."""
        ctx = app_ctx
        async with AsyncClient(
            transport=ASGITransport(app=ctx["app"]), base_url="http://t",
        ) as c:
            r = await c.get(
                "/api/drivers/samsara", headers=_h(ctx["tok_drv"]),
            )
            assert r.status_code == 403


# Silence pytest's unused-import warning — pytest finds the asyncio
# tests via the plugin auto-mode set in pytest.ini.
_ = pytest
