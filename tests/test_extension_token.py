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


def _req(path: str):
    """A request fake with what get_current_user reads: a path and a state."""
    class _R:
        url = type("U", (), {"path": path})()
        state = type("S", (), {})()
    return _R


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

    _Req = _req("/api/map/vehicles")
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

    _Req = _req("/api/map/vehicles")

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

    async def _perms(role, account_id, **kw):
        return SimpleNamespace(can_view_location=True)
    monkeypatch.setattr(ext, "get_user_permissions", _perms)

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


@pytest.mark.asyncio
async def test_connect_refuses_a_role_without_the_live_map(monkeypatch):
    """Permissions are the single source of truth; the token's scope only
    narrows them.  A person whose role has no live map would connect
    and then meet 403 on every request — so the consent step says no,
    and no session is minted."""
    from types import SimpleNamespace
    from interfaces.api.routes import extension as ext

    role = SimpleNamespace(value="accounting")
    db_user = SimpleNamespace(id=9, telegram_id=2, account_id=42, role=role,
                              is_active=True, is_manager=False,
                              is_primary_owner=False, display_name="B")

    async def _db_user(user, db):
        return db_user
    monkeypatch.setattr(ext, "get_current_db_user", _db_user)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db", lambda: object())

    async def _perms(role, account_id, **kw):
        return SimpleNamespace(can_view_location=False)
    monkeypatch.setattr(ext, "get_user_permissions", _perms)
    minted = []

    async def _mint(db, request, **kw):
        minted.append(kw)
        return "minted"
    monkeypatch.setattr(ext, "mint_session_token", _mint)

    class _Req:
        headers = {"x-requested-with": "4truck-dashboard", "user-agent": "x"}
        client = None
    with pytest.raises(HTTPException) as e:
        await ext.connect_extension.__wrapped__(
            _Req(), user={"sub": "2", "account_id": 42, "role": "accounting", "uid": 9})
    assert e.value.status_code == 403
    assert "live map" in str(e.value.detail)
    assert minted == []


def test_the_panels_data_path_is_the_dashboards_gates_not_its_own():
    """The extension writes no permission rule of its own: the two
    endpoints it reads go through the same permission gate and the same
    Team Management scope filters as the dashboard's Live Map — company
    allow-list, then assigned-vehicle scope.  A future endpoint the
    panel reads must be added to this list and pass the same checks."""
    from tests._repo import REPO
    src = (REPO / "features/location/router.py").read_text()
    # The panel reads /map/vehicles and /map/vehicles/live.
    panel = (REPO / "interfaces/browser_extension/src/features/live-map/LiveMapPanel.tsx").read_text()
    assert "'/map/vehicles'" in panel and "'/map/vehicles/live'" in panel
    # Both are permission-gated by the verdict the scope narrows to.
    assert src.count('require_permission("can_view_location")') >= 2
    # Both apply Team Management's company scope and the unit scope.
    assert "filter_by_allowed_companies(" in src
    assert "filter_by_assigned_trucks(" in src and "member_unit_scope(user, \"location\")" in src


def test_the_signin_notice_points_at_the_one_session_to_disconnect():
    from interfaces.api.security_notifications import signin_notice_action
    assert signin_notice_action(91) == {
        "label": "Disconnect this session", "url": "/profile?session=91"}
    assert signin_notice_action(None)["url"] == "/profile"


# ── Where a scoped token may knock at all ────────────────────────────


def test_path_normalization_strips_both_mounts_and_a_trailing_slash():
    from interfaces.api.deps import normalize_api_path
    assert normalize_api_path("/api/v1/map/vehicles/") == "/map/vehicles"
    assert normalize_api_path("/api/map/vehicles/live") == "/map/vehicles/live"
    assert normalize_api_path("/api/extension/me") == "/extension/me"
    assert normalize_api_path("/api") == "/"
    assert normalize_api_path("") == "/"
    assert normalize_api_path("/api/v1") == "/"


@pytest.mark.asyncio
async def test_a_scoped_token_is_refused_outside_its_routes_with_403(monkeypatch):
    """"Cannot", not "does not": a lifted panel token knocking on the
    profile, the package download or a custom-layer write is turned
    away before any handler runs — 403, so a stray call does not make
    the panel drop its token, and no fall-through to a cookie."""
    from interfaces.api import deps

    async def _not_revoked(_jti):
        return False
    monkeypatch.setattr(deps, "_is_revoked_with_cache", _not_revoked)
    scoped = create_jwt(1, 42, "owner", user_id=7, jti="ext-1",
                        aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)

    for path in ("/api/user/me", "/api/v1/user/me", "/api/extension/info",
                 "/api/extension/download", "/api/map/custom-layers", "/api/vehicles",
                 "/api/extension/connect"):
        with pytest.raises(HTTPException) as e:
            await deps.get_current_user(_req(path)(), authorization=f"Bearer {scoped}", auth_token=None)
        assert e.value.status_code == 403, path

    for path in ("/api/map/vehicles", "/api/v1/map/vehicles", "/api/map/vehicles/live",
                 "/api/v1/map/vehicles/live/", "/api/extension/me"):
        user = await deps.get_current_user(_req(path)(), authorization=f"Bearer {scoped}", auth_token=None)
        assert user["aud"] == "extension", path

    # The same person's ordinary token goes everywhere it always did.
    full = create_jwt(1, 42, "owner", user_id=7, jti="dash-1")
    assert (await deps.get_current_user(_req("/api/user/me")(), authorization=f"Bearer {full}", auth_token=None))["sub"] == "1"


@pytest.mark.asyncio
async def test_a_scoped_bearer_is_not_rescued_by_the_cookie_behind_it(monkeypatch):
    """Bearer first, cookie second is how a stale dashboard token falls
    through to a fresh cookie.  For a scoped token outside its routes
    that fall-through would be an escalation — so it is a hard stop."""
    from interfaces.api import deps

    async def _not_revoked(_jti):
        return False
    monkeypatch.setattr(deps, "_is_revoked_with_cache", _not_revoked)
    scoped = create_jwt(1, 42, "owner", user_id=7, jti="ext-2",
                        aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)
    cookie = create_jwt(1, 42, "owner", user_id=7, jti="dash-2")
    with pytest.raises(HTTPException) as e:
        await deps.get_current_user(_req("/api/user/me")(), authorization=f"Bearer {scoped}", auth_token=cookie)
    assert e.value.status_code == 403


def test_every_listed_route_exists_so_a_rename_breaks_ci_not_the_panel():
    from interfaces.api.auth import EXTENSION_ROUTES
    from interfaces.api.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for route in EXTENSION_ROUTES:
        assert f"/api{route}" in paths, f"{route} is not mounted under /api"
        assert f"/api/v1{route}" in paths, f"{route} is not mounted under /api/v1"


@pytest.mark.asyncio
async def test_logout_revokes_an_extension_session_instead_of_shrugging(monkeypatch):
    """A raw jwt.decode refuses any token with an ``aud``, so the panel's
    logout used to log "already invalid?" and answer ok — leaving the
    session live.  Now the row is revoked and the jti denylisted."""
    from starlette.responses import Response
    revoked, denylisted = [], []

    class _DB:
        async def revoke_user_session_by_jti(self, jti):
            revoked.append(jti)
            return {"expires_at": "2099-01-01T00:00:00+00:00"}
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db", lambda: _DB())

    async def _mark(jti, expires_at=None):
        denylisted.append(jti)
    monkeypatch.setattr(auth_mod, "mark_jti_revoked", _mark)

    tok = create_jwt(1, 42, "owner", user_id=7, jti="ext-3",
                     aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE)

    class _Req:
        headers = {"user-agent": "x"}
        client = None
    await auth_mod.auth_logout(_Req(), Response(), authorization=f"Bearer {tok}", auth_token=None)
    assert revoked == ["ext-3"]
    assert denylisted == ["ext-3"]
