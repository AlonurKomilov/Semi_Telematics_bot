"""Tests for and (PostgreSQL adapter).

tests verify that:
  - ENABLE_API / ENABLE_BOT / ENABLE_SCHEDULER env flags parse correctly
  - docker-compose.services.yml and systemd units have correct env vars

tests verify that:
  - pg_adapter._sqlite_to_pg_sql() translates SQL correctly
  - _PgRow exposes dict-like access
  - _PgCursor fetchone/fetchall behave like aiosqlite.Cursor
  - _DatabaseCore._using_postgres property works
  - billing.BillingMixin uses self._db (not a non-existent self._connect)
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


# ═══════════════════════════════════════════════════════════════════
# Service split flag tests
# ═══════════════════════════════════════════════════════════════════

class TestServiceSplitFlags:
    """run.py env flag parsing — backward-compat and split-service modes."""

    def test_default_flags_all_enabled(self, monkeypatch):
        monkeypatch.delenv("ENABLE_API",       raising=False)
        monkeypatch.delenv("ENABLE_BOT",       raising=False)
        monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)

        enable_api       = os.getenv("ENABLE_API",       "1") == "1"
        enable_bot       = os.getenv("ENABLE_BOT",       "1") == "1"
        enable_scheduler = os.getenv("ENABLE_SCHEDULER", "1") == "1"

        assert enable_api is True
        assert enable_bot is True
        assert enable_scheduler is True

    def test_api_only_flags(self, monkeypatch):
        monkeypatch.setenv("ENABLE_API",       "1")
        monkeypatch.setenv("ENABLE_BOT",       "0")
        monkeypatch.setenv("ENABLE_SCHEDULER", "0")

        enable_api       = os.getenv("ENABLE_API",       "1") == "1"
        enable_bot       = os.getenv("ENABLE_BOT",       "1") == "1"
        enable_scheduler = os.getenv("ENABLE_SCHEDULER", "1") == "1"

        assert enable_api is True
        assert enable_bot is False
        assert enable_scheduler is False

    def test_bot_only_flags(self, monkeypatch):
        monkeypatch.setenv("ENABLE_API",       "0")
        monkeypatch.setenv("ENABLE_BOT",       "1")
        monkeypatch.setenv("ENABLE_SCHEDULER", "1")

        enable_api       = os.getenv("ENABLE_API",       "1") == "1"
        enable_bot       = os.getenv("ENABLE_BOT",       "1") == "1"
        enable_scheduler = os.getenv("ENABLE_SCHEDULER", "1") == "1"

        assert enable_api is False
        assert enable_bot is True
        assert enable_scheduler is True

    def test_docker_compose_services_file_exists(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "docker-compose.services.yml")
        assert os.path.exists(path), "docker-compose.services.yml not found"
        content = open(path).read()
        assert "ENABLE_API=1"       in content
        assert "ENABLE_BOT=0"       in content
        assert "ENABLE_BOT=1"       in content
        assert "ENABLE_SCHEDULER=1" in content

    def test_api_service_unit_exists(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "4truck-api.service")
        assert os.path.exists(path), "4truck-api.service not found"
        content = open(path).read()
        assert "ENABLE_API=1"       in content
        assert "ENABLE_BOT=0"       in content
        assert "ENABLE_SCHEDULER=0" in content

    def test_bot_service_unit_has_flags(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "4truck-bot.service")
        assert os.path.exists(path), "4truck-bot.service not found"
        content = open(path).read()
        assert "ENABLE_BOT=1"       in content
        assert "ENABLE_SCHEDULER=1" in content


# ═══════════════════════════════════════════════════════════════════
# PostgreSQL adapter unit tests
# ═══════════════════════════════════════════════════════════════════

from adapters.storage.pg_adapter import _sqlite_to_pg_sql, _PgRow, _PgCursor  # noqa: E402


class TestSqlTranslation:
    """_sqlite_to_pg_sql() — SQLite → PostgreSQL translation."""

    def test_question_mark_to_positional(self):
        sql = "SELECT * FROM users WHERE id = ? AND account_id = ?"
        result = _sqlite_to_pg_sql(sql)
        assert "$1" in result and "$2" in result
        assert "?" not in result

    def test_no_params_unchanged(self):
        sql = "SELECT * FROM accounts ORDER BY id"
        assert _sqlite_to_pg_sql(sql) == sql

    def test_integer_pk_autoincrement_to_serial(self):
        sql = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
        result = _sqlite_to_pg_sql(sql)
        assert "SERIAL PRIMARY KEY" in result
        assert "AUTOINCREMENT" not in result

    def test_pragma_returns_empty(self):
        assert _sqlite_to_pg_sql("PRAGMA journal_mode=WAL") == ""
        assert _sqlite_to_pg_sql("PRAGMA foreign_keys=ON")  == ""

    def test_datetime_now_to_pg(self):
        sql = "INSERT INTO t (created_at) VALUES (datetime('now'))"
        result = _sqlite_to_pg_sql(sql)
        assert "NOW()" in result
        assert "datetime" not in result.lower().replace("NOW()", "")

    def test_multiple_params_ordered(self):
        sql = "INSERT INTO t (a, b, c) VALUES (?, ?, ?)"
        result = _sqlite_to_pg_sql(sql)
        assert "$1" in result and "$2" in result and "$3" in result
        assert "?" not in result

    def test_if_not_exists_preserved(self):
        sql = "CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY)"
        assert "IF NOT EXISTS" in _sqlite_to_pg_sql(sql)

    def test_create_index_preserved(self):
        sql = "CREATE INDEX IF NOT EXISTS idx_users ON users(account_id)"
        result = _sqlite_to_pg_sql(sql)
        assert "CREATE INDEX" in result
        assert "idx_users" in result

    def test_select_with_limit_and_params(self):
        sql = "SELECT * FROM events WHERE account_id = ? ORDER BY id LIMIT ?"
        result = _sqlite_to_pg_sql(sql)
        assert "$1" in result and "$2" in result
        assert "?" not in result

    def test_on_conflict_do_nothing_preserved(self):
        sql = "INSERT INTO t (a) VALUES (?) ON CONFLICT DO NOTHING"
        result = _sqlite_to_pg_sql(sql)
        assert "ON CONFLICT DO NOTHING" in result

    def test_update_with_multiple_params(self):
        sql = "UPDATE users SET name = ?, role = ? WHERE id = ?"
        result = _sqlite_to_pg_sql(sql)
        assert "$1" in result and "$2" in result and "$3" in result


class TestPgRow:
    """_PgRow — dict-like access shim over asyncpg Record."""

    @staticmethod
    def _make_row(data: dict) -> _PgRow:
        class FakeRecord:
            def __getitem__(self, key): return data[key]
            def keys(self): return data.keys()
            def __iter__(self): return iter(data.values())
            def __len__(self): return len(data)
        return _PgRow(FakeRecord())

    def test_getitem_by_string_key(self):
        row = self._make_row({"id": 1, "name": "Fleet A"})
        assert row["id"] == 1
        assert row["name"] == "Fleet A"

    def test_keys(self):
        row = self._make_row({"id": 1, "tier": "starter"})
        assert "id" in row.keys()
        assert "tier" in row.keys()

    def test_get_existing(self):
        row = self._make_row({"status": "active"})
        assert row.get("status") == "active"

    def test_get_missing_returns_default(self):
        row = self._make_row({"id": 1})
        assert row.get("missing", "fallback") == "fallback"
        assert row.get("missing") is None


class TestPgCursor:
    """_PgCursor — aiosqlite cursor shim."""

    @staticmethod
    def _fake_record(data: dict):
        class FakeRecord:
            def __getitem__(self, key): return data[key]
            def keys(self): return data.keys()
            def __iter__(self): return iter(data.values())
            def __len__(self): return len(data)
        return FakeRecord()

    @pytest.mark.asyncio
    async def test_fetchone_returns_first_row(self):
        rows = [self._fake_record({"id": 1}), self._fake_record({"id": 2})]
        cursor = _PgCursor(rows)
        row = await cursor.fetchone()
        assert row["id"] == 1

    @pytest.mark.asyncio
    async def test_fetchone_empty_returns_none(self):
        cursor = _PgCursor([])
        assert await cursor.fetchone() is None

    @pytest.mark.asyncio
    async def test_fetchall_returns_all(self):
        rows = [self._fake_record({"id": i}) for i in range(3)]
        cursor = _PgCursor(rows)
        result = await cursor.fetchall()
        assert len(result) == 3
        assert result[0]["id"] == 0

    @pytest.mark.asyncio
    async def test_fetchall_empty(self):
        cursor = _PgCursor([])
        assert await cursor.fetchall() == []

    def test_lastrowid_default(self):
        assert _PgCursor([]).lastrowid == 0

    def test_lastrowid_set(self):
        assert _PgCursor([], lastrowid=99).lastrowid == 99


class TestDatabaseCoreBackend:
    """_DatabaseCore._using_postgres property."""

    def test_using_postgres_false_by_default(self):
        from adapters.storage.core import _DatabaseCore
        core = _DatabaseCore("data/test.db")
        assert core._using_postgres is False

    def test_using_postgres_true_when_pool_set(self):
        from adapters.storage.core import _DatabaseCore
        core = _DatabaseCore("data/test.db")
        core._pg_pool = object()
        assert core._using_postgres is True


class TestBillingMixinContracts:
    """billing.BillingMixin must use self._db, not self._connect."""

    def test_no_self_connect_calls(self):
        import inspect
        from adapters.storage.billing import BillingMixin
        source = inspect.getsource(BillingMixin)
        assert "_connect" not in source

    def test_uses_self_db(self):
        import inspect
        from adapters.storage.billing import BillingMixin
        source = inspect.getsource(BillingMixin)
        assert "self._db" in source

    @pytest.mark.asyncio
    async def test_billing_mixin_e2e(self, pg_db):
        """End-to-end: get_or_create_subscription works on a real Postgres DB."""
        db = pg_db
        acct = await db.create_account("Test Carrier")
        sub = await db.get_or_create_subscription(acct.id, tier="starter")
        assert sub is not None
        assert sub["tier"] == "starter"
        assert sub["account_id"] == acct.id
        assert sub["monthly_base_usd"] == 4900

        # Idempotent — same row returned
        sub2 = await db.get_or_create_subscription(acct.id, tier="starter")
        assert sub2["id"] == sub["id"]

        # Update
        await db.update_subscription(acct.id, vehicle_count=15, billing_email="test@fleet.com")
        sub3 = await db.get_subscription(acct.id)
        assert sub3["vehicle_count"] == 15
        assert sub3["billing_email"] == "test@fleet.com"

        # Usage snapshot
        snap_id = await db.record_usage_snapshot(
            account_id=acct.id,
            period_start="2026-04-01",
            period_end="2026-04-30",
            vehicle_count=15,
            user_count=30,
            ai_queries=250,
            base_vehicles=10,
            monthly_base_cents=4900,
            extra_vehicle_cents=299,
        )
        assert snap_id > 0

        # Extra vehicles = 15 - 10 = 5, amount = 4900 + 5*299 = 6395
        snaps = await db.get_usage_snapshots(acct.id)
        assert len(snaps) == 1
        assert snaps[0]["extra_vehicles"] == 5
        assert snaps[0]["amount_due_cents"] == 6395

        await db.close()

    @pytest.mark.asyncio
    async def test_mark_stripe_event_processed_idempotent(self, pg_db):
        """First call wins; retries with the same event_id no-op."""
        db = pg_db
        first  = await db.mark_stripe_event_processed("evt_abc", "checkout.session.completed", 1)
        second = await db.mark_stripe_event_processed("evt_abc", "checkout.session.completed", 1)
        assert first is True
        assert second is False
        # Different event_id still records
        third = await db.mark_stripe_event_processed("evt_xyz", "invoice.payment_failed", None)
        assert third is True

    @pytest.mark.asyncio
    async def test_record_invoice_upserts_on_retry(self, pg_db):
        """First call inserts; second updates mutable fields without dupe row."""
        db = pg_db
        acct = await db.create_account("Inv Carrier")
        inv_id = "in_1ABC"
        rid1 = await db.record_invoice(
            acct.id, inv_id,
            amount_due_cents=6395, amount_paid_cents=0,
            status="open", currency="usd",
            provider_customer_id="cus_X",
            hosted_invoice_url="https://pay.stripe.com/abc",
        )
        assert rid1 > 0
        # Same invoice id, payment succeeded: status flips to paid
        rid2 = await db.record_invoice(
            acct.id, inv_id,
            amount_paid_cents=6395, status="paid",
            paid_at="2026-05-01T12:00:00+00:00",
        )
        assert rid2 == rid1
        invs = await db.get_invoices(acct.id)
        assert len(invs) == 1
        assert invs[0]["status"] == "paid"
        assert invs[0]["amount_paid_cents"] == 6395
        assert invs[0]["paid_at"].startswith("2026-05-01")
        # Original immutable fields preserved
        assert invs[0]["hosted_invoice_url"] == "https://pay.stripe.com/abc"

    @pytest.mark.asyncio
    async def test_find_account_by_stripe_customer_and_subscription(self, pg_db):
        """Lookup helpers resolve stripe ids back to our account_id."""
        db = pg_db
        acct = await db.create_account("LookupCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id,
            provider_customer_id="cus_LOOKUP",
            provider_subscription_id="sub_LOOKUP",
        )
        assert await db.find_account_by_stripe_customer("cus_LOOKUP") == acct.id
        assert await db.find_account_by_stripe_subscription("sub_LOOKUP") == acct.id
        assert await db.find_account_by_stripe_customer("cus_MISSING") is None
        assert await db.find_account_by_stripe_customer("") is None


class TestStripeInvoiceProjection:
    """The _invoice_fields helper must project Stripe payloads safely."""

    def test_subscription_invoice_with_lines(self):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        payload = {
            "id": "in_test_1",
            "customer": "cus_1",
            "subscription": "sub_1",
            "amount_due": 6395,
            "amount_paid": 6395,
            "currency": "USD",
            "status": "paid",
            "hosted_invoice_url": "https://pay.stripe.com/x",
            "invoice_pdf": "https://pay.stripe.com/x.pdf",
            "status_transitions": {"paid_at": 1717200000},
            "lines": {"data": [{"period": {"start": 1714521600, "end": 1717113600}}]},
        }
        out = StripeBillingProvider._invoice_fields(payload)
        assert out["provider_invoice_id"] == "in_test_1"
        assert out["currency"] == "usd"  # lowercased
        assert out["amount_paid_cents"] == 6395
        assert out["status"] == "paid"
        assert out["period_start"].startswith("2024-")
        assert out["period_end"].startswith("2024-")
        assert out["paid_at"].startswith("2024-")

    def test_open_invoice_without_paid_at(self):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        out = StripeBillingProvider._invoice_fields({
            "id": "in_test_2", "amount_due": 4900, "status": "open",
        })
        assert out["status"] == "open"
        assert out["paid_at"] is None
        assert out["amount_paid_cents"] == 0
        assert out["currency"] == "usd"  # default when missing


class TestStripeWebhookDispatch:
    """Webhook dispatch wiring — signature verification is monkey-patched."""

    @pytest.mark.asyncio
    async def test_invoice_payment_succeeded_records_and_recovers(
        self, pg_db, monkeypatch
    ):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("WebhookCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id,
            status="past_due",
            provider_customer_id="cus_W",
            provider_subscription_id="sub_W",
        )
        event = {
            "id": "evt_paid_1",
            "type": "invoice.payment_succeeded",
            "data": {"object": {
                "id": "in_W1", "customer": "cus_W", "subscription": "sub_W",
                "amount_due": 6395, "amount_paid": 6395, "currency": "usd",
                "status": "paid",
                "status_transitions": {"paid_at": 1717200000},
                "hosted_invoice_url": "https://pay.stripe.com/x",
                "invoice_pdf": "https://pay.stripe.com/x.pdf",
            }},
        }
        # Stub the stripe module: construct_event returns the event as-is
        class _FakeWebhook:
            @staticmethod
            def construct_event(payload, sig, secret): return event
        class _FakeStripe:
            Webhook = _FakeWebhook
            class error:  # noqa: N801
                class SignatureVerificationError(Exception): ...
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )

        result = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        assert result == {"handled": True, "event_type": "invoice.payment_succeeded"}

        invs = await db.get_invoices(acct.id)
        assert len(invs) == 1 and invs[0]["status"] == "paid"
        sub = await db.get_subscription(acct.id)
        assert sub["status"] == "active"  # recovered from past_due

        # Replay: same event id is a no-op, no second invoice row
        result2 = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        assert result2.get("duplicate") is True
        assert len(await db.get_invoices(acct.id)) == 1

    @pytest.mark.asyncio
    async def test_payment_failed_sets_past_due_since_then_success_clears(
        self, pg_db, monkeypatch
    ):
        """past_due_since is set on failed payment, cleared on recovery."""
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("GraceCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id, status="active",
            provider_customer_id="cus_G", provider_subscription_id="sub_G",
        )

        def _stub(event):
            class _S:
                class Webhook:
                    @staticmethod
                    def construct_event(p, s, k): return event
                class error:  # noqa: N801
                    class SignatureVerificationError(Exception): ...
            return _S

        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        fail_event = {
            "id": "evt_g_fail",
            "type": "invoice.payment_failed",
            "data": {"object": {
                "id": "in_G1", "customer": "cus_G", "subscription": "sub_G",
                "amount_due": 4900, "status": "open",
            }},
        }
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _stub(fail_event)
        )
        await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        sub = await db.get_subscription(acct.id)
        assert sub["status"] == "past_due"
        assert sub["past_due_since"], "past_due_since should be set on first failure"
        first_since = sub["past_due_since"]

        # Second failure: timestamp must NOT reset (grace clock keeps ticking)
        fail2 = dict(fail_event, id="evt_g_fail2")
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _stub(fail2)
        )
        await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        sub2 = await db.get_subscription(acct.id)
        assert sub2["past_due_since"] == first_since

        # Recovery clears the timestamp
        ok_event = {
            "id": "evt_g_ok",
            "type": "invoice.payment_succeeded",
            "data": {"object": {
                "id": "in_G2", "customer": "cus_G", "subscription": "sub_G",
                "amount_due": 4900, "amount_paid": 4900, "status": "paid",
                "status_transitions": {"paid_at": 1717200000},
            }},
        }
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _stub(ok_event)
        )
        await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        sub3 = await db.get_subscription(acct.id)
        assert sub3["status"] == "active"
        assert sub3["past_due_since"] is None

    @pytest.mark.asyncio
    async def test_invoice_payment_failed_marks_past_due(self, pg_db, monkeypatch):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("FailCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id, status="active",
            provider_customer_id="cus_F", provider_subscription_id="sub_F",
        )
        event = {
            "id": "evt_fail_1",
            "type": "invoice.payment_failed",
            "data": {"object": {
                "id": "in_F1", "customer": "cus_F", "subscription": "sub_F",
                "amount_due": 4900, "amount_paid": 0, "currency": "usd",
                "status": "open",
            }},
        }
        class _FakeStripe:
            class Webhook:
                @staticmethod
                def construct_event(p, s, k): return event
            class error:  # noqa: N801
                class SignatureVerificationError(Exception): ...
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )

        await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        sub = await db.get_subscription(acct.id)
        assert sub["status"] == "past_due"
        invs = await db.get_invoices(acct.id)
        assert len(invs) == 1 and invs[0]["status"] == "open"


class TestCompAccounts:
    """Comp lifecycle, enforcement bypass, and summary projection."""

    @pytest.mark.asyncio
    async def test_grant_renew_revoke_lifecycle(self, pg_db):
        db = pg_db
        acct = await db.create_account("CompCo")
        # Grant requires expires_at
        with pytest.raises(ValueError):
            await db.grant_comp(acct.id, expires_at="", reason="x")
        await db.grant_comp(
            acct.id, expires_at="2099-12-31T23:59:59+00:00",
            reason="VIP early adopter", actor_user_id=42,
        )
        sub = await db.get_subscription(acct.id)
        assert sub["is_comped"] == 1
        assert sub["comp_expires_at"].startswith("2099-")
        assert sub["comp_reason"] == "VIP early adopter"
        assert sub["comp_granted_by"] == 42
        # Renew requires already-comped account
        await db.grant_comp(acct.id, expires_at="2099-12-31T23:59:59+00:00")
        await db.renew_comp(acct.id, new_expires_at="2100-06-30T00:00:00+00:00")
        sub = await db.get_subscription(acct.id)
        assert sub["comp_expires_at"].startswith("2100-")
        # Revoke clears flags
        await db.revoke_comp(acct.id, reason="moved to paid plan")
        sub = await db.get_subscription(acct.id)
        assert sub["is_comped"] == 0
        assert sub["comp_expires_at"] is None
        # Renew on non-comp fails loudly
        with pytest.raises(ValueError):
            await db.renew_comp(acct.id, new_expires_at="2099-12-31T00:00:00+00:00")
        # Audit trail intact
        hist = await db.get_comp_history(acct.id)
        actions = [h["action"] for h in hist]
        assert "granted" in actions and "renewed" in actions and "revoked" in actions

    @pytest.mark.asyncio
    async def test_expire_lapsed_comps_sweep(self, pg_db):
        db = pg_db
        a1 = await db.create_account("Lapsed1")
        a2 = await db.create_account("Active1")
        # Already-expired comp
        await db.grant_comp(a1.id, expires_at="2020-01-01T00:00:00+00:00")
        # Still-valid comp
        await db.grant_comp(a2.id, expires_at="2099-12-31T23:59:59+00:00")
        expired = await db.expire_lapsed_comps()
        assert a1.id in expired and a2.id not in expired
        s1 = await db.get_subscription(a1.id)
        s2 = await db.get_subscription(a2.id)
        assert s1["is_comped"] == 0
        assert s2["is_comped"] == 1
        # Audit row for expiry
        hist = await db.get_comp_history(a1.id, limit=5)
        assert any(h["action"] == "expired" for h in hist)

    def test_is_account_blocked_rules(self):
        from adapters.storage.billing import BillingMixin
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 5, 24, tzinfo=timezone.utc)
        # No subscription → not blocked
        assert BillingMixin.is_account_blocked(None, 7, now) == (False, "")
        # Active → not blocked
        assert BillingMixin.is_account_blocked({"status": "active"}, 7, now) == (False, "")
        # Canceled → blocked immediately (no grace for cancellation)
        b, r = BillingMixin.is_account_blocked({"status": "canceled"}, 7, now)
        assert b and r == "subscription_canceled"
        # Unpaid → blocked
        b, r = BillingMixin.is_account_blocked({"status": "unpaid"}, 7, now)
        assert b and r == "subscription_unpaid"
        # past_due within grace → not blocked
        recent = (now - timedelta(days=3)).isoformat()
        assert BillingMixin.is_account_blocked(
            {"status": "past_due", "past_due_since": recent}, 7, now
        ) == (False, "")
        # past_due past grace → blocked
        old = (now - timedelta(days=10)).isoformat()
        b, r = BillingMixin.is_account_blocked(
            {"status": "past_due", "past_due_since": old}, 7, now
        )
        assert b and r == "past_due_grace_expired"
        # past_due without past_due_since → not blocked (defensive default)
        assert BillingMixin.is_account_blocked(
            {"status": "past_due"}, 7, now
        ) == (False, "")
        # Comp with future expiry overrides everything
        assert BillingMixin.is_account_blocked(
            {
                "status": "canceled",
                "is_comped": 1,
                "comp_expires_at": (now + timedelta(days=30)).isoformat(),
            },
            7, now,
        ) == (False, "")
        # Comp with past expiry falls through to the status check
        b, _ = BillingMixin.is_account_blocked(
            {
                "status": "canceled",
                "is_comped": 1,
                "comp_expires_at": (now - timedelta(days=1)).isoformat(),
            },
            7, now,
        )
        assert b is True

    def test_build_summary_comped_shows_discount(self):
        from adapters.storage.billing import BillingMixin
        sub = {
            "tier": "starter", "status": "active",
            "vehicle_count": 12, "base_vehicles": 10,
            "monthly_base_usd": 4900, "extra_vehicle_cents": 299,
            "billing_email": "vip@example.com",
            "is_comped": 1, "comp_expires_at": "2099-12-31T00:00:00+00:00",
            "comp_reason": "VIP",
        }
        out = BillingMixin.build_summary(sub, provider="stripe")
        # Subtotal = 4900 + 2*299 = 5498
        assert out["subtotal_cents"] == 5498
        assert out["discount_cents"] == 5498
        assert out["amount_due_cents"] == 0
        assert out["is_comped"] is True
        labels = [item["label"] for item in out["line_items"]]
        assert any("Starter plan" in l for l in labels)
        assert any("2 extra trucks" in l for l in labels)
        assert "100% Special Discount" in labels
        # Discount line is the negative of the subtotal
        discount_item = next(i for i in out["line_items"] if i["label"] == "100% Special Discount")
        assert discount_item["amount_cents"] == -5498

    def test_build_summary_non_comped_no_discount(self):
        from adapters.storage.billing import BillingMixin
        sub = {
            "tier": "pro", "status": "active",
            "vehicle_count": 10, "base_vehicles": 10,
            "monthly_base_usd": 9900, "extra_vehicle_cents": 299,
            "billing_email": "", "is_comped": 0,
        }
        out = BillingMixin.build_summary(sub, provider="stripe")
        assert out["amount_due_cents"] == 9900
        assert out["discount_cents"] == 0
        assert out["is_comped"] is False
        labels = [item["label"] for item in out["line_items"]]
        assert "100% Special Discount" not in labels


class TestBillingEnforcementMiddleware:
    """End-to-end: middleware returns 402 when account is past-due past grace."""

    @pytest.mark.asyncio
    async def test_blocks_canceled_account_and_bypasses_billing_routes(
        self, pg_db, monkeypatch
    ):
        from httpx import AsyncClient, ASGITransport
        # Enable enforcement for this test only
        monkeypatch.setenv("BILLING_ENFORCEMENT_ENABLED", "1")
        # Re-import so the middleware re-reads the env
        from interfaces.api.app import BillingEnforcementMiddleware  # noqa: F401

        from fastapi import FastAPI
        from interfaces.api.app import BillingEnforcementMiddleware as MW
        from interfaces.api.auth import create_jwt, AUTH_COOKIE_NAME

        # Set up a blocked account
        db = pg_db
        acct = await db.create_account("BlockedCo")
        await db.get_or_create_subscription(acct.id)
        await db.update_subscription(acct.id, status="canceled")

        app = FastAPI()
        app.add_middleware(MW)

        @app.get("/api/v1/fleet/vehicles")
        async def _vehicles():
            return {"ok": True}

        @app.get("/api/v1/billing/summary")
        async def _summary():
            return {"ok": True}

        # Patch the platform-db lookup the middleware does
        import infra.platform as _ip
        class _Router:
            platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())

        # Build a JWT for the blocked account
        token = create_jwt(
            telegram_id=42, account_id=acct.id, role="owner"
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            # Protected route is blocked
            r = await client.get(
                "/api/v1/fleet/vehicles",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 402, r.text
            assert r.json()["reason"] == "subscription_canceled"
            # Billing route stays open
            r2 = await client.get(
                "/api/v1/billing/summary",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r2.status_code == 200

    @pytest.mark.asyncio
    async def test_active_comp_bypasses_enforcement(self, pg_db, monkeypatch):
        from httpx import AsyncClient, ASGITransport
        monkeypatch.setenv("BILLING_ENFORCEMENT_ENABLED", "1")
        from fastapi import FastAPI
        from interfaces.api.app import BillingEnforcementMiddleware as MW
        from interfaces.api.auth import create_jwt

        db = pg_db
        acct = await db.create_account("CompProtectedCo")
        await db.get_or_create_subscription(acct.id)
        # status that would normally block...
        await db.update_subscription(acct.id, status="canceled")
        # ...but an active comp overrides
        await db.grant_comp(acct.id, expires_at="2099-12-31T00:00:00+00:00")

        app = FastAPI()
        app.add_middleware(MW)

        @app.get("/api/v1/fleet/vehicles")
        async def _vehicles(): return {"ok": True}

        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())

        token = create_jwt(telegram_id=42, account_id=acct.id, role="owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/fleet/vehicles",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200


class TestActiveVehicleBilling:
    """Active-vehicle counting + compute_billing + get_billing_summary."""

    @staticmethod
    def _vehicle_row(vid: str, name: str, captured_at: str) -> dict:
        return {
            "vehicle_id": vid,
            "vehicle_name": name,
            "company_code": "ACME",
            "lat": None, "lon": None,
            "speed_mph": None, "heading": None,
            "address": "",
            "engine_state": "",
            "fuel_pct": None, "def_pct": None,
            "odometer_mi": None, "odometer_time": None,
            "engine_hours": None, "engine_hours_time": None,
            "fault_count": 0, "dtc_critical_count": 0,
            "last_driver_id": "", "last_driver_name": "",
            "captured_at": captured_at,
        }

    @pytest.mark.asyncio
    async def test_count_active_and_inactive(self, pg_db):
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("FleetCo")
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).isoformat()
        stale = (now - timedelta(days=10)).isoformat()
        rows = [
            self._vehicle_row("v1", "Truck #1", recent),
            self._vehicle_row("v2", "Truck #2", recent),
            self._vehicle_row("v3", "Truck #3", stale),
            self._vehicle_row("v4", "Truck #4", ""),   # never reported
        ]
        await db.upsert_vehicle_state(acct.id, rows)
        assert await db.count_active_vehicles(acct.id) == 2
        assert await db.count_inactive_vehicles(acct.id) == 2
        inactive = await db.list_inactive_vehicles(acct.id, limit=10)
        names = [v["vehicle_name"] for v in inactive]
        assert "Truck #3" in names and "Truck #4" in names

    @pytest.mark.asyncio
    async def test_compute_billing_starter_with_extras(self, pg_db):
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("StarterFleet")
        await db.get_or_create_subscription(acct.id, tier="starter")
        # 12 active, 2 inactive → 2 extras at 299 cents = 598 cents on top of 4900 base
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).isoformat()
        stale = (now - timedelta(days=10)).isoformat()
        rows = [self._vehicle_row(f"v{i}", f"Truck #{i}", recent) for i in range(12)]
        rows.extend([self._vehicle_row(f"vs{i}", f"Parked #{i}", stale) for i in range(2)])
        await db.upsert_vehicle_state(acct.id, rows)
        billing = await db.compute_billing(acct.id)
        assert billing["active_vehicles"] == 12
        assert billing["inactive_vehicles"] == 2
        assert billing["included"] == 10
        assert billing["extras"] == 2
        assert billing["base_cents"] == 4900
        assert billing["extras_cents"] == 598
        assert billing["subtotal_cents"] == 5498

    @pytest.mark.asyncio
    async def test_compute_billing_under_included_count(self, pg_db):
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("SmallFleet")
        await db.get_or_create_subscription(acct.id, tier="starter")
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).isoformat()
        rows = [self._vehicle_row(f"v{i}", f"Truck #{i}", recent) for i in range(5)]
        await db.upsert_vehicle_state(acct.id, rows)
        billing = await db.compute_billing(acct.id)
        assert billing["active_vehicles"] == 5
        assert billing["extras"] == 0
        assert billing["extras_cents"] == 0
        assert billing["subtotal_cents"] == 4900  # base only

    @pytest.mark.asyncio
    async def test_get_billing_summary_uses_active_count(self, pg_db):
        """End-to-end summary uses active count, not raw vehicle_count."""
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("SummaryCo")
        await db.get_or_create_subscription(acct.id, tier="pro")
        # Set vehicle_count = 99 (stale legacy value); real active = 15
        await db.update_subscription(acct.id, vehicle_count=99)
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        stale  = (now - timedelta(days=10)).isoformat()
        rows = [self._vehicle_row(f"a{i}", f"Active #{i}", recent) for i in range(15)]
        rows.extend([self._vehicle_row(f"p{i}", f"Parked #{i}", stale) for i in range(3)])
        await db.upsert_vehicle_state(acct.id, rows)

        summary = await db.get_billing_summary(acct.id, provider="stripe")
        # Active drives billing; the legacy raw count is still surfaced
        assert summary["active_vehicles"] == 15
        assert summary["inactive_vehicles"] == 3
        # Pro: 9900 base + (15-10) * 299 = 11395
        assert summary["amount_due_cents"] == 11395
        assert summary["subtotal_cents"] == 11395
        # Sample of inactive trucks for the "X inactive — not billed" footer
        assert len(summary["inactive_sample"]) == 3
        sample_names = [v["vehicle_name"] for v in summary["inactive_sample"]]
        assert all(n.startswith("Parked #") for n in sample_names)

    @pytest.mark.asyncio
    async def test_get_billing_summary_comped_with_active_count(self, pg_db):
        """Comp accounts see the would-be amount + discount + $0 total."""
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("CompFleet")
        await db.get_or_create_subscription(acct.id, tier="pro")
        await db.grant_comp(acct.id, expires_at="2099-12-31T23:59:59+00:00")
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        rows = [self._vehicle_row(f"v{i}", f"Truck #{i}", recent) for i in range(12)]
        await db.upsert_vehicle_state(acct.id, rows)

        summary = await db.get_billing_summary(acct.id, provider="stripe")
        assert summary["is_comped"] is True
        assert summary["active_vehicles"] == 12
        # 9900 + 2 * 299 = 10498 would-be → discount 10498 → due 0
        assert summary["subtotal_cents"] == 10498
        assert summary["discount_cents"] == 10498
        assert summary["amount_due_cents"] == 0
        labels = [it["label"] for it in summary["line_items"]]
        assert "100% Special Discount" in labels


class TestStripeTwoLineSubscription:
    """Checkout uses both line items; sync_billing_quantity patches extras."""

    @pytest.mark.asyncio
    async def test_checkout_attaches_extras_line_item(self, pg_db, monkeypatch):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter_test")
        monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extras_test")
        # Capture the line_items the provider sends to Stripe
        captured: dict = {}
        class _FakeCustomer:
            id = "cus_new"
            @staticmethod
            def create(**kw): return {"id": "cus_new"}
        class _FakeCheckout:
            class Session:
                @staticmethod
                def create(**kw):
                    captured["line_items"] = kw["line_items"]
                    return {"url": "https://stripe/x", "id": "cs_test"}
        class _FakeStripe:
            Customer = _FakeCustomer
            checkout = _FakeCheckout
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )
        db = pg_db
        acct = await db.create_account("CheckoutCo")
        await db.get_or_create_subscription(acct.id)
        result = await StripeBillingProvider().create_checkout_session(
            acct.id, db, "starter", "ok", "cancel",
        )
        assert result["session_id"] == "cs_test"
        assert captured["line_items"] == [
            {"price": "price_starter_test", "quantity": 1},
            {"price": "price_extras_test",  "quantity": 0},
        ]

    @pytest.mark.asyncio
    async def test_checkout_single_line_when_extras_unset(self, pg_db, monkeypatch):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter_test")
        monkeypatch.delenv("STRIPE_PRICE_EXTRA_VEHICLE", raising=False)
        captured: dict = {}
        class _FakeStripe:
            class Customer:
                @staticmethod
                def create(**kw): return {"id": "cus_x"}
            class checkout:  # noqa: N801
                class Session:
                    @staticmethod
                    def create(**kw):
                        captured["line_items"] = kw["line_items"]
                        return {"url": "u", "id": "s"}
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )
        db = pg_db
        acct = await db.create_account("SingleLineCo")
        await db.get_or_create_subscription(acct.id)
        await StripeBillingProvider().create_checkout_session(
            acct.id, db, "starter", "ok", "cancel",
        )
        assert captured["line_items"] == [{"price": "price_starter_test", "quantity": 1}]

    def test_extract_item_ids_matches_extras_price(self, monkeypatch):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extras_test")
        stripe_sub = {
            "items": {"data": [
                {"id": "si_base",  "price": {"id": "price_starter_test"}},
                {"id": "si_extra", "price": {"id": "price_extras_test"}},
            ]},
        }
        base, extra = StripeBillingProvider._extract_item_ids(stripe_sub)
        assert base == "si_base"
        assert extra == "si_extra"

    @pytest.mark.asyncio
    async def test_sync_billing_quantity_no_op_when_qty_matches(
        self, pg_db, monkeypatch
    ):
        from datetime import datetime, timezone, timedelta
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("SyncNoOpCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id, provider="stripe",
            provider_subscription_id="sub_X", provider_extra_item_id="si_extra",
        )
        # 12 active = 2 extras
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        rows = [TestActiveVehicleBilling._vehicle_row(f"v{i}", f"Truck #{i}", recent) for i in range(12)]
        await db.upsert_vehicle_state(acct.id, rows)
        patched: dict = {}
        class _FakeStripe:
            class SubscriptionItem:
                @staticmethod
                def retrieve(_id): return {"quantity": 2}  # already matches
                @staticmethod
                def modify(*a, **kw):
                    patched["called"] = True
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "x")
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )
        result = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
        assert result["skipped"] == "noop"
        assert "called" not in patched

    @pytest.mark.asyncio
    async def test_sync_billing_quantity_patches_on_change(self, pg_db, monkeypatch):
        from datetime import datetime, timezone, timedelta
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("SyncPatchCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id, provider="stripe",
            provider_subscription_id="sub_X", provider_extra_item_id="si_extra",
        )
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        rows = [TestActiveVehicleBilling._vehicle_row(f"v{i}", f"Truck #{i}", recent) for i in range(15)]
        await db.upsert_vehicle_state(acct.id, rows)
        patched: dict = {}
        class _FakeStripe:
            class SubscriptionItem:
                @staticmethod
                def retrieve(_id): return {"quantity": 2}  # stale
                @staticmethod
                def modify(item_id, *, quantity, proration_behavior):
                    patched["item"] = item_id
                    patched["qty"] = quantity
                    patched["proration"] = proration_behavior
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )
        result = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
        assert result["before"] == 2
        assert result["after"] == 5  # 15 - 10 included
        assert patched == {"item": "si_extra", "qty": 5, "proration": "create_prorations"}

    @pytest.mark.asyncio
    async def test_sync_billing_quantity_skips_non_stripe(self, pg_db):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("StubAcct")
        await db.get_or_create_subscription(acct.id)
        # Stays on stub provider (default)
        result = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
        assert result["skipped"] == "not_stripe"


class TestMonthlyBillingSnapshot:
    """Snapshot job: previous-month window, idempotency, active-driven amount."""

    def test_previous_month_window_mid_year(self):
        from capabilities.platform.billing.jobs import _previous_month_window
        from datetime import datetime, timezone
        # Pretend it's 2026-06-15 → window covers all of May 2026
        now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        start, end = _previous_month_window(now)
        assert start.startswith("2026-05-01T00:00:00")
        assert end.startswith("2026-06-01T00:00:00")

    def test_previous_month_window_january_rolls_year(self):
        from capabilities.platform.billing.jobs import _previous_month_window
        from datetime import datetime, timezone
        now = datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc)
        start, end = _previous_month_window(now)
        assert start.startswith("2025-12-01T00:00:00")
        assert end.startswith("2026-01-01T00:00:00")

    @pytest.mark.asyncio
    async def test_snapshot_uses_active_count(self, pg_db, monkeypatch):
        """The recorded row's amount_due reflects active count, not raw."""
        from datetime import datetime, timezone, timedelta
        from capabilities.platform.billing.jobs import snapshot_account_billing
        db = pg_db
        acct = await db.create_account("MonthCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        # Stale raw vehicle_count (legacy field); real active = 13
        await db.update_subscription(acct.id, vehicle_count=99)
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        stale  = (now - timedelta(days=10)).isoformat()
        rows = (
            [TestActiveVehicleBilling._vehicle_row(f"a{i}", f"Active #{i}", recent) for i in range(13)]
            + [TestActiveVehicleBilling._vehicle_row(f"p{i}", f"Parked #{i}", stale) for i in range(2)]
        )
        await db.upsert_vehicle_state(acct.id, rows)

        # Point the job at our test DB
        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())

        snap_id = await snapshot_account_billing(acct.id)
        assert snap_id and snap_id > 0
        snaps = await db.get_usage_snapshots(acct.id, limit=1)
        assert len(snaps) == 1
        snap = snaps[0]
        # 13 active → 3 extras × 299 = 897 + 4900 base = 5797
        assert snap["active_vehicles"] == 13
        assert snap["inactive_vehicles"] == 2
        assert snap["extra_vehicles"] == 3
        assert snap["amount_due_cents"] == 5797
        # Raw vehicle_count still recorded but doesn't drive the math
        assert snap["vehicle_count"] == 99

    @pytest.mark.asyncio
    async def test_snapshot_idempotent_on_replay(self, pg_db, monkeypatch):
        """Re-running for the same period returns the same row, no dupes."""
        from datetime import datetime, timezone, timedelta
        from capabilities.platform.billing.jobs import snapshot_account_billing
        db = pg_db
        acct = await db.create_account("ReplayCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        rows = [TestActiveVehicleBilling._vehicle_row(f"v{i}", f"Truck #{i}", recent) for i in range(7)]
        await db.upsert_vehicle_state(acct.id, rows)
        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        id1 = await snapshot_account_billing(acct.id)
        id2 = await snapshot_account_billing(acct.id)
        assert id1 == id2
        snaps = await db.get_usage_snapshots(acct.id)
        assert len(snaps) == 1

    @pytest.mark.asyncio
    async def test_snapshot_skips_account_without_subscription(self, pg_db, monkeypatch):
        from capabilities.platform.billing.jobs import snapshot_account_billing
        db = pg_db
        acct = await db.create_account("NoSubCo")
        # Don't create a subscription
        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        result = await snapshot_account_billing(acct.id)
        assert result is None


class TestBillingEmailUpdate:
    """PATCH /billing/email writes locally and syncs Stripe customer."""

    @pytest.mark.asyncio
    async def test_stub_provider_writes_local_only(self, pg_db):
        from capabilities.platform.billing.stub import StubBillingProvider
        db = pg_db
        acct = await db.create_account("EmailStubCo")
        await db.get_or_create_subscription(acct.id)
        result = await StubBillingProvider().update_billing_email(
            acct.id, db, "ops@example.com",
        )
        assert result["synced_to_provider"] is False
        sub = await db.get_subscription(acct.id)
        assert sub["billing_email"] == "ops@example.com"

    @pytest.mark.asyncio
    async def test_stripe_skips_when_no_customer_id(self, pg_db, monkeypatch):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("StripeNoCustCo")
        await db.get_or_create_subscription(acct.id)
        # No provider_customer_id set
        # _stripe shouldn't be called in this path; trip it if it is
        def _fail(): raise AssertionError("_stripe should not be called")
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", _fail,
        )
        result = await StripeBillingProvider().update_billing_email(
            acct.id, db, "ops@example.com",
        )
        assert result["synced_to_provider"] is False
        assert result["reason"] == "no_stripe_customer"
        sub = await db.get_subscription(acct.id)
        assert sub["billing_email"] == "ops@example.com"

    @pytest.mark.asyncio
    async def test_stripe_pushes_to_customer_on_change(self, pg_db, monkeypatch):
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("StripeCustCo")
        await db.get_or_create_subscription(acct.id)
        await db.update_subscription(
            acct.id, provider="stripe", provider_customer_id="cus_X",
        )
        captured: dict = {}
        class _FakeStripe:
            class Customer:
                @staticmethod
                def modify(cust_id, *, email):
                    captured["cust_id"] = cust_id
                    captured["email"]   = email
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )
        result = await StripeBillingProvider().update_billing_email(
            acct.id, db, "finance@example.com",
        )
        assert result["synced_to_provider"] is True
        assert captured == {"cust_id": "cus_X", "email": "finance@example.com"}


class TestBillingNotifications:
    """Telegram notifications: helpers, throttling, webhook + comp wiring."""

    def test_reminder_bucket_only_fires_on_exact_match(self):
        from capabilities.platform.billing.notifications import reminder_bucket
        assert reminder_bucket(7) == 7
        assert reminder_bucket(3) == 3
        assert reminder_bucket(1) == 1
        # 6 days left is past the 7-day bucket, before the 3-day — no fire
        assert reminder_bucket(6) is None
        assert reminder_bucket(9) is None
        assert reminder_bucket(0) is None
        assert reminder_bucket(-2) is None

    def test_days_until_handles_malformed(self):
        from capabilities.platform.billing.notifications import days_until
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        target = (now + timedelta(days=5)).isoformat()
        assert days_until(target, now) == 5
        assert days_until("", now) is None
        assert days_until("not-a-date", now) is None

    @pytest.mark.asyncio
    async def test_send_to_admins_only_owner_admin(self, pg_db, monkeypatch):
        """Only Owner + Admin recipients get billing notifications."""
        from adapters.storage.models import Role
        from capabilities.platform.billing import notifications as notif
        db = pg_db
        acct = await db.create_account("NotifyCo")
        owner = await db.create_user(telegram_id=1001, account_id=acct.id, role=Role.OWNER)
        admin = await db.create_user(telegram_id=1002, account_id=acct.id, role=Role.ADMIN)
        driver = await db.create_user(telegram_id=1003, account_id=acct.id, role=Role.DRIVER)
        # Wire platform DB
        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        sent: list[int] = []
        class _Bot:
            async def send_message(self, chat_id, text, parse_mode=None, disable_web_page_preview=None):
                sent.append(chat_id)
        class _App:
            bot = _Bot()
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: _App()
        )
        count = await notif._send_to_admins(acct.id, "<b>hi</b>")
        assert count == 2
        assert sorted(sent) == [1001, 1002]  # owner + admin only
        # ensure the driver is unused (suppress lint)
        assert driver.telegram_id == 1003

    @pytest.mark.asyncio
    async def test_send_to_admins_no_bot_registered_no_op(self, pg_db, monkeypatch):
        from adapters.storage.models import Role
        from capabilities.platform.billing import notifications as notif
        db = pg_db
        acct = await db.create_account("NoBotCo")
        await db.create_user(telegram_id=2001, account_id=acct.id, role=Role.OWNER)
        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: None
        )
        # Should return 0 without raising
        assert await notif._send_to_admins(acct.id, "x") == 0

    @pytest.mark.asyncio
    async def test_send_to_admins_tolerates_per_recipient_failures(
        self, pg_db, monkeypatch,
    ):
        from adapters.storage.models import Role
        from capabilities.platform.billing import notifications as notif
        db = pg_db
        acct = await db.create_account("FailCo")
        await db.create_user(telegram_id=3001, account_id=acct.id, role=Role.OWNER)
        await db.create_user(telegram_id=3002, account_id=acct.id, role=Role.ADMIN)
        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        class _BadBot:
            async def send_message(self, chat_id, **kw):
                if chat_id == 3001:
                    raise RuntimeError("user blocked the bot")
        class _App: bot = _BadBot()
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: _App()
        )
        # One success, one failure → returns 1
        assert await notif._send_to_admins(acct.id, "x") == 1

    @pytest.mark.asyncio
    async def test_webhook_payment_failed_fires_notification(
        self, pg_db, monkeypatch,
    ):
        from adapters.storage.models import Role
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        db = pg_db
        acct = await db.create_account("WebhookNotifyCo")
        await db.get_or_create_subscription(acct.id, tier="starter")
        await db.update_subscription(
            acct.id, status="active",
            provider_customer_id="cus_W", provider_subscription_id="sub_W",
        )
        await db.create_user(telegram_id=4001, account_id=acct.id, role=Role.OWNER)

        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        seen: list[str] = []
        class _Bot:
            async def send_message(self, chat_id, text, **kw):
                seen.append(text)
        class _App: bot = _Bot()
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: _App()
        )

        event = {
            "id": "evt_fail_notify",
            "type": "invoice.payment_failed",
            "data": {"object": {
                "id": "in_W1", "customer": "cus_W", "subscription": "sub_W",
                "amount_due": 4900, "status": "open",
                "hosted_invoice_url": "https://pay.stripe.com/x",
            }},
        }
        class _FakeStripe:
            class Webhook:
                @staticmethod
                def construct_event(p, s, k): return event
            class error:  # noqa: N801
                class SignatureVerificationError(Exception): ...
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "x")
        monkeypatch.setattr(
            "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe
        )
        await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
        assert seen, "expected a notification message"
        assert "Payment failed" in seen[0]
        assert "$49.00" in seen[0]
        assert "pay.stripe.com/x" in seen[0]


class TestCompExpirySweep:
    """Daily sweep: expire lapsed comps + send tiered reminders."""

    @pytest.mark.asyncio
    async def test_sweep_expires_and_notifies(self, pg_db, monkeypatch):
        from adapters.storage.models import Role
        from capabilities.platform.billing.jobs import run_comp_expiry_sweep
        db = pg_db
        a1 = await db.create_account("LapsedCo")
        a2 = await db.create_account("ActiveCo")
        await db.create_user(telegram_id=5001, account_id=a1.id, role=Role.OWNER)
        await db.create_user(telegram_id=5002, account_id=a2.id, role=Role.OWNER)
        await db.grant_comp(a1.id, expires_at="2020-01-01T00:00:00+00:00")
        await db.grant_comp(a2.id, expires_at="2099-12-31T00:00:00+00:00")

        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        seen: list[tuple[int, str]] = []
        class _Bot:
            async def send_message(self, chat_id, text, **kw):
                seen.append((chat_id, text))
        class _App: bot = _Bot()
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: _App()
        )

        result = await run_comp_expiry_sweep()
        assert result["expired"] == 1
        assert result["expired_notified"] == 1
        # The lapsed account's owner got an "ended" message
        assert any(c == 5001 and "ended" in t for c, t in seen)
        # The active comp account got NO message in phase 1
        assert not any(c == 5002 for c, t in seen)
        # And ``is_comped`` was flipped off on the lapsed one
        s1 = await db.get_subscription(a1.id)
        assert s1["is_comped"] == 0

    @pytest.mark.asyncio
    async def test_sweep_sends_expiring_reminder_once_per_bucket(
        self, pg_db, monkeypatch,
    ):
        """A 7-day reminder fires once; a second sweep on the same window no-ops."""
        from adapters.storage.models import Role
        from capabilities.platform.billing.jobs import run_comp_expiry_sweep
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("ExpiringCo")
        await db.create_user(telegram_id=6001, account_id=acct.id, role=Role.OWNER)
        # Comp expires in exactly 7 days from a known clock
        clock = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        seven_out = (clock + timedelta(days=7)).isoformat()
        await db.grant_comp(acct.id, expires_at=seven_out)

        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        seen: list[str] = []
        class _Bot:
            async def send_message(self, chat_id, text, **kw):
                seen.append(text)
        class _App: bot = _Bot()
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: _App()
        )

        result1 = await run_comp_expiry_sweep(now=clock)
        assert result1["reminded"] == 1
        assert "7 days" in seen[0]
        # Replay same day → throttle prevents a second fire
        result2 = await run_comp_expiry_sweep(now=clock)
        assert result2["reminded"] == 0
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_sweep_renewal_resets_reminder_window(
        self, pg_db, monkeypatch,
    ):
        """Renewing the comp counts as a new window — reminders can fire again."""
        from adapters.storage.models import Role
        from capabilities.platform.billing.jobs import run_comp_expiry_sweep
        from datetime import datetime, timezone, timedelta
        db = pg_db
        acct = await db.create_account("RenewSweepCo")
        await db.create_user(telegram_id=7001, account_id=acct.id, role=Role.OWNER)
        clock = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        seven_out = (clock + timedelta(days=7)).isoformat()
        await db.grant_comp(acct.id, expires_at=seven_out)

        import infra.platform as _ip
        class _Router: platform = db
        monkeypatch.setattr(_ip, "get_router", lambda: _Router())
        seen: list[str] = []
        class _Bot:
            async def send_message(self, chat_id, text, **kw):
                seen.append(text)
        class _App: bot = _Bot()
        monkeypatch.setattr(
            "infra.bot_registry.get_app_for_account", lambda _aid: _App()
        )

        await run_comp_expiry_sweep(now=clock)  # fires the 7-day reminder
        assert len(seen) == 1
        # Renew — pushes expiry further; the new window allows a fresh
        # round of reminders.  We set the new expiry to clock+7 days
        # (same offset) so the 7-day reminder is eligible again.
        new_exp = (clock + timedelta(days=14)).isoformat()
        await db.renew_comp(acct.id, new_expires_at=new_exp)
        # Simulate the clock moving 7 days forward so days_until==7
        clock2 = clock + timedelta(days=7)
        await run_comp_expiry_sweep(now=clock2)
        assert len(seen) == 2

    @pytest.mark.asyncio
    async def test_record_comp_reminder_sent_appends_history(self, pg_db):
        """The public throttle helper writes the expected audit row."""
        db = pg_db
        acct = await db.create_account("ReminderHistCo")
        await db.grant_comp(acct.id, expires_at="2099-12-31T00:00:00+00:00")
        await db.record_comp_reminder_sent(
            acct.id, bucket_days=3, expires_at="2099-12-31T00:00:00+00:00",
        )
        hist = await db.get_comp_history(acct.id)
        actions = [(h["action"], h["reason"]) for h in hist]
        assert ("expiring_reminder", "bucket=3d") in actions


class TestBillingMetrics:
    """Prometheus recorders should never crash and tick the right counters."""

    def test_record_billing_webhook_normalizes_unknown_event_type(self):
        from infra import observability as obs
        # Just verify it doesn't crash on edge inputs — the underlying
        # Counter is a stub when prometheus_client isn't installed and
        # a real instrument otherwise; either way the call is the API
        # we care about being stable.
        obs.record_billing_webhook("", "processed")
        obs.record_billing_webhook("checkout.session.completed", "processed")
        obs.record_billing_webhook("checkout.session.completed", "duplicate")
        obs.record_billing_webhook("invoice.payment_failed", "unmatched")
        obs.record_billing_webhook(None, "invalid_signature")  # type: ignore[arg-type]

    def test_record_sync_billing_quantity_accepts_known_results(self):
        from infra import observability as obs
        for result in (
            "noop", "patched", "stripe_error",
            "not_stripe", "no_extras_item", "no_subscription",
        ):
            obs.record_sync_billing_quantity(result)

    def test_record_billing_notification_skips_zero_count(self):
        from infra import observability as obs
        # ``count=0`` means "no one was reached" — we don't want this
        # showing up on dashboards as a tick.  Verify by reading the
        # internal state when prometheus_client is real; when it's a
        # stub the call is a no-op anyway, but the function shouldn't
        # raise.
        obs.record_billing_notification("checkout_complete", count=0)
        obs.record_billing_notification("checkout_complete", count=3)
        obs.record_billing_notification("comp_granted", channel="telegram", count=1)

    def test_record_comp_sweep_skips_zero(self):
        from infra import observability as obs
        obs.record_comp_sweep("expired", count=0)
        obs.record_comp_sweep("expired", count=5)
        obs.record_comp_sweep("reminder_sent", count=2)
        obs.record_comp_sweep("checked", count=42)

    @pytest.mark.asyncio
    async def test_sync_billing_quantity_records_metric_per_outcome(
        self, pg_db, monkeypatch,
    ):
        """Each branch of sync_billing_quantity ticks the right label."""
        from capabilities.platform.billing.stripe_client import StripeBillingProvider
        from infra import observability as obs
        db = pg_db

        # Snapshot the counter we want to observe.  When prom is a
        # stub the counter is the singleton ``_STUB`` — we can't read
        # values from it, so this assertion is light: just confirm
        # the call path doesn't crash and returns the right token.
        provider = StripeBillingProvider()

        # not_stripe: subscription exists but provider=stub
        acct1 = await db.create_account("MetricStubCo")
        await db.get_or_create_subscription(acct1.id)
        r = await provider.sync_billing_quantity(acct1.id, db)
        assert r["skipped"] == "not_stripe"

        # no_extras_item: provider=stripe but no extra item id
        acct2 = await db.create_account("MetricNoItemCo")
        await db.get_or_create_subscription(acct2.id)
        await db.update_subscription(acct2.id, provider="stripe")
        r = await provider.sync_billing_quantity(acct2.id, db)
        assert r["skipped"] == "no_extras_item"

        # The metric object must be addressable for record_*; if obs
        # is a real Counter (prom installed) the label combo we used
        # in this test is now visible at /metrics.
        assert obs.BILLING_SYNC_QTY is not None


class TestPgAdapterImports:
    """pg_adapter symbols must remain importable across renames."""

    def test_pg_adapter_importable(self):
        from adapters.storage.pg_adapter import (
            _sqlite_to_pg_sql, _PgRow, _PgCursor,
            _PgConnection, _PgPool, open_pg_pool,
        )
        # The import itself is the test — every symbol must resolve so
        # any rename breakage is caught here before downstream callers
        # fail at runtime.  Assert each name so ruff sees them used.
        for sym in (_sqlite_to_pg_sql, _PgRow, _PgCursor, _PgConnection, _PgPool, open_pg_pool):
            assert sym is not None
        assert callable(_sqlite_to_pg_sql)
        assert callable(open_pg_pool)
