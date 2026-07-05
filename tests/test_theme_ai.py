"""Tests for the AI theme maker.

The model is always mocked; coverage is the sanitizer's guarantees (valid
hex only, mid-tone surfaces nudged out of the dead zone, headings forced
readable on their header band) and the endpoint's gates.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role
from features.applications.theme_ai import (
    _fix_surface, _lum, _parse_response, _sanitize_palettes,
)


def _palette(**over):
    base = {"name": "Test", "surface": "#ffffff", "accent": "#c40000",
            "header": "#7a2a2a", "bg": "#f5f5f5", "heading": "#ffffff"}
    base.update(over)
    return base


class TestSanitizer:
    def test_valid_palettes_pass_capped_at_three(self):
        out = _sanitize_palettes({"palettes": [_palette(name=f"P{i}") for i in range(5)]})
        assert len(out) == 3 and out[0]["accent"] == "#c40000"

    def test_bad_hex_drops_the_palette(self):
        out = _sanitize_palettes({"palettes": [
            _palette(accent="red"), _palette(surface="#12345"), _palette(),
        ]})
        assert len(out) == 1

    def test_midtone_surface_gets_nudged_out_of_dead_zone(self):
        mid = "#808080"                     # lum ≈ 0.50 — the dead zone
        assert 0.42 < _lum(mid) < 0.62
        fixed = _fix_surface(mid)
        assert not 0.42 < _lum(fixed) < 0.62
        out = _sanitize_palettes({"palettes": [_palette(surface=mid)]})
        assert not 0.42 < _lum(out[0]["surface"]) < 0.62

    def test_unreadable_heading_forced_readable(self):
        # Heading nearly the same tone as the header band → replaced.
        out = _sanitize_palettes({"palettes": [
            _palette(header="#202020", heading="#303030")]})
        assert out[0]["heading"] == "#ffffff"
        out = _sanitize_palettes({"palettes": [
            _palette(header="#f0f0f0", heading="#e8e8e8")]})
        assert out[0]["heading"] == "#0a0a0a"

    def test_junk_shapes_are_empty(self):
        assert _sanitize_palettes(None) == []
        assert _sanitize_palettes({"palettes": "nope"}) == []
        assert _parse_response("I love colours!") is None
        assert _parse_response('```json\n{"palettes": []}\n```') == {"palettes": []}


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


class TestEndpoint:
    async def test_generates_for_own_company_only(self, api, monkeypatch):
        app, db = api
        acct = await db.create_account("Theme Co")
        co = await db.add_company(acct.id, "PTG", samsara_api_key="k")
        rec = await db.create_user(910001, acct.id, role=Role.RECRUITER)
        from interfaces.api.auth import create_jwt
        h = {"Authorization": f"Bearer {create_jwt(910001, acct.id, 'recruiter', user_id=rec.id)}"}

        async def fake_gen(*, account_id, logo_bytes, wishes="", seed_color=""):
            assert account_id == acct.id and wishes == "bold"
            return [_palette()]
        monkeypatch.setattr("features.applications.theme_ai.generate_theme_palettes", fake_gen)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/applications/companies/{co.id}/brand/ai-theme",
                             headers=h, json={"prompt": "bold"})
            assert r.status_code == 200, r.text
            assert r.json()["palettes"][0]["accent"] == "#c40000"
            # Unknown / foreign company → 404.
            r = await c.post("/api/applications/companies/999999/brand/ai-theme",
                             headers=h, json={"prompt": "x"})
            assert r.status_code == 404
