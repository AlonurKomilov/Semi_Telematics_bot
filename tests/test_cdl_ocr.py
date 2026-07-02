"""Tests for the CDL fast-fill (photo → prefilled fields).

The vision call is always mocked — CI never hits a live model.  Coverage:
the response parser + sanitizer (never trust model output raw) and the
public endpoint's guards (token gate, image-only, best-effort contract).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/cdl_ocr_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from features.applications.ocr import _parse_response, _sanitize_fields

JPG = b"\xFF\xD8\xFF\xE0" + b"\x00" * 40
PDF = b"%PDF-1.4" + b"\x00" * 40
EXE = b"MZ\x90\x00" + b"\x00" * 40


# ── parser + sanitizer units (no AI, no DB) ─────────────────────────

class TestParseResponse:
    def test_plain_json(self):
        assert _parse_response('{"first": "Jane"}') == {"first": "Jane"}

    def test_fenced_json(self):
        assert _parse_response('```json\n{"first": "Jane"}\n```') == {"first": "Jane"}

    def test_junk_and_non_dict(self):
        assert _parse_response("I could not read the image") is None
        assert _parse_response('["a"]') is None
        assert _parse_response("") is None


class TestSanitizeFields:
    def test_full_valid_payload(self):
        out = _sanitize_fields({
            "first": " Jane ", "middle": "Q", "last": "Roe",
            "dob": "05/05/1992", "addr1": "1 Main St", "city": "Austin",
            "state": "tx", "zip": "78701", "number": "12345678",
            "issue_state": "TX", "cdl_class": "a", "exp": "2027-01-31",
            "endorsements": ["H", "N"], "restrictions": "Corrective lenses",
        })
        assert out is not None
        assert out["first"] == "Jane" and out["dob"] == "1992-05-05"
        assert out["state"] == "TX" and out["cdl_class"] == "A"
        assert out["exp"] == "2027-01-31" and out["endorsements"] == ["H", "N"]

    def test_x_endorsement_folds_to_h_and_n(self):
        out = _sanitize_fields({"endorsements": ["X", "T"]})
        assert out is not None and out["endorsements"] == ["H", "N", "T"]

    def test_invalid_values_dropped_not_guessed(self):
        out = _sanitize_fields({
            "state": "ZZ", "cdl_class": "D", "zip": "abc", "dob": "soon",
            "exp": "unknown", "endorsements": ["Q"], "first": "Jane",
        })
        assert out == {"first": "Jane"}   # everything invalid vanished

    def test_zip_normalised_not_rejected(self):
        # Models return the same code three ways — all normalise.
        assert _sanitize_fields({"zip": "29609"})["zip"] == "29609"
        assert _sanitize_fields({"zip": "29609-5442"})["zip"] == "29609-5442"
        assert _sanitize_fields({"zip": "296095442"})["zip"] == "29609-5442"
        assert _sanitize_fields({"zip": "2960", "first": "J"}) == {"first": "J"}

    def test_not_a_license_or_empty(self):
        assert _sanitize_fields({"not_license": True, "first": "Jane"}) is None
        assert _sanitize_fields({}) is None
        assert _sanitize_fields({"first": "\x00\x01"}) is None

    def test_unknown_keys_never_pass_through(self):
        out = _sanitize_fields({"first": "Jane", "evil": "<script>", "role": "owner"})
        assert out == {"first": "Jane"}

    def test_lengths_capped(self):
        out = _sanitize_fields({"first": "A" * 500})
        assert out is not None and len(out["first"]) == 50


# ── the public endpoint (vision mocked) ─────────────────────────────

@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


async def _link(db):
    acct = await db.create_account("OCR Co")
    return await db.create_application_link(acct.id, label="test")


class TestOcrEndpoint:
    async def test_happy_path_returns_fields(self, api, monkeypatch):
        app, db = api
        link = await _link(db)

        async def fake_extract(image_bytes, account_id):
            assert image_bytes.startswith(b"\xFF\xD8") and account_id == link["account_id"]
            return {"first": "Jane", "cdl_class": "A"}
        monkeypatch.setattr("features.applications.ocr.extract_cdl_fields", fake_extract)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/ocr-cdl",
                             data={"token": link["token"]},
                             files={"file": ("cdl.jpg", JPG, "image/jpeg")})
        assert r.status_code == 200, r.text
        assert r.json() == {"fields": {"first": "Jane", "cdl_class": "A"}}

    async def test_extraction_failure_is_soft(self, api, monkeypatch):
        app, db = api
        link = await _link(db)

        async def fake_extract(image_bytes, account_id):
            return None
        monkeypatch.setattr("features.applications.ocr.extract_cdl_fields", fake_extract)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/ocr-cdl",
                             data={"token": link["token"]},
                             files={"file": ("cdl.jpg", JPG, "image/jpeg")})
        assert r.status_code == 200 and r.json() == {"fields": None}

    async def test_invalid_token_404(self, api):
        app, _db = api
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/ocr-cdl",
                             data={"token": "nope"},
                             files={"file": ("cdl.jpg", JPG, "image/jpeg")})
        assert r.status_code == 404

    async def test_non_image_rejected(self, api):
        app, db = api
        link = await _link(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for name, blob, mime in (("x.exe", EXE, "application/octet-stream"),
                                     ("x.pdf", PDF, "application/pdf")):
                r = await c.post("/api/applications/ocr-cdl",
                                 data={"token": link["token"]},
                                 files={"file": (name, blob, mime)})
                assert r.status_code == 422, name
