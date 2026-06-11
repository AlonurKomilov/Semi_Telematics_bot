"""Driver Module — document-expiration scheduler tests.

Two layers:
  * Pure-helper tests (``classify_documents`` / ``_bucket_for``) — no DB,
    no bot.  Verifies the bucket math and past-due detection.
  * Adapter dedup tests — exercises ``record_doc_notification`` against
    the real SQLite migrations so the composite-PK contract is asserted.
  * Bot-job integration test — wires the scheduler against a stubbed
    bot and platform DB and verifies the end-to-end alert + dedup
    + status-flip behaviour.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from adapters.storage.drivers import DriverDocument
from features.drivers.expiration import (
    BUCKETS,
    classify_documents,
    format_alert,
    _bucket_for,
    _days_until,
    ExpiringDoc,
)


# ── Pure helper tests ──────────────────────────────────────────────


def _doc(days: int, *, doc_id: int = 1, status: str = "active") -> DriverDocument:
    """Build a synthetic DriverDocument expiring ``days`` from today."""
    today = _dt.datetime.now(_dt.timezone.utc).date()
    expires = (today + _dt.timedelta(days=days)).isoformat()
    return DriverDocument(
        id=doc_id, account_id=1, user_id=10, doc_type="cdl",
        bucket="driver_documents", object_key="k", drive_file_id=None,
        file_name="cdl.pdf", file_size=1000, mime_type="application/pdf",
        issued_at=None, expires_at=expires, status=status,
        uploaded_by=0, uploaded_at="2026-01-01", notes=None,
    )


class TestBucketing:
    def test_bucket_choice_is_smallest_applicable(self):
        # T-0 / T-1 / T-7 / T-14 / T-30 — the helper returns the smallest
        # bucket that's >= days_until.
        assert _bucket_for(0) == 0
        assert _bucket_for(1) == 1
        assert _bucket_for(2) == 7
        assert _bucket_for(7) == 7
        assert _bucket_for(8) == 14
        assert _bucket_for(14) == 14
        assert _bucket_for(15) == 30
        assert _bucket_for(30) == 30
        # Out of band — no alert for >30 days, and past-due is handled separately.
        assert _bucket_for(31) is None
        assert _bucket_for(-1) is None

    def test_known_buckets_constant(self):
        # If we ever change the bucket set, the dashboard chip colors
        # and the format_alert copy need to follow — this guard makes
        # that intentional rather than accidental.
        assert BUCKETS == (0, 1, 7, 14, 30)

    def test_days_until_handles_malformed(self):
        today = _dt.datetime.now(_dt.timezone.utc)
        assert _days_until(today, "") is None
        assert _days_until(today, "not-a-date") is None
        # ISO date with time component — strips to the date portion.
        future = (today.date() + _dt.timedelta(days=5)).isoformat()
        assert _days_until(today, future + "T00:00:00") == 5


class TestClassify:
    def test_past_due_active_goes_to_expired_list(self):
        docs = [_doc(-3, doc_id=11, status="active")]
        alerts, expired = classify_documents(docs)
        assert alerts == []
        assert len(expired) == 1
        assert expired[0].id == 11

    def test_past_due_already_expired_is_skipped(self):
        # Already-expired docs don't need flipping again.
        docs = [_doc(-3, doc_id=12, status="expired")]
        alerts, expired = classify_documents(docs)
        assert alerts == []
        assert expired == []

    def test_doc_at_exact_threshold_picks_that_bucket(self):
        docs = [
            _doc(0, doc_id=20),
            _doc(1, doc_id=21),
            _doc(7, doc_id=22),
            _doc(14, doc_id=23),
            _doc(30, doc_id=24),
        ]
        alerts, _ = classify_documents(docs)
        by_id = {a.doc_id: a.bucket for a in alerts}
        assert by_id == {20: 0, 21: 1, 22: 7, 23: 14, 24: 30}

    def test_doc_beyond_window_is_not_an_alert(self):
        docs = [_doc(31, doc_id=30)]
        alerts, expired = classify_documents(docs)
        assert alerts == []
        assert expired == []

    def test_doc_with_no_expires_is_skipped(self):
        d = _doc(10)
        d2 = DriverDocument(**{**d.__dict__, "expires_at": None})
        alerts, expired = classify_documents([d2])
        assert alerts == []
        assert expired == []


class TestFormatAlert:
    def test_t0_message_is_distinct_for_driver_vs_admin(self):
        doc = ExpiringDoc(
            doc_id=1, account_id=1, user_id=10, doc_type="cdl",
            file_name="cdl.pdf", expires_at="2026-05-13",
            days_until=0, bucket=0,
        )
        driver = format_alert(doc, for_driver=True).lower()
        admin = format_alert(doc, for_driver=False).lower()
        assert "expired today" in driver
        # Driver framing has an "upload" CTA — admin framing doesn't.
        assert "upload" in driver
        assert "upload" not in admin

    def test_advance_warning_includes_bucket_days(self):
        doc = ExpiringDoc(
            doc_id=1, account_id=1, user_id=10, doc_type="medical_card",
            file_name="med.pdf", expires_at="2026-06-10",
            days_until=5, bucket=7,
        )
        msg = format_alert(doc, for_driver=True)
        assert "Medical card" in msg
        assert "7 days" in msg
        assert "med.pdf" in msg


# ── Dedup ledger tests ────────────────────────────────────────────


@pytest.fixture
async def db(pg_db):
    yield pg_db


class TestNotificationLedger:
    async def test_first_insert_returns_true_then_dedup_returns_false(self, db):
        # Need a real doc row so the FK semantics in production are
        # at least surfaced (we don't enforce FKs on this table for
        # ease of cleanup, but the insert must succeed).
        acct = await db.create_account("X")
        user = await db.create_user(501, acct.id, role=Role.DRIVER)
        doc = await db.add_document(
            account_id=acct.id, user_id=user.id, doc_type="cdl",
            bucket="b", object_key="k", file_name="f.pdf",
        )
        ok = await db.record_doc_notification(doc.id, 7)
        assert ok is True
        # Same bucket → dedup hit
        again = await db.record_doc_notification(doc.id, 7)
        assert again is False
        # Different bucket on same doc → newly inserted
        other = await db.record_doc_notification(doc.id, 14)
        assert other is True
        # Read-back diagnostic
        buckets = await db.get_doc_notification_buckets(doc.id)
        assert set(buckets) == {7, 14}


# ── End-to-end bot job test ───────────────────────────────────────


class _FakeAccount:
    def __init__(self, id_: int):
        self.id = id_


@pytest.fixture
async def expiry_app(pg_db):
    """Spin up an isolated DB seeded with one expiring doc + one
    past-due doc + admin/driver users, plus the in-memory platform-DB
    wiring the scheduler imports use."""
    db = pg_db

    acct = await db.create_account("X")
    admin = await db.create_user(900, acct.id, role=Role.OWNER, display_name="Boss")
    driver = await db.create_user(901, acct.id, role=Role.DRIVER, display_name="Jane Doe")

    today = _dt.datetime.now(_dt.timezone.utc).date()
    soon = (today + _dt.timedelta(days=7)).isoformat()
    past = (today - _dt.timedelta(days=2)).isoformat()
    doc_soon = await db.add_document(
        account_id=acct.id, user_id=driver.id, doc_type="cdl",
        bucket="b", object_key="k1", file_name="cdl.pdf",
        expires_at=soon,
    )
    doc_past = await db.add_document(
        account_id=acct.id, user_id=driver.id, doc_type="medical_card",
        bucket="b", object_key="k2", file_name="med.pdf",
        expires_at=past,
    )

    # Wire infra.platform so get_platform_db() returns our test db,
    # mirroring how API tests bootstrap the legacy single-DB mode.
    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = db

    yield {
        "db": db, "acct": acct, "admin": admin, "driver": driver,
        "doc_soon": doc_soon, "doc_past": doc_past,
    }

    _cp._db = _old_db
    # db.close() handled by the pg_db fixture's own teardown


class TestSchedulerJob:
    async def test_fires_one_alert_per_party_and_flips_expired(self, expiry_app):
        ctx = expiry_app
        # Stub bot + bot_registry so we count outgoing messages instead
        # of touching Telegram.
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        fake_app = MagicMock()
        fake_app.bot = fake_bot

        # Force the per-account local-hour gate to fire so the test
        # doesn't depend on wall-clock time.  ``is_local_hour`` is now
        # consulted to skip 23/24 hourly ticks per account; we simulate
        # the one hour where it does fire.
        with patch(
            "features.drivers.documents.alert.get_app_for_account",
            return_value=fake_app,
        ), patch(
            "features.drivers.documents.alert.is_local_hour",
            new=AsyncMock(return_value=True),
        ):
            from features.drivers.documents.alert import check_document_expirations
            await check_document_expirations(None)

        # Past-due doc was flipped.
        cur = await ctx["db"]._db.execute(
            "SELECT status FROM driver_documents WHERE id = ?",
            (ctx["doc_past"].id,),
        )
        row = await cur.fetchone()
        assert row[0] == "expired"

        # T-7 alert went to one driver + one admin (= 2 messages).
        # The past-due doc was flipped (no alert — it's expired now,
        # not in the "T-N advance" window).
        assert fake_bot.send_message.call_count == 2
        recipients = sorted(
            call.kwargs["chat_id"] for call in fake_bot.send_message.call_args_list
        )
        assert recipients == sorted([ctx["admin"].telegram_id, ctx["driver"].telegram_id])

        # Dedup row recorded for the T-7 bucket on the soon-doc.
        buckets = await ctx["db"].get_doc_notification_buckets(ctx["doc_soon"].id)
        assert buckets == [7]

    async def test_rerun_same_day_is_a_noop(self, expiry_app):
        _ = expiry_app  # fixture seeds the DB + patches infra.platform
        fake_bot = MagicMock()
        fake_bot.send_message = AsyncMock()
        fake_app = MagicMock()
        fake_app.bot = fake_bot

        with patch(
            "features.drivers.documents.alert.get_app_for_account",
            return_value=fake_app,
        ), patch(
            "features.drivers.documents.alert.is_local_hour",
            new=AsyncMock(return_value=True),
        ):
            from features.drivers.documents.alert import check_document_expirations
            await check_document_expirations(None)
            first_count = fake_bot.send_message.call_count
            # Re-run — every alert is in dedup, every past-due doc is
            # already flipped.
            await check_document_expirations(None)

        assert fake_bot.send_message.call_count == first_count
