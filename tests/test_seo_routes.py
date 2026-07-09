"""Apex SEO surface — robots.txt, sitemap.xml, and the link-preview image.

These are what Google Search Console consumes; a regression here silently
de-lists the site (a missing sitemap 404s the GSC submission, a broken
robots.txt can block crawling entirely).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/seo_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api()


class TestSeoRoutes:
    async def test_robots_txt(self, api):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as c:
            r = await c.get("/robots.txt")
            assert r.status_code == 200
            assert "text/plain" in r.headers["content-type"]
            assert "Sitemap: https://4truck.us/sitemap.xml" in r.text
            # The apex allows crawling — a Disallow-all here would de-list
            # the whole marketing site.
            assert "Allow: /" in r.text
            assert "Disallow: /\n" not in r.text.replace("Disallow: /login", "")

    async def test_sitemap_xml(self, api):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as c:
            r = await c.get("/sitemap.xml")
            assert r.status_code == 200
            assert "xml" in r.headers["content-type"]
            assert "<urlset" in r.text
            for url in ("https://4truck.us/", "https://4truck.us/privacy", "https://4truck.us/terms"):
                assert f"<loc>{url}</loc>" in r.text

    async def test_og_image(self, api):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as c:
            r = await c.get("/og-image.png")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_landing_has_seo_head(self, api):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as c:
            r = await c.get("/")
            assert r.status_code == 200
            assert '<link rel="canonical" href="https://4truck.us/">' in r.text
            assert 'property="og:image"' in r.text
            assert "application/ld+json" in r.text
