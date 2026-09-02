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


def test_login_from_the_extension_mints_a_scoped_token_and_no_cookie():
    import inspect
    src = inspect.getsource(auth_mod.auth_email_login)
    assert 'body.client == EXTENSION_AUDIENCE' in src
    assert 'device_label="Browser extension"' in src
    assert 'if not is_extension:' in src, "the extension must not also receive an unscoped cookie"
