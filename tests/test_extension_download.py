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
    assert res.headers["content-disposition"].startswith("attachment")

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
async def test_extension_me_is_three_display_strings_and_nothing_else(monkeypatch):
    """The panel's token is a live-map key.  /user/me would hand it the
    permission matrix, the email and the company list; this endpoint
    hands it an avatar's worth and no more."""
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

    out = await ext.extension_me(user={"sub": "1", "uid": 7, "account_id": 42, "role": "owner",
                                       "aud": "extension", "scope": ["can_location_map"]})
    assert out == {"display_name": "Allen Klein", "role": "owner",
                   "account_name": "Premier Trucking Group"}
    assert "email" not in out and "permissions" not in out
