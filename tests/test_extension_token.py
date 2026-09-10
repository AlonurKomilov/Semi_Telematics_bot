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


def test_refresh_never_drops_the_audience():
    """A refresh that dropped aud would widen a truck-list key into an
    account key every eight hours.  Pinned at the call site."""
    import inspect
    src = inspect.getsource(auth_mod.refresh_token)
    assert 'aud=payload.get("aud")' in src


def test_refresh_reissues_the_audiences_scope_of_today():
    """The scope belongs to what the token IS, not to the day it was
    minted.  Copying the old claim froze every installed extension at
    the scope it connected with -- the panel grew Inventory and no
    existing installation could reach it.  Re-reading the audience also
    means a NARROWING reaches live tokens on the next refresh."""
    from interfaces.api.auth import _scope_for_audience
    stale = {"aud": EXTENSION_AUDIENCE, "scope": ["can_view_location"]}
    assert tuple(_scope_for_audience(stale)) == tuple(EXTENSION_SCOPE)
    # An unscoped token stays unscoped -- this must never MINT a scope.
    assert _scope_for_audience({"scope": None}) is None
    # A scope this module does not own is not ours to rewrite.
    assert _scope_for_audience({"aud": "somebody-elses", "scope": ["x"]}) == ["x"]


def test_the_panel_may_flag_an_item_and_may_not_retire_one():
    """The owner asked for the write half on 2026-09-09, and it arrived
    NARROW — which is what having two lists is for.

    The SCOPE opens ``can_manage_inventory``; the ROUTE LIST decides
    where that flag may be used.  Verify and status are listed; add,
    transfer and remove are not, so the panel's key cannot reach them
    however senior the person holding it.  Moving an item between trucks
    or retiring it is done at a desk with the registry in front of you.

    If a future route makes those reachable, this test is the argument
    it has to answer."""
    assert "can_view_inventory" in EXTENSION_SCOPE
    assert "can_manage_inventory" in EXTENSION_SCOPE
    from interfaces.api.auth import EXTENSION_ROUTES
    assert "/extension/inventory-verify" in EXTENSION_ROUTES
    assert "/extension/inventory-status" in EXTENSION_ROUTES
    for office_only in ("transfer", "remove", "items"):
        assert not any(office_only in r for r in EXTENSION_ROUTES), office_only


def test_add_is_reachable_and_the_two_that_hide_a_loss_are_not():
    """The owner asked for add on 2026-09-10 so a person who has just
    seen a new dashcam does not have to walk back to a laptop — the walk
    is where the record stops being made at all.

    It is the THIRD and last verb this key performs.  Transfer and remove
    stay unreachable, and not by omission: they are how a loss is tidied
    away ("it is on truck 5 now", "it was retired").  The scope opens the
    manage flag; this list decides where it may be used."""
    from interfaces.api.auth import EXTENSION_ROUTES
    assert "/extension/inventory-add" in EXTENSION_ROUTES
    for hides_a_loss in ("transfer", "remove"):
        assert not any(hides_a_loss in r for r in EXTENSION_ROUTES), hides_a_loss


def test_an_add_is_walled_on_the_VEHICLE_since_there_is_no_item_yet():
    """An item's wall reads the item; an add has no item to read, so the
    wall reads the vehicle — both the company and Team Management's unit
    width, so nobody records something onto a truck their own list does
    not show them."""
    import inspect
    from interfaces.api.routes import extension
    body = inspect.getsource(extension._writable_vehicle).split('"""', 2)[2]
    assert "company_allows(" in body
    assert "filter_by_assigned_trucks(" in body
    # …and the same registry-id rung the fleet list needed.
    assert '"registry_id"' in body
    assert "inventory_service.add_item(" in inspect.getsource(extension.extension_add_item)


def test_the_write_verbs_share_the_dashboards_wall_rather_than_copying_it():
    """Two routers, one company wall and one driver snapshot.  A second
    copy of a wall is a second chance to forget a brick — so both reach
    features.inventory.service, and the dashboard's own helpers were
    rewritten to delegate rather than the extension growing its own."""
    import inspect
    from interfaces.api.routes import extension
    from features.inventory import router as dash
    ext = inspect.getsource(extension._writable_item)
    assert "inventory_service.item_if_visible(" in ext
    assert "item_if_visible(" in inspect.getsource(dash._item_or_404)
    assert "driver_on_truck(" in inspect.getsource(dash._driver_snapshot)
    # Add too: one place normalises the category, one stamps the driver.
    assert "service.add_item(" in inspect.getsource(dash.add_item)


def test_the_panel_is_told_what_it_may_do_not_which_flag_says_so():
    """``abilities`` is the panel's vocabulary, like ``features``: it
    hides a control the server would refuse instead of offering it and
    answering 403 on the press."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_me)
    assert '"inventory.write"' in src
    body = src.split("return {", 1)[1]
    assert "can_" not in body


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


def test_the_inventory_endpoint_is_gated_on_the_inventory_grant():
    """/vehicle-link rides the map the panel already shows and needs no
    gate of its own.  Inventory is a feature an owner may withhold, so a
    dispatcher denied it on the dashboard is denied it here -- which
    works only because the flag is in EXTENSION_SCOPE (the narrowing is
    an intersection, so an unlisted flag reads False for everyone)."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_inventory)
    assert 'require_permission("can_view_inventory")' in src


def test_the_panel_is_told_what_is_aboard_not_what_it_is_worth():
    """The payload line, drawn on purpose and pinned so it stays drawn.

    A category, a name and a status answer "what is on this truck and
    does any of it need attention".  ``identifier`` (a fuel-card or
    serial number) and ``notes`` (free text) answer the dashboard's
    questions, and a key that lives in a browser extension should not
    carry them.  Widening this line is a decision, not a refactor -- if
    you mean to, move this test's list rather than deleting it."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_inventory)
    body = src.split("rows = await tenant.list_vehicle_inventory", 1)[1]
    # The line has moved TWICE, each time by exactly one field and each
    # time for a reason written beside it:
    #   last_verified_at — or Verify closed a strip and changed nothing;
    #   identifier       — or you are verifying a serial you cannot see,
    #                      and the person who just typed it cannot check
    #                      it against the device.
    # Both cross for ONE vehicle, asked for.  Neither is in the fleet
    # list or on the map card.
    for key in ('"id"', '"category"', '"label"', '"status"',
                '"last_verified_at"', '"identifier"'):
        assert key in body, key
    # ``notes`` still does not cross: free text is not a fact, and a note
    # can hold anything somebody typed.
    for withheld in ('"notes"', 'r["notes"]'):
        assert withheld not in body, f"{withheld} has no business in a panel key"


def test_the_panel_is_told_features_not_flags():
    """/me answers with the panel's own vocabulary — feature ids — never
    with the permission matrix.  The mapping from grant to feature is a
    decision, so it lives here where it can be read and tested, and the
    panel stays ignorant of what a flag is called.  The verdict is the
    SCOPED one: a token that may not reach a feature is not offered it."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_me)
    assert "effective_perms(user)" in src, "the scope must narrow this too"
    assert '"features": features' in src
    # The three display strings plus the feature list, and nothing that
    # names a permission in the answer itself.
    body = src.split("return {", 1)[1]
    assert "can_" not in body, "a flag name has no business in /me's answer"


def test_a_panel_is_told_when_its_key_is_behind():
    """A token carries the scope of the day it was minted.  Widen the
    audience's scope and every installed panel is holding a key that
    cannot reach the feature its build now ships — and nobody thinks to
    Disconnect and connect again, so it reads as broken.

    /me says so, the panel spends ONE refresh (which re-mints against
    today's scope) and asks again.  The person sees nothing, which is
    the point."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_me)
    assert "set(claim) != set(EXTENSION_SCOPE)" in src
    assert '"scope_stale": scope_stale' in src
    # An unscoped token has no audience scope to be behind.
    assert 'isinstance(claim, list)' in src


def test_the_fleet_list_is_gated_and_walled():
    """The Inventory feature's opening screen is a fleet-wide read, so it
    carries both guards: the grant, and the company wall.  The
    dashboard's own /inventory/alerts skips the wall because the page's
    vehicle list is already scoped — here there is no such list, so
    skipping it would be a hole."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_inventory_fleet)
    assert 'require_permission("can_view_inventory")' in src
    assert "company_allows(" in src and "get_user_company_codes(" in src


def test_the_fleet_list_starts_from_the_registry_when_asked_for_all():
    """The panel's question is "every vehicle I may see", and the old
    shape could not answer it: folding ``list_account_inventory`` (items
    JOIN vehicles) means a vehicle with nothing recorded cannot appear,
    so its absence read as "no such vehicle".

    ``?all=1`` takes the registry as the spine and left-joins the counts.
    The bare path keeps the MAP's question — only what has something
    aboard — because the overlay card renders a count line whenever one
    is present, and a server that began answering with zeros would put
    "0 items" on every marker of every not-yet-updated extension."""
    import inspect
    from interfaces.api.routes import extension
    body = inspect.getsource(extension.extension_inventory_fleet).split('"""', 2)[2]
    assert 'alias="all"' in inspect.getsource(extension.extension_inventory_fleet)
    assert "tenant.list_vehicles(" in body, "the spine is the registry"
    assert "get_attention_map(" in body, "counts are left-joined, not the spine"
    # …and the map's branch is still there, unchanged in shape.
    assert "list_account_inventory(" in body


def test_the_fleet_list_applies_the_unit_width_the_live_map_applies():
    """A driver who sees one truck on the Live Map must not see two
    hundred in the panel beside it.  Same helper, same answer — and the
    company wall stays ``company_allows`` rather than
    ``filter_by_allowed_companies``, which reads a blank company code as
    denied and would drop the registry-only trailers and manual units
    that are nearly half this fleet."""
    import inspect
    from interfaces.api.routes import extension
    # The BODY, not the docstring — which names the rejected helper in
    # order to say why it is rejected, and would otherwise fail a guard
    # written to forbid it.
    body = inspect.getsource(extension.extension_inventory_fleet).split('"""', 2)[2]
    assert "filter_by_assigned_trucks(" in body
    assert "company_allows(" in body
    assert "filter_by_allowed_companies" not in body


def test_the_unit_scope_is_decided_on_the_registry_id_not_the_provider_id():
    """The identity ladder's second rung reads a row's ``vehicle_id`` as
    the PROVIDER id (vehicle_scope.allows_row's external_key default).
    These rows carry the REGISTRY id under that name, so without a
    ``registry_id`` the ladder would compare two different id spaces —
    a driver missing their own truck, or matching somebody else's on a
    collision.  Supplying registry_id makes rung ONE decide, and rung
    one is authoritative including when it says no.

    It is then projected off the wire: it did its work in the scope and
    has no business on a page we do not own."""
    import inspect
    from interfaces.api.routes import extension
    body = inspect.getsource(extension.extension_inventory_fleet).split('"""', 2)[2]
    assert '"registry_id": int(v.id)' in body or '"registry_id": vid' in body
    # …and both branches must carry it, or the map's rows scope wrongly.
    assert body.count('"registry_id"') >= 2
    # The wire keeps the five it always kept.
    assert 'wire = ("vehicle_id", "name", "company", "total", "attention")' in body


def test_the_fleet_list_carries_counts_not_contents():
    """One row per truck: what it is called and how much is aboard.  An
    item's own label arrives only when a truck is chosen, and its
    identifier and notes never do — same line the per-vehicle endpoint
    draws, drawn once more where it would be easiest to forget."""
    import inspect
    from interfaces.api.routes import extension
    src = inspect.getsource(extension.extension_inventory_fleet)
    body = src.split("fleet: dict[int, dict] = {}", 1)[1]
    for key in ('"vehicle_id"', '"name"', '"company"', '"total"', '"attention"'):
        assert key in body, key
    for withheld in ('"identifier"', '"notes"', '"label"'):
        assert withheld not in body, f"{withheld} is not a count"


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
