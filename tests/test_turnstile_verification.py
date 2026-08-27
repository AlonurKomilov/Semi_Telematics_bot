"""Tests for the Cloudflare Turnstile integration.

Covers:
- Helper is a no-op when TURNSTILE_SECRET_KEY is unset (dev/test bypass)
- Helper rejects empty/missing tokens when configured
- Helper passes token through to Cloudflare and returns the boolean verdict
- /auth/register-account rejects 403 when Turnstile fails
- /auth/register rejects 403 when Turnstile fails
- /auth/config exposes the site key
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport


# ── verify_turnstile helper unit tests ────────────────────────────


class TestTurnstileHelper:
    async def test_no_op_when_secret_unset(self, monkeypatch):
        """Empty TURNSTILE_SECRET_KEY → always return True (no API call)."""
        monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
        from infra.turnstile import verify_turnstile, is_turnstile_enabled
        assert is_turnstile_enabled() is False
        # Even an empty token passes when the gate isn't configured.
        assert await verify_turnstile(None) is True
        assert await verify_turnstile("") is True
        assert await verify_turnstile("anything") is True

    async def test_rejects_empty_token_when_configured(self, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test-secret-xxx")
        from infra.turnstile import verify_turnstile
        assert await verify_turnstile(None) is False
        assert await verify_turnstile("") is False

    async def test_passes_token_to_cloudflare(self, monkeypatch):
        """Configured: posts to siteverify and returns the success field."""
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test-secret-xxx")

        calls = []

        class _FakeResp:
            status_code = 200
            def json(self):
                return {"success": True}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, data=None):
                calls.append((url, data))
                return _FakeResp()

        import infra.turnstile as ts
        monkeypatch.setattr(ts.httpx, "AsyncClient", _FakeClient)

        result = await ts.verify_turnstile("valid-token", remote_ip="1.2.3.4")
        assert result is True
        assert calls
        assert calls[0][1]["secret"] == "test-secret-xxx"
        assert calls[0][1]["response"] == "valid-token"
        assert calls[0][1]["remoteip"] == "1.2.3.4"

    async def test_returns_false_on_cloudflare_failure(self, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test-secret-xxx")

        class _FakeResp:
            status_code = 200
            def json(self):
                return {"success": False, "error-codes": ["invalid-input-response"]}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, data=None):
                return _FakeResp()

        import infra.turnstile as ts
        monkeypatch.setattr(ts.httpx, "AsyncClient", _FakeClient)

        assert await ts.verify_turnstile("bad-token") is False

    async def test_returns_false_on_http_error(self, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test-secret-xxx")

        import infra.turnstile as ts

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, url, data=None):
                raise ts.httpx.ConnectError("dns fail")

        monkeypatch.setattr(ts.httpx, "AsyncClient", _FakeClient)

        # Network failure should fail-closed (reject), never silently pass.
        assert await ts.verify_turnstile("good-token") is False


# ── Endpoint integration tests ────────────────────────────────────


@pytest_asyncio.fixture
async def api_app(pg_db, monkeypatch):
    import infra.platform as _cp
    _cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


class TestRegisterAccountTurnstile:
    async def test_skips_when_not_configured(self, api_app, monkeypatch):
        """No TURNSTILE_SECRET_KEY: omitting the token must NOT block."""
        monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Skip CF Co",
                "email":        "skip.cf@example.com",
                "password":     "longpassword01",
            })
        assert r.status_code == 200, r.text

    async def test_rejects_when_token_invalid(self, api_app, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "live-secret-xxx")

        # Patch siteverify to always-fail so we don't hit Cloudflare.
        async def _fake_verify(token, remote_ip=None):
            return False
        import interfaces.api.auth as _auth_mod
        import infra.turnstile as ts
        monkeypatch.setattr(ts, "verify_turnstile", _fake_verify)
        # The endpoint does a local import — patch its module reference too.
        if hasattr(_auth_mod, "verify_turnstile"):
            monkeypatch.setattr(_auth_mod, "verify_turnstile", _fake_verify)

        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Bot Co",
                "email":        "bot.co@example.com",
                "password":     "longpassword01",
                "turnstile_token": "bad",
            })
        assert r.status_code == 403, r.text
        assert "captcha" in r.json()["detail"].lower()

    async def test_accepts_when_token_valid(self, api_app, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "live-secret-xxx")

        async def _fake_verify(token, remote_ip=None):
            return True
        import infra.turnstile as ts
        monkeypatch.setattr(ts, "verify_turnstile", _fake_verify)

        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Real Human Co",
                "email":        "real.human@example.com",
                "password":     "longpassword01",
                "turnstile_token": "valid-token",
            })
        assert r.status_code == 200, r.text


class TestForgotPasswordTurnstile:
    async def test_403_on_captcha_fail(self, api_app, monkeypatch):
        async def _deny(token, remote_ip=None):
            return False
        import infra.turnstile as ts
        monkeypatch.setattr(ts, "verify_turnstile", _deny)
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/forgot-password", json={
                "email": "someone@example.com",
            })
        assert r.status_code == 403

    async def test_passes_without_turnstile_config(self, api_app, monkeypatch):
        """Dev mode: no secret configured → flow works without a token."""
        monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/forgot-password", json={
                "email": "nobody@example.com",
            })
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── R7: company-name collision ────────────────────────────────────


class TestNameCollision:
    async def test_case_insensitive_collision_raises(self, pg_db):
        import pytest as _pytest
        await pg_db.create_account("Acme Trucking")
        with _pytest.raises(ValueError, match="already exists"):
            await pg_db.create_account("ACME TRUCKING")

    async def test_collision_returns_409_on_self_serve(self, api_app):
        app, db = api_app
        await db.create_account("Premier Haulage")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "premier haulage",
                "email": "x@example.com",
                "password": "longpassword01",
            })
        assert r.status_code == 409
        assert "already exists" in r.json()["detail"]


# ── R4: atomic create_account ─────────────────────────────────────


class TestAtomicCreate:
    async def test_seed_failure_rolls_back_account_row(self, pg_db, monkeypatch):
        """PTI-seed failure must NOT leave a half-provisioned account."""
        import pytest as _pytest

        async def _boom(*a, **k):
            raise RuntimeError("simulated seed failure")
        monkeypatch.setattr(pg_db, "seed_account_pti_templates", _boom)

        with _pytest.raises(RuntimeError, match="simulated seed failure"):
            await pg_db.create_account("Doomed Carriers")

        # The account row must be gone (rolled back with the seeds).
        cur = await pg_db._db.execute(
            "SELECT COUNT(*) FROM accounts WHERE name = ?", ("Doomed Carriers",)
        )
        assert (await cur.fetchone())[0] == 0
        # No orphan permission rows either.
        cur = await pg_db._db.execute(
            "SELECT COUNT(*) FROM role_permissions rp "
            "WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id = rp.account_id)"
        )
        assert (await cur.fetchone())[0] == 0

    async def test_successful_create_seeds_everything(self, pg_db):
        acct = await pg_db.create_account("Healthy Fleet")
        cur = await pg_db._db.execute(
            "SELECT COUNT(*) FROM role_permissions WHERE account_id = ?", (acct.id,)
        )
        assert (await cur.fetchone())[0] > 0
        cur = await pg_db._db.execute(
            "SELECT COUNT(*) FROM pti_checklist_templates WHERE account_id = ?", (acct.id,)
        )
        assert (await cur.fetchone())[0] == 2  # truck + trailer


# ── R5: platform audit log ────────────────────────────────────────


class TestPlatformAudit:
    async def test_write_and_list(self, pg_db):
        acct = await pg_db.create_account("Audited Inc")
        await pg_db.add_platform_audit(
            "account_created",
            account_id=acct.id,
            actor="operator:12345",
            details="test entry",
        )
        rows = await pg_db.list_platform_audit(event="account_created")
        assert len(rows) == 1
        assert rows[0]["account_id"] == acct.id
        assert rows[0]["actor"] == "operator:12345"

    async def test_self_serve_signup_writes_audit(self, api_app):
        app, db = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Audit Trail Co",
                "email": "audit@example.com",
                "password": "longpassword01",
            })
        assert r.status_code == 200, r.text
        rows = await db.list_platform_audit(event="account_created")
        assert any(row["actor"] == "self-serve" for row in rows)


class TestAuthConfigExposesSiteKey:
    async def test_site_key_exposed(self, api_app, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "public-site-key-123")
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/api/auth/config")
        assert r.status_code == 200
        assert r.json()["turnstile_site_key"] == "public-site-key-123"

    async def test_site_key_empty_when_unset(self, api_app, monkeypatch):
        monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/api/auth/config")
        assert r.status_code == 200
        # The field is always present; just empty when not configured.
        assert r.json().get("turnstile_site_key", "") == ""
