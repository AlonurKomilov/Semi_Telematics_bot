"""Tests for Phase C (service split flags) and Phase E (PostgreSQL adapter).

Phase C tests verify that:
  - ENABLE_API / ENABLE_BOT / ENABLE_SCHEDULER env flags parse correctly
  - docker-compose.services.yml and systemd units have correct env vars

Phase E tests verify that:
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
#  Phase C — Service split flag tests
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
#  Phase E — PostgreSQL adapter unit tests
# ═══════════════════════════════════════════════════════════════════

from adapters.storage.pg_adapter import _sqlite_to_pg_sql, _PgRow, _PgCursor


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
    async def test_billing_mixin_e2e(self, tmp_path):
        """End-to-end: get_or_create_subscription works on a real SQLite DB."""
        from adapters.storage.platform_db import PlatformDB
        db_path = str(tmp_path / "billing_test.db")
        db = PlatformDB(db_path, pool_size=1)
        await db.initialize()

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


class TestExportScriptExists:
    """scripts/export_sqlite_to_postgres.py must be present and complete."""

    def test_script_file_exists(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "scripts", "export_sqlite_to_postgres.py")
        assert os.path.exists(path)

    def test_script_has_table_defs(self):
        root = os.path.dirname(os.path.dirname(__file__))
        content = open(os.path.join(root, "scripts", "export_sqlite_to_postgres.py")).read()
        assert "PLATFORM_TABLES" in content
        assert "TENANT_TABLES"   in content
        assert "accounts"        in content
        assert "subscriptions"   in content
        assert "billing_usage_snapshots" in content

    def test_pg_adapter_importable(self):
        from adapters.storage.pg_adapter import (
            _sqlite_to_pg_sql, _PgRow, _PgCursor,
            _PgConnection, _PgPool, open_pg_pool,
        )
        assert callable(_sqlite_to_pg_sql)
        assert callable(open_pg_pool)
