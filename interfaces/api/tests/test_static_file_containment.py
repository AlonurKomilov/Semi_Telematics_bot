"""Exercise the mounted SPA routes with files outside their public roots."""

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


@pytest.mark.parametrize("prefix", ["dashboard", "miniapp"])
async def test_spa_routes_contain_files_and_keep_navigation(tmp_path, monkeypatch, prefix):
    from interfaces.api import app as api
    for name in ("dashboard", "miniapp"):
        public = tmp_path / name / "dist"
        (public / "assets").mkdir(parents=True)
        (public / "index.html").write_text("PUBLIC_HTML")
        (public / "assets/app.js").write_text("PUBLIC_JS")
        (public.parent / "outside.txt").write_text("PRIVATE_MARKER")
        (public / "escape.txt").symlink_to(public.parent / "outside.txt")
    monkeypatch.setattr(api, "__file__", str(tmp_path / "api" / "app.py"))
    app = api.create_api()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("..%2Foutside.txt", "%2e%2e/outside.txt", "escape.txt",
                     "%2F" + str(tmp_path / prefix / "outside.txt").lstrip("/")):
            response = await client.get(f"/{prefix}/{path}")
            assert response.status_code == 404, path
            assert "PRIVATE_MARKER" not in response.text
        assert (await client.get(f"/{prefix}/assets/app.js")).text == "PUBLIC_JS"
        assert (await client.get(f"/{prefix}/vehicles/123")).text == "PUBLIC_HTML"
        if prefix == "dashboard":
            assert (await client.get(f"/{prefix}/assets/missing.js")).status_code == 404
