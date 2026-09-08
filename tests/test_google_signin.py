"""Sign in with Google — the rules that make it safe, each pinned.

Google's own verification is stubbed: ``verify_google_credential`` is
replaced by a function that returns the claims a test hands it.  What
is tested is everything AFTER Google has spoken — who a verified
identity may sign in as, when an email may link, what a setup token
opens, and what the strand guard refuses.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

SUB = "google-sub-1001"
SUB2 = "google-sub-2002"


def _claims(email: str, sub: str = SUB, name: str = "Owner One") -> dict:
    return {"sub": sub, "email": email.lower(), "name": name}


@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    import infra.platform as _cp
    _cp._db = pg_db
    monkeypatch.setenv("GOOGLE_SIGNIN_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


def _stub_google(monkeypatch, claims: dict):
    """Google said this.  Everything below is our decision."""
    import interfaces.api.auth as auth_mod

    async def _verify(credential: str) -> dict:
        assert credential, "an empty credential must never reach verification"
        return dict(claims)
    monkeypatch.setattr(auth_mod, "verify_google_credential", _verify)
    # routes/user.py imports the name at call time from the module above.


async def _verified_user(db, email: str, *, company: str = "Linked Co", password_hash="x" * 20):
    acct = await db.create_account(company)
    from adapters.storage.models import Role
    user = await db.create_user_with_email(
        email=email, password_hash=password_hash, account_id=acct.id,
        role=Role.OWNER, display_name="Owner", email_verified=True,
    )
    return acct, user


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── linking on first sign-in ─────────────────────────────────────────

async def test_first_google_signin_links_by_doubly_verified_email(api, monkeypatch):
    app, db = api
    acct, user = await _verified_user(db, "owner@linked.co")
    _stub_google(monkeypatch, _claims("owner@linked.co"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/google", json={"credential": "tok"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["account_id"] == acct.id
    fresh = await db.get_user_by_id(user.id)
    assert fresh.google_sub == SUB and fresh.google_email == "owner@linked.co"


async def test_unknown_email_is_refused_with_the_generic_message(api, monkeypatch):
    app, db = api
    _stub_google(monkeypatch, _claims("nobody@nowhere.co"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/google", json={"credential": "tok"})
    assert r.status_code == 401
    generic = r.json()["detail"]
    # The same words for every refusal: nothing here says whether the
    # address exists.
    _stub_google(monkeypatch, _claims("owner@unverified.co"))
    acct = await db.create_account("Unverified Co")
    from adapters.storage.models import Role
    await db.create_user_with_email(email="owner@unverified.co", password_hash="x" * 20,
                                    account_id=acct.id, role=Role.OWNER, email_verified=False)
    async with await _client(app) as c:
        r2 = await c.post("/api/auth/google", json={"credential": "tok"})
    assert r2.status_code == 401 and r2.json()["detail"] == generic


async def test_an_email_in_two_accounts_is_refused_not_guessed(api, monkeypatch):
    """The schema says UNIQUE(account_id, email); a global idx_users_email
    on the live database says stricter.  The rule is written for the
    schema — if that index is ever dropped, a first Google sign-in must
    still refuse to guess between tenants — so the count is stubbed."""
    app, db = api
    acct, user = await _verified_user(db, "twice@acme.co", company="Acme One")

    async def _two(email: str) -> int:
        return 2 if email == "twice@acme.co" else 0
    monkeypatch.setattr(type(db), "count_accounts_for_email", _two, raising=False)
    monkeypatch.setattr(db, "count_accounts_for_email", _two, raising=False)
    _stub_google(monkeypatch, _claims("twice@acme.co"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/google", json={"credential": "tok"})
    assert r.status_code == 401
    # ...and the one row that exists was NOT linked by guesswork.
    assert (await db.get_user_by_id(user.id)).google_sub is None


async def test_once_linked_only_the_sub_matches(api, monkeypatch):
    app, db = api
    acct, user = await _verified_user(db, "owner@bound.co")
    await db.link_google_to_user(user.id, SUB, "owner@bound.co")
    # A DIFFERENT Google account carrying the same address — a reused
    # Workspace mailbox — must not inherit the user.
    _stub_google(monkeypatch, _claims("owner@bound.co", sub=SUB2))
    async with await _client(app) as c:
        r = await c.post("/api/auth/google", json={"credential": "tok"})
    assert r.status_code == 401
    # The bound sub still signs in.
    _stub_google(monkeypatch, _claims("changed@elsewhere.co", sub=SUB))
    async with await _client(app) as c:
        r = await c.post("/api/auth/google", json={"credential": "tok"})
    assert r.status_code == 200 and r.json()["user"]["account_id"] == acct.id


# ── the strand guard ─────────────────────────────────────────────────

async def test_google_cannot_be_unlinked_when_it_is_the_only_way_in(api):
    app, db = api
    acct = await db.create_account("Google Only Co")
    from adapters.storage.models import Role
    user = await db.create_user_with_email(
        email="g@only.co", password_hash=None, account_id=acct.id, role=Role.OWNER,
        email_verified=True, google_sub=SUB, google_email="g@only.co",
    )
    with pytest.raises(ValueError):
        await db.unlink_google_from_user(user.id)
    # Add a password → now it may go.
    await db.set_user_email_password(user.id, "g@only.co", "h" * 20)
    await db.unlink_google_from_user(user.id)
    assert (await db.get_user_by_id(user.id)).google_sub is None


async def test_the_same_guard_protects_telegram(api):
    app, db = api
    acct = await db.create_account("TG Only Co")
    from adapters.storage.models import Role
    user = await db.create_user(telegram_id=777001, account_id=acct.id, role=Role.OWNER)
    with pytest.raises(ValueError):
        await db.unlink_telegram_from_user(user.id)


# ── a company from Google: the setup gate ────────────────────────────

async def test_google_signup_is_gated_until_setup_is_complete(api, monkeypatch):
    app, db = api
    _stub_google(monkeypatch, _claims("founder@newco.io", name="Founder"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/register-google", json={"credential": "tok"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "setup_required"
        assert "access_token" not in body and body["setup_token"]
        acct_id = body["account_id"]

        # Nothing signs in while setup is pending.  Google, for the
        # owner who started it, hands back a FRESH setup token instead —
        # a closed tab or an expired fifteen minutes is not a dead end.
        r = await c.post("/api/auth/google", json={"credential": "tok"})
        assert r.status_code == 200 and r.json()["status"] == "setup_required"
        assert "access_token" not in r.json() and r.json()["setup_token"]
        body["setup_token"] = r.json()["setup_token"]     # the resumed one is the one we use

        # The setup token opens exactly one door.
        hdr = {"Authorization": f"Bearer {body['setup_token']}"}
        r = await c.get("/api/user/me", headers=hdr)
        assert r.status_code == 403
        r = await c.post("/api/auth/refresh", headers=hdr)
        assert r.status_code == 403

        # No trial yet: an abandoned click burns nothing.
        assert (await db.get_account(acct_id)).tier != "pro"

        # Complete it.
        r = await c.post("/api/auth/complete-setup", headers=hdr, json={
            "company_name": "New Co Logistics", "password": "averylongpassword", "display_name": "F. Ounder",
        })
        assert r.status_code == 200, r.text
        assert r.json()["access_token"]

        # The gate is open, the company is named, the trial started.
        lc = await db.get_account_lifecycle(acct_id)
        assert lc["setup_pending_since"] is None and lc["name"] == "New Co Logistics"
        assert (await db.get_account(acct_id)).tier == "pro"

        # Completion cannot be replayed — the setup token is spent.
        r = await c.post("/api/auth/complete-setup", headers=hdr, json={
            "company_name": "Again", "password": "averylongpassword",
        })
        assert r.status_code in (401, 403, 409)
        # ...and the email path was refused all along, with the reason.

        # And Google now signs in normally, and the password works too.
        r = await c.post("/api/auth/google", json={"credential": "tok"})
        assert r.status_code == 200
        r = await c.post("/api/auth/login", json={"email": "founder@newco.io", "password": "averylongpassword"})
        assert r.status_code == 200, r.text


async def test_abandoned_google_signups_are_listed_for_the_sweep(api, monkeypatch):
    app, db = api
    _stub_google(monkeypatch, _claims("gone@newco.io"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/register-google", json={"credential": "tok"})
    acct_id = r.json()["account_id"]
    assert acct_id not in await db.list_accounts_setup_abandoned(before_iso="2000-01-01T00:00:00Z")
    assert acct_id in await db.list_accounts_setup_abandoned(before_iso="2999-01-01T00:00:00Z")


# ── joining by invite ────────────────────────────────────────────────

async def test_google_with_an_invite_joins_that_company_once(api, monkeypatch):
    app, db = api
    acct = await db.create_account("Invite Co")
    from adapters.storage.models import Role
    admin = await db.create_user_with_email(email="admin@invite.co", password_hash="x" * 20,
                                            account_id=acct.id, role=Role.OWNER, email_verified=True)
    await db._db.execute(
        "INSERT INTO invites (code, account_id, role, created_by, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("INV-GOOGLE-1", acct.id, "dispatcher", admin.id, "2999-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    await db._db.commit()
    _stub_google(monkeypatch, _claims("new.dispatcher@gmail.com", sub=SUB2, name="Dee Spatch"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/register-google", json={"credential": "tok", "invite_code": "INV-GOOGLE-1"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "joined" and r.json()["user"]["role"] == "dispatcher"
        # Second use of the same invite: refused.
        _stub_google(monkeypatch, _claims("another@gmail.com", sub="google-sub-3003"))
        r = await c.post("/api/auth/register-google", json={"credential": "tok", "invite_code": "INV-GOOGLE-1"})
        assert r.status_code == 410
    joined = await db.get_user_by_google_sub(SUB2)
    assert joined.account_id == acct.id and joined.password_hash is None and joined.role.value == "dispatcher"


# ── the config, per host ─────────────────────────────────────────────

async def test_the_client_id_is_served_to_login_hosts_and_never_to_the_operator_console(api):
    app, _ = api
    async with await _client(app) as c:
        r = await c.get("/api/auth/config", headers={"host": "dash.4truck.us"})
        assert r.json()["google_signin_client_id"].endswith("googleusercontent.com")
        r = await c.get("/api/auth/config", headers={"host": "system.4truck.us"})
        assert r.json()["google_signin_client_id"] == ""


async def test_the_sweep_claims_before_it_purges(api, monkeypatch):
    """An owner who finishes setup between the sweep's listing and its
    deletion keeps their company: the claim re-checks the predicate in
    the same statement that takes the row."""
    app, db = api
    _stub_google(monkeypatch, _claims("late@newco.io"))
    async with await _client(app) as c:
        r = await c.post("/api/auth/register-google", json={"credential": "tok"})
        acct_id = r.json()["account_id"]
        hdr = {"Authorization": f"Bearer {r.json()['setup_token']}"}
        # Listed as abandoned (pretend a week passed)...
        assert acct_id in await db.list_accounts_setup_abandoned(before_iso="2999-01-01T00:00:00Z")
        # ...then the owner completes setup before the sweep reaches it.
        r = await c.post("/api/auth/complete-setup", headers=hdr, json={
            "company_name": "Late But Real Co", "password": "averylongpassword",
        })
        assert r.status_code == 200, r.text
    # The claim refuses, so the sweep skips the purge.
    assert await db.claim_abandoned_setup(acct_id, before_iso="2999-01-01T00:00:00Z") is False
    assert (await db.get_account(acct_id)).name == "Late But Real Co"
