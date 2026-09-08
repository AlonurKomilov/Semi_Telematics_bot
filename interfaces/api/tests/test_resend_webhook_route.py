"""The Resend webhook, exercised as a REQUEST rather than a function.

This file exists because of what its absence cost. The handler carried
``db = await _get_platform_db()`` — an await on a sync accessor — from
the day it was written. Every correctly-signed event would have raised
``TypeError: object Database can't be used in 'await' expression`` and
returned 500. Nobody noticed for months, because the endpoint URL was
never configured: a handler nothing calls is a handler nobody has
tested, and unit tests of the inner helpers could not see it.

So these drive the ROUTE — signature headers, real body, real
middleware — which is the only altitude at which that class of bug is
visible.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SECRET = "whsec_" + base64.b64encode(b"test-signing-secret-32-bytes-ok!").decode()

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", SECRET)
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    from interfaces.api.app import create_api
    return create_api()


def _signed(body: dict, *, msg_id: str = "msg_test_1", skew: int = 0):
    """Headers Svix would send — the same construction Resend uses."""
    raw = json.dumps(body).encode()
    ts = str(int(time.time()) + skew)
    key = base64.b64decode(SECRET.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(
        key, f"{msg_id}.{ts}.".encode() + raw, hashlib.sha256).digest()).decode()
    return raw, {"Content-Type": "application/json", "svix-id": msg_id,
                 "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}


async def _post(app, raw, headers):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        return await c.post("/api/v1/webhooks/resend", content=raw,
                            headers=headers)


class TestTheRouteSurvivesARealRequest:

    async def test_a_signed_event_does_not_500(self, api):
        """The regression this file was written for. An unmatched event
        is a NORMAL case — most events belong to neither an invite nor a
        notification — and it must answer 200, not crash."""
        raw, headers = _signed({"type": "email.delivered",
                                "data": {"email_id": "nothing-matches-this"}})
        r = await _post(api, raw, headers)
        assert r.status_code == 200, r.text
        assert r.json()["matched"] is False

    async def test_a_bad_signature_is_refused(self, api):
        raw, headers = _signed({"type": "email.delivered", "data": {}})
        headers["svix-signature"] = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        r = await _post(api, raw, headers)
        assert r.status_code == 401

    async def test_missing_headers_are_refused(self, api):
        raw, _ = _signed({"type": "email.delivered", "data": {}})
        r = await _post(api, raw, {"Content-Type": "application/json"})
        assert r.status_code == 401

    async def test_a_stale_event_is_refused(self, api):
        """The 5-minute replay window. Worth pinning because it is also
        why a backlog of hours-old events cannot be replayed into the
        endpoint — a real operational consequence, not just a check."""
        raw, headers = _signed({"type": "email.delivered", "data": {}},
                               skew=-3600)
        r = await _post(api, raw, headers)
        assert r.status_code == 401
        assert "eplay" in r.text or "imestamp" in r.text

    async def test_an_unset_secret_fails_closed(self, api, monkeypatch):
        """Without this the endpoint would be 'any POST mutates state'."""
        monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "")
        raw, headers = _signed({"type": "email.delivered", "data": {}})
        r = await _post(api, raw, headers)
        assert r.status_code in (401, 503)


class TestABouncedNotificationRetiresItsChannel:

    async def test_end_to_end_through_the_route(self, api, pg_db):
        """The whole point of the feature, driven the way Resend drives
        it: a signed hard-bounce arrives, and the channel it belongs to
        is switched off with the person told in the bell."""
        from adapters.storage.models import Role
        acct = await pg_db.create_account("Route Bounce Co")
        u = await pg_db.create_user(telegram_id=7950, account_id=acct.id,
                                    role=Role.FLEET)
        await pg_db.upsert_notification_channel(
            acct.id, "user", u.id, "email", address="a@example.com",
            verified=True)
        await pg_db.record_notification_delivery(
            acct.id, channel="email", recipient_type="user",
            recipient_id=str(u.id), category="alert.faults",
            correlation_key="alert:9",
            handle={"resend_email_id": "re_route_test"})

        raw, headers = _signed(
            {"type": "email.bounced",
             "data": {"email_id": "re_route_test",
                      "bounce": {"type": "Permanent"}}},
            msg_id="msg_route_bounce")
        r = await _post(api, raw, headers)
        assert r.status_code == 200, r.text

        conn = await pg_db.get_notification_channel(
            acct.id, "user", u.id, "email")
        assert not conn["enabled_master"]
        assert [n for n in await pg_db.list_inbox_notices(acct.id, u.id)
                if n["category"] == "system.channel_broken"]
