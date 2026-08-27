"""Invoice → WO extraction: the coercion layer + the endpoint guards.

The vision call is always mocked — CI never hits a live model.  Pins
the trust posture: every model value re-typed/clamped server-side,
totals cross-checked (mismatch WARNS, never silently trusts), magic
bytes beat the declared Content-Type, and the permission matrix
(``can_work_orders_all``) is the only access gate.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from features.work_orders.extraction import _parse_model_json, coerce_extract

JPG = b"\xFF\xD8\xFF\xE0" + b"\x00" * 40
PDF = b"%PDF-1.4" + b"\x00" * 40
EXE = b"MZ\x90\x00" + b"\x00" * 40
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40


# ── model-reply parsing (no AI, no DB) ──────────────────────────────

class TestParseModelJson:
    def test_plain_and_fenced(self):
        assert _parse_model_json('{"total": 5}') == {"total": 5}
        assert _parse_model_json('```json\n{"total": 5}\n```') == {"total": 5}

    def test_prose_wrapped_object_recovered(self):
        assert _parse_model_json('Here you go: {"total": 5} hope it helps') == {"total": 5}

    def test_junk_and_non_dict(self):
        assert _parse_model_json("cannot read this") is None
        assert _parse_model_json("[1, 2]") is None
        assert _parse_model_json("") is None


class TestCoerceExtract:
    def _raw(self, **over):
        base = {
            "vendor": {"name": "Shop A", "phone": "555", "email": "a@b.c", "address": "1 Main"},
            "invoice_number": "INV-9",
            "service_date": "2026-07-20",
            "odometer": 123456,
            "vehicle_hint": "234",
            "line_items": [
                {"description": "Filter", "kind": "part", "quantity": 2, "unit_price": 45, "total": 90},
                {"description": "Diag", "kind": "labor", "quantity": 1, "unit_price": 120, "total": 120},
            ],
            "fee": 15, "tax": 8.5, "total": 233.5,
            "confidence": {"vendor": 1, "line_items": 0.9, "totals": 1, "meta": 0.8},
            "notes": "ok",
        }
        base.update(over)
        return base

    def test_happy_path_no_warnings(self):
        out = coerce_extract(self._raw())
        assert out["warnings"] == []
        f = out["fields"]
        assert f["vendor"]["name"] == "Shop A"
        assert f["total"] == 233.5 and f["fee"] == 15 and f["tax"] == 8.5
        assert [li["kind"] for li in f["line_items"]] == ["part", "labor"]

    def test_totals_mismatch_warns_not_trusts(self):
        out = coerce_extract(self._raw(total=999))
        assert any(w.startswith("totals_mismatch") for w in out["warnings"])
        assert out["fields"]["total"] == 999   # printed total kept, flagged

    def test_missing_total_computed_from_lines(self):
        out = coerce_extract(self._raw(total=0))
        assert out["fields"]["total"] == 233.5
        assert out["warnings"] == []

    def test_garbage_types_coerced_never_raise(self):
        out = coerce_extract({
            "vendor": "not a dict", "invoice_number": 12,
            "service_date": "July 4th", "odometer": "many",
            "line_items": [{"description": "X", "quantity": "NaN",
                            "unit_price": -5, "total": "1e999"}, "junk", 42],
            "fee": "free", "tax": None, "total": float("inf"),
            "confidence": {"vendor": 7, "line_items": -1},
        })
        f = out["fields"]
        assert f["vendor"] == {"name": "", "phone": "", "email": "", "address": ""}
        assert f["invoice_number"] == ""            # non-str dropped
        assert f["service_date"] == ""              # unparseable → dropped
        assert "bad_service_date" in out["warnings"]
        assert f["odometer"] is None
        assert len(f["line_items"]) == 1            # dict-only, desc required
        li = f["line_items"][0]
        assert li["unit_price"] == 0 and li["total"] == 0 and li["quantity"] == 1
        assert f["fee"] == 0 and f["tax"] == 0 and f["total"] == 0
        assert out["confidence"]["vendor"] == 1.0   # clamped into [0, 1]
        assert out["confidence"]["line_items"] == 0.0

    def test_line_reconstruction_and_caps(self):
        out = coerce_extract(self._raw(line_items=[
            {"description": "A", "kind": "part", "quantity": 4, "unit_price": 25, "total": 0},
            {"description": "B", "kind": "part", "quantity": 2, "unit_price": 0, "total": 30},
        ], fee=0, tax=0, total=0))
        a, b = out["fields"]["line_items"]
        assert a["total"] == 100.0       # qty × unit filled in
        assert b["unit_price"] == 15.0   # total ÷ qty filled in

    def test_line_items_truncated_at_cap(self):
        items = [{"description": f"P{i}", "quantity": 1, "unit_price": 1, "total": 1}
                 for i in range(80)]
        out = coerce_extract(self._raw(line_items=items, fee=0, tax=0, total=60))
        assert len(out["fields"]["line_items"]) == 60
        assert "line_items_truncated" in out["warnings"]

    def test_control_chars_stripped_email_validated(self):
        out = coerce_extract(self._raw(vendor={
            "name": "Shop\x00\x01 B", "phone": "555", "email": "not-an-email", "address": "",
        }))
        assert out["fields"]["vendor"]["name"] == "Shop B"
        assert out["fields"]["vendor"]["email"] == ""


# ── the endpoint (vision mocked; permission + magic-byte gates) ─────

@pytest_asyncio.fixture
async def api(pg_db):
    from adapters.storage import Role
    import infra.platform as cp
    cp._db = pg_db
    acct = await pg_db.create_account("Scan Co")
    owner = await pg_db.create_user(920001, acct.id, role=Role.OWNER)
    driver = await pg_db.create_user(920002, acct.id, role=Role.DRIVER)
    from interfaces.api.app import create_api
    from interfaces.api.auth import create_jwt
    app = create_api()
    return {
        "app": app, "db": pg_db, "acct": acct,
        "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


GOOD_EXTRACT = {
    "ok": True,
    "fields": {"vendor": {"name": "Shop A", "phone": "", "email": "", "address": ""},
               "invoice_number": "INV-9", "service_date": "2026-07-20",
               "odometer": None, "vehicle_hint": "", "line_items": [],
               "fee": 0, "tax": 0, "total": 0},
    "confidence": {"vendor": 1, "line_items": 0, "totals": 0, "meta": 1},
    "notes": "", "warnings": [],
}


class TestExtractInvoiceEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_pdf_and_heic(self, api, monkeypatch):
        seen = []

        async def fake(raw, mime, *, account_id, user_id=None, role=None):
            seen.append(mime)
            return dict(GOOD_EXTRACT)
        monkeypatch.setattr("features.work_orders.extraction.extract_invoice", fake)

        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.post("/api/work-orders/extract-invoice", headers=_h(api["owner"]),
                             files={"file": ("inv.pdf", PDF, "application/pdf")})
            assert r.status_code == 200, r.text
            assert r.json()["fields"]["invoice_number"] == "INV-9"
            r = await c.post("/api/work-orders/extract-invoice", headers=_h(api["owner"]),
                             files={"file": ("inv.heic", HEIC, "image/heic")})
            assert r.status_code == 200, r.text
        assert seen == ["application/pdf", "image/heic"]

    @pytest.mark.asyncio
    async def test_magic_bytes_beat_declared_type(self, api, monkeypatch):
        async def fake(raw, mime, **kw):   # pragma: no cover — must not run
            raise AssertionError("engine must not be called for a fake JPEG")
        monkeypatch.setattr("features.work_orders.extraction.extract_invoice", fake)

        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.post("/api/work-orders/extract-invoice", headers=_h(api["owner"]),
                             files={"file": ("evil.jpg", EXE, "image/jpeg")})
        assert r.status_code == 415

    @pytest.mark.asyncio
    async def test_driver_permission_denied(self, api):
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.post("/api/work-orders/extract-invoice", headers=_h(api["driver"]),
                             files={"file": ("inv.jpg", JPG, "image/jpeg")})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_engine_failure_is_502_with_reason(self, api, monkeypatch):
        async def fake(raw, mime, **kw):
            return {"ok": False, "error": "The AI service is unavailable right now — try again in a minute."}
        monkeypatch.setattr("features.work_orders.extraction.extract_invoice", fake)

        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.post("/api/work-orders/extract-invoice", headers=_h(api["owner"]),
                             files={"file": ("inv.jpg", JPG, "image/jpeg")})
        assert r.status_code == 502
        assert "unavailable" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_file_rejected(self, api):
        async with AsyncClient(transport=ASGITransport(app=api["app"]),
                               base_url="http://t") as c:
            r = await c.post("/api/work-orders/extract-invoice", headers=_h(api["owner"]),
                             files={"file": ("inv.jpg", b"", "image/jpeg")})
        assert r.status_code == 422
