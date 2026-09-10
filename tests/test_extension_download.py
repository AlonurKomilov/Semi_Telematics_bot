"""The extension is fetched from the product, by a signed-in person."""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import HTTPException

from interfaces.api.routes import extension as ext

# A public key and the id Chrome derives from it (the extension's first,
# pre-store key — kept as a fixed vector for the derivation).
KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA11rqKsO6CyQuKFatyfSsaSOstiVoQkQk"
    "BQwAs2wDYO9LxAub3dFS2qy6o5zyEtPlEB0pzXaO93aF12uJVD1NGFLE+2O/5iHV+f4ZFmOTThaZ"
    "YXeqvUuPo+1lUd1zBkdFf/wH9caF9EMDU5YmMR81WVQflaQkfNtdiqBdsPIXUifGZZaFCKA7dMdi"
    "U0kFOyecLcoMaqIH7UUndRpOXERawjZUa0bj5wL2WpP7CdPQPPPswz6bohI+pHMhCiiX3j65XdyQ"
    "IUHKSbo9HJ+ziBXuJdtC6w3NJk9Mccv/7qzaAPHZxNNjSkFov8wck7LJ7UaqB1GVzVTPcenF/lef"
    "Xt01NQIDAQAB"
)
KEY_ID = "bpfmimpagohdiafleecmpkkcglohcbge"


@pytest.mark.asyncio
async def test_a_signed_in_user_gets_the_built_package(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "chunks").mkdir(parents=True)
    (dist / "manifest.json").write_text(
        '{"manifest_version":3,"version":"0.1.0","key":"%s"}' % KEY_B64)
    (dist / "sidepanel.js").write_text("// js")
    (dist / "chunks" / "a.js").write_text("// chunk")
    monkeypatch.setattr(ext, "_DIST", dist)
    monkeypatch.setattr(ext, "_VERSION_FILE", dist / "manifest.json")

    res = await ext.download_extension(user={"account_id": 1, "sub": "1"})
    body = b"".join([chunk async for chunk in res.body_iterator])
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    # Paths inside the zip are what Chrome expects: manifest at the root.
    assert "manifest.json" in names and "chunks/a.js" in names
    # The header carries the NAME, and the name carries the build — this
    # is the only thing that tells two downloads apart in a Downloads
    # folder.  `key` is present, so this is the sideload flavour.
    assert res.headers["content-disposition"] == (
        'attachment; filename="4truck-extension-sideload-0.1.0.zip"')

    info = await ext.extension_info(user={"account_id": 1, "sub": "1"})
    assert info == {"built": True, "version": "0.1.0", "extension_id": KEY_ID}


def test_the_id_is_chromes_derivation_of_the_manifest_key():
    """The id is never a literal anywhere on the server: it is computed
    from the public key in the built manifest, the way Chrome computes
    it, so the store build, a sideload and /extension/info cannot drift."""
    assert ext.extension_id_from_key(KEY_B64) == KEY_ID
    assert len(KEY_ID) == 32 and set(KEY_ID) <= set("abcdefghijklmnop")


@pytest.mark.asyncio
async def test_a_manifest_without_a_key_reports_no_id(tmp_path, monkeypatch):
    dist = tmp_path / "dist"; dist.mkdir()
    (dist / "manifest.json").write_text('{"manifest_version":3,"version":"0.2.0"}')
    monkeypatch.setattr(ext, "_DIST", dist)
    monkeypatch.setattr(ext, "_VERSION_FILE", dist / "manifest.json")
    info = await ext.extension_info(user={"account_id": 1, "sub": "1"})
    assert info == {"built": True, "version": "0.2.0", "extension_id": ""}


@pytest.mark.asyncio
async def test_an_unbuilt_server_says_so_instead_of_serving_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ext, "_DIST", tmp_path / "nowhere")
    with pytest.raises(HTTPException) as e:
        await ext.download_extension(user={"account_id": 1, "sub": "1"})
    assert e.value.status_code == 503


def test_the_route_is_login_gated():
    """A public URL would be an anonymous copy of the package for anyone
    to poke at.  Any signed-in role may download; the panel enforces
    what each token may see."""
    import inspect
    for fn in (ext.download_extension, ext.extension_info):
        assert "Depends(get_current_user)" in inspect.getsource(fn)


@pytest.mark.asyncio
async def test_extension_me_answers_the_panel_and_leaks_nothing_else(monkeypatch):
    """The panel's token is a key to the live map, so the panel must not
    read ``/user/me``: that answer carries the whole permission matrix,
    the email and the company list.  This one carries an avatar's worth,
    plus which features the panel may OPEN and which verbs it may PRESS
    — feature ids, never permission flags, because the panel has no
    business learning the permission vocabulary.

    The key set is asserted EXACTLY, so a field added to the answer has
    to be added here too.  This guard went stale once — it still read
    "three display strings" after the feature switcher landed, and sat
    red where nobody was watching it.  A privacy wall only works while
    somebody notices it move.
    """
    from types import SimpleNamespace

    db_user = SimpleNamespace(id=7, account_id=42, display_name="Allen Klein",
                              email="allen@example.com", is_primary_owner=True)

    class _DB:
        async def get_account(self, account_id):
            assert account_id == 42
            return SimpleNamespace(name="Premier Trucking Group", plan="pro")

    async def _db_user(user, db):
        return db_user
    monkeypatch.setattr(ext, "get_current_db_user", _db_user)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db", lambda: _DB())

    # Location granted, Inventory not — so the panel is offered exactly
    # one feature and no write verb.  Stated here rather than read from
    # a database, so the answer is about the MAPPING and not about
    # whichever seed the fixture happened to carry.
    async def _perms(_user):
        return SimpleNamespace(can_view_location=True, can_view_inventory=False,
                               can_manage_inventory=False)
    monkeypatch.setattr(ext, "effective_perms", _perms)

    out = await ext.extension_me(user={"sub": "1", "uid": 7, "account_id": 42, "role": "owner",
                                       "aud": "extension", "scope": list(ext.EXTENSION_SCOPE)})
    assert out == {"display_name": "Allen Klein", "role": "owner",
                   "account_name": "Premier Trucking Group",
                   "features": ["live-map"], "abilities": [], "scope_stale": False}

    # The three things a lifted token must never learn from this route.
    assert "email" not in out and "permissions" not in out and "companies" not in out
    # …and no permission flag reaches the wire under any key.
    assert not any("can_" in str(v) for v in out.values())


@pytest.mark.asyncio
async def test_a_token_minted_before_the_scope_changed_is_told_to_refresh(monkeypatch):
    """A panel holding a stale token sees a feature the build has and
    the server will not offer, and reads it as broken.  It is told, and
    heals itself with one /auth/refresh — the alternative is waiting up
    to eight hours, or a Disconnect nobody thinks to perform."""
    from types import SimpleNamespace

    async def _db_user(user, db):
        return SimpleNamespace(id=7, account_id=42, display_name="A", email="a@b.c")
    monkeypatch.setattr(ext, "get_current_db_user", _db_user)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "get_platform_db",
                        lambda: SimpleNamespace(get_account=_no_account))

    async def _perms(_user):
        return SimpleNamespace(can_view_location=True, can_view_inventory=True,
                               can_manage_inventory=True)
    monkeypatch.setattr(ext, "effective_perms", _perms)

    base = {"sub": "1", "uid": 7, "account_id": 42, "role": "owner", "aud": "extension"}

    stale = await ext.extension_me(user={**base, "scope": ["can_location_map"]})
    assert stale["scope_stale"] is True

    fresh = await ext.extension_me(user={**base, "scope": list(ext.EXTENSION_SCOPE)})
    assert fresh["scope_stale"] is False
    # A granted manage flag reaches the panel as a VERB, not as a flag.
    assert fresh["abilities"] == ["inventory.write"]
    assert sorted(fresh["features"]) == ["inventory", "live-map"]


async def _no_account(_account_id):
    raise RuntimeError("no account row — the avatar degrades, the route does not")


def test_the_name_can_actually_reach_the_browser():
    """Setting the header is only half of it.

    The dashboard fetches this zip with a bearer token, so it arrives as
    a blob — and a blob has no name.  The client must READ
    Content-Disposition, and that header is not CORS-safelisted: an API
    served from another origin (the VITE_API_BASE deployment) hands the
    client nothing to read unless it is exposed.

    The near half of this wall lives in the dashboard
    (``src/api/contentDisposition.ts`` and its guard); this is the far
    half.  Both were once correct on the server and wrong at the
    browser, and every build landed as ``4truck-extension (10).zip``.
    """
    from tests._repo import REPO
    app_src = (REPO / "interfaces/api/app.py").read_text()
    assert 'expose_headers=["Content-Disposition"]' in app_src
