"""A token for a client that needs a SLICE of the account.

The browser extension shows a truck list.  If its token is lifted off a
laptop it must open a truck list, not the account — however senior the
person who signed in.  These pin the three places that make that true:
mint, refresh, and the permission gate.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from interfaces.api import auth as auth_mod
from interfaces.api.auth import (
    EXTENSION_AUDIENCE, EXTENSION_SCOPE, create_jwt, decode_jwt,
)


def test_a_scoped_token_carries_its_audience_and_scope():
    tok = create_jwt(1, 42, "owner", user_id=7,
                     aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)
    payload = decode_jwt(tok)
    assert payload["aud"] == "extension"
    assert payload["scope"] == list(EXTENSION_SCOPE)


def test_an_ordinary_token_carries_neither():
    payload = decode_jwt(create_jwt(1, 42, "owner", user_id=7))
    assert "aud" not in payload and "scope" not in payload


@pytest.mark.asyncio
async def test_an_unknown_audience_is_refused_not_read_as_unscoped(monkeypatch):
    """A token we did not mint for any client we know is not a token."""
    from interfaces.api import deps
    tok = create_jwt(1, 42, "owner", user_id=7, aud="some-other-app", scope=[])

    async def _not_revoked(_jti):
        return False
    monkeypatch.setattr(deps, "_is_revoked_with_cache", _not_revoked)

    class _Req:
        state = type("S", (), {})()
    with pytest.raises(HTTPException) as e:
        await deps.get_current_user(_Req(), authorization=f"Bearer {tok}", auth_token=None)
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_the_owner_behind_an_extension_token_cannot_archive_a_truck(pg_db, monkeypatch):
    """THE POINT.  Same person, two tokens: the dashboard one may do
    everything the owner may; the extension one may read the live map
    and nothing else."""
    from interfaces.api import deps
    import infra.platform as _cp

    acct = (await pg_db.create_account("Scoped Co")).id
    monkeypatch.setattr(_cp, "_db", pg_db)

    async def _not_revoked(_jti):
        return False
    monkeypatch.setattr(deps, "_is_revoked_with_cache", _not_revoked)

    class _Req:
        state = type("S", (), {})()

    scoped = create_jwt(1, acct, "owner", user_id=7, is_primary_owner=True,
                        aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)
    user = await deps.get_current_user(_Req(), authorization=f"Bearer {scoped}", auth_token=None)

    # The live map — the one thing the extension exists for — passes.
    assert await deps.require_permission("can_view_location")(user=dict(user))
    # Everything else an owner could normally do is refused for THIS token.
    for denied in ("can_manage_vehicles", "can_manage_users", "can_manage_vehicle_docs"):
        with pytest.raises(HTTPException) as e:
            await deps.require_permission(denied)(user=dict(user))
        assert e.value.status_code == 403, denied

    # And the same owner's ordinary token is untouched.
    full = create_jwt(1, acct, "owner", user_id=7, is_primary_owner=True)
    user_full = await deps.get_current_user(_Req(), authorization=f"Bearer {full}", auth_token=None)
    assert await deps.require_permission("can_manage_vehicles")(user=dict(user_full))


def test_refresh_keeps_the_scope_it_was_given():
    """A refresh that dropped aud/scope would widen a truck-list key into
    an account key every eight hours.  Pinned at the call site."""
    import inspect
    src = inspect.getsource(auth_mod.refresh_token)
    assert 'aud=payload.get("aud")' in src and 'scope=payload.get("scope")' in src


# ── The consent flow: one mint, no password in the panel ─────────────
# (The password login used to mint the scoped token for a panel that sent
# client="extension"; that path is gone — see the refusal test below.)


def test_only_the_consent_endpoint_mints_an_extension_token():
    """"Never silently connect" made grep-enforceable: the audience is
    passed to the mint from exactly one place, the consent endpoint —
    not from any password or Telegram login."""
    import re
    from tests._repo import REPO
    auth_src = (REPO / "interfaces/api/auth.py").read_text()
    ext_src = (REPO / "interfaces/api/routes/extension.py").read_text()
    pat = re.compile(r"aud\s*=\s*EXTENSION_AUDIENCE\b")
    assert not pat.search(auth_src), "auth.py must not mint an extension token"
    assert len(pat.findall(ext_src)) == 1


def test_the_login_endpoints_refuse_an_old_panel_instead_of_widening_it():
    """A panel still sending client="extension" gets a 400 with the new
    instruction — never an UNSCOPED token plus a cookie, which is what
    dropping the branch silently would have produced."""
    from tests._repo import REPO
    src = (REPO / "interfaces/api/auth.py").read_text()
    assert src.count('if body.client == EXTENSION_AUDIENCE:') == 3
    assert src.count("Connect the browser extension from your 4truck dashboard.") == 3


@pytest.mark.asyncio
async def test_refresh_refuses_a_revoked_session(monkeypatch):
    """Refresh would otherwise carry the expiry past the denylist entry
    and a disconnected session would come back on its own."""
    from starlette.responses import Response
    tok = create_jwt(1, 42, "owner", user_id=7, jti="revoked-jti")

    async def _revoked(jti):
        return jti == "revoked-jti"
    monkeypatch.setattr(auth_mod, "is_jti_revoked", _revoked)
    # The handle is looked up before the token is read; no query may run
    # for a revoked token, so an object with no methods is the proof.
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db", lambda: object())

    class _Req:
        headers = {"user-agent": "x"}
        client = None
    # ``__wrapped__``: past the rate limiter, which wants a real Request.
    with pytest.raises(HTTPException) as e:
        await auth_mod.refresh_token.__wrapped__(_Req(), Response(), authorization=f"Bearer {tok}")
    assert e.value.status_code == 401
    assert "revoked" in str(e.value.detail).lower()


@pytest.mark.asyncio
async def test_refreshing_an_extension_token_sets_no_cookie(monkeypatch):
    """The panel's refresh must never become the dashboard's cookie: a
    two-permission key would overwrite a full session, or a lifted
    panel token would gain one."""
    from starlette.responses import Response
    from types import SimpleNamespace
    tok = create_jwt(1, 42, "owner", user_id=7, jti="ext-jti",
                     aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)

    async def _not_revoked(_jti):
        return False
    monkeypatch.setattr(auth_mod, "is_jti_revoked", _not_revoked)

    role = SimpleNamespace(value="owner")
    user = SimpleNamespace(id=7, telegram_id=1, account_id=42, role=role,
                           is_active=True, is_manager=False, is_primary_owner=True,
                           display_name="A")

    class _DB:
        async def get_user_by_telegram_id(self, _tid):
            return user
        async def update_user_session_on_refresh(self, *a, **k):
            return None
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db", lambda: _DB())

    class _Req:
        headers = {"user-agent": "x"}
        client = None
    res = Response()
    out = await auth_mod.refresh_token.__wrapped__(_Req(), res, authorization=f"Bearer {tok}")
    assert decode_jwt(out.access_token)["aud"] == "extension"
    assert "set-cookie" not in {k.decode().lower() for k, _ in res.raw_headers}

    # And the unscoped counterpart DOES get its cookie — the guard is
    # about the audience, not a regression for the dashboard.
    plain = create_jwt(1, 42, "owner", user_id=7, jti="dash-jti")
    res2 = Response()
    await auth_mod.refresh_token.__wrapped__(_Req(), res2, authorization=f"Bearer {plain}")
    assert "set-cookie" in {k.decode().lower() for k, _ in res2.raw_headers}


@pytest.mark.asyncio
async def test_connect_refuses_a_scoped_caller_and_a_bare_post(monkeypatch):
    from interfaces.api.routes import extension as ext

    class _Req:
        headers = {"x-requested-with": "4truck-dashboard", "user-agent": "x"}
        client = None
    scoped = {"sub": "1", "account_id": 42, "role": "owner", "uid": 7,
              "aud": "extension", "scope": list(EXTENSION_SCOPE)}
    with pytest.raises(HTTPException) as e:
        await ext.connect_extension.__wrapped__(_Req(), user=scoped)
    assert e.value.status_code == 403

    class _Bare:
        headers = {"user-agent": "x"}
        client = None
    with pytest.raises(HTTPException) as e:
        await ext.connect_extension.__wrapped__(_Bare(), user={"sub": "1", "account_id": 42, "role": "owner", "uid": 7})
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_connect_mints_the_scoped_token_as_its_own_announced_session(monkeypatch):
    """The whole point in one call: a dashboard session in, a live-map
    key out — its own session row, notified regardless of device label,
    and no cookie anywhere."""
    from types import SimpleNamespace
    from interfaces.api.routes import extension as ext

    role = SimpleNamespace(value="owner")
    db_user = SimpleNamespace(id=7, telegram_id=1, account_id=42, role=role,
                              is_active=True, is_manager=False,
                              is_primary_owner=True, display_name="Allen")
    seen = {}

    async def _db_user(user, db):
        return db_user
    monkeypatch.setattr(ext, "get_current_db_user", _db_user)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db", lambda: object())

    async def _mint(db, request, **kw):
        seen.update(kw)
        return "minted"
    monkeypatch.setattr(ext, "mint_session_token", _mint)

    class _Req:
        headers = {"x-requested-with": "4truck-dashboard", "user-agent": "x"}
        client = None
    out = await ext.connect_extension.__wrapped__(
        _Req(), user={"sub": "1", "account_id": 42, "role": "owner", "uid": 7})
    assert out.access_token == "minted"
    assert seen["aud"] == EXTENSION_AUDIENCE and tuple(seen["scope"]) == EXTENSION_SCOPE
    assert seen["device_label"] == "Browser extension"
    assert seen["always_notify"] is True and seen["remember_me"] is True


def test_the_signin_notice_points_at_the_one_session_to_disconnect():
    from interfaces.api.security_notifications import signin_notice_action
    assert signin_notice_action(91) == {
        "label": "Disconnect this session", "url": "/profile?session=91"}
    assert signin_notice_action(None)["url"] == "/profile"
