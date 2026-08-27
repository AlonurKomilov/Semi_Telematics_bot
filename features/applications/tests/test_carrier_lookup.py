"""Tests for the FMCSA employer autocomplete (carrier lookup).

The upstream registries are always mocked — CI never hits FMCSA/Socrata.
Covers query sanitization, the provider chain (QCMobile when keyed →
census fallback), the TTL cache, and the public endpoint's guards.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from features.applications import carrier_lookup as cl


@pytest.fixture(autouse=True)
def _fresh_cache():
    cl._CACHE.clear()
    yield
    cl._CACHE.clear()


class TestSanitize:
    def test_clean_query(self):
        assert cl._clean_query("  osy group ") == "OSY GROUP"
        assert cl._clean_query("o'brien & sons-co.") == "O'BRIEN & SONS-CO."
        # Structural characters are stripped (quotes/parens/semicolons);
        # apostrophes survive for real names and are SoQL-escaped at query
        # build time (_census_search doubles them).  Length capped.
        assert cl._clean_query("x') OR 1=1 --\";") == "X' OR 11 --"
        assert len(cl._clean_query("A" * 200)) == 60


class TestProviderChain:
    async def test_census_when_no_key_and_cache(self, monkeypatch):
        calls = {"census": 0, "qc": 0}
        async def fake_census(q, limit):
            calls["census"] += 1
            return [{"name": "ACME", "dba": "", "city": "AUSTIN", "state": "TX",
                     "phone": "5551000", "dot_number": "1", "active": True}]
        async def fake_qc(q, limit, key):
            calls["qc"] += 1
            return []
        monkeypatch.delenv("FMCSA_WEBKEY", raising=False)
        monkeypatch.setattr(cl, "_census_search", fake_census)
        monkeypatch.setattr(cl, "_qcmobile_search", fake_qc)

        out = await cl.search_carriers("acme transport")
        assert out[0]["name"] == "ACME" and calls == {"census": 1, "qc": 0}
        # Same query again → served from cache, no new upstream call.
        await cl.search_carriers("acme transport")
        assert calls["census"] == 1

    async def test_qcmobile_preferred_when_keyed(self, monkeypatch):
        async def fake_census(q, limit):
            return [{"name": "FROM-CENSUS", "dba": "", "city": "", "state": "",
                     "phone": "", "dot_number": "2", "active": True}]
        async def fake_qc(q, limit, key):
            assert key == "the-key"
            return [{"name": "FROM-QC", "dba": "", "city": "", "state": "",
                     "phone": "", "dot_number": "3", "active": True}]
        monkeypatch.setenv("FMCSA_WEBKEY", "the-key")
        monkeypatch.setattr(cl, "_census_search", fake_census)
        monkeypatch.setattr(cl, "_qcmobile_search", fake_qc)
        out = await cl.search_carriers("acme")
        assert out[0]["name"] == "FROM-QC"

    async def test_qcmobile_empty_falls_back_to_census(self, monkeypatch):
        async def fake_census(q, limit):
            return [{"name": "FROM-CENSUS", "dba": "", "city": "", "state": "",
                     "phone": "", "dot_number": "2", "active": True}]
        async def fake_qc(q, limit, key):
            return []
        monkeypatch.setenv("FMCSA_WEBKEY", "the-key")
        monkeypatch.setattr(cl, "_census_search", fake_census)
        monkeypatch.setattr(cl, "_qcmobile_search", fake_qc)
        out = await cl.search_carriers("acme")
        assert out[0]["name"] == "FROM-CENSUS"

    async def test_short_query_and_upstream_failure_are_empty(self, monkeypatch):
        assert await cl.search_carriers("ab") == []
        async def boom(q, limit):
            raise RuntimeError("upstream down")
        monkeypatch.delenv("FMCSA_WEBKEY", raising=False)
        monkeypatch.setattr(cl, "_census_search", boom)
        assert await cl.search_carriers("acme") == []


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


class TestEndpoint:
    async def test_token_gate_and_happy_path(self, api, monkeypatch):
        app, db = api
        acct = await db.create_account("Lookup Co")
        link = await db.create_application_link(acct.id, label="t")

        async def fake_search(q, limit=8):
            return [{"name": "ACME", "dba": "", "city": "AUSTIN", "state": "TX",
                     "phone": "5551000", "dot_number": "42", "active": True}]
        monkeypatch.setattr("features.applications.carrier_lookup.search_carriers", fake_search)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/applications/carrier-lookup",
                            params={"token": "bad", "q": "acme"})
            assert r.status_code == 404
            r = await c.get("/api/applications/carrier-lookup",
                            params={"token": link["token"], "q": "ac"})
            assert r.status_code == 200 and r.json() == {"items": []}
            r = await c.get("/api/applications/carrier-lookup",
                            params={"token": link["token"], "q": "acme"})
            assert r.status_code == 200
            assert r.json()["items"][0]["dot_number"] == "42"
