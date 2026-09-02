"""The extension is fetched from the product, by a signed-in person."""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import HTTPException

from interfaces.api.routes import extension as ext


@pytest.mark.asyncio
async def test_a_signed_in_user_gets_the_built_package(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "chunks").mkdir(parents=True)
    (dist / "manifest.json").write_text('{"manifest_version":3,"version":"0.1.0"}')
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
    assert info == {"built": True, "version": "0.1.0",
                    "extension_id": "bpfmimpagohdiafleecmpkkcglohcbge"}


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
