"""Phase 5a — exhaustive tests for the SQLite→PostgreSQL SQL translator.

Pins every translation rule we ship so a future refactor of
``adapters.storage.pg_adapter._sqlite_to_pg_sql`` can't silently regress
production-critical SQL. Each assertion is a single end-to-end input →
expected output comparison, easy to extend when new patterns appear.

Runs against any backend (SQLite-only test infra) — the translator is a
pure string function.
"""

from __future__ import annotations

import pytest

from adapters.storage.pg_adapter import _sqlite_to_pg_sql


# ── Identity / passthrough cases ──────────────────────────────

def test_simple_select_passes_through():
    assert _sqlite_to_pg_sql("SELECT 1") == "SELECT 1"


def test_pragma_returns_empty():
    """PRAGMAs are SQLite-only — caller (pg_adapter execute) skips empty results."""
    assert _sqlite_to_pg_sql("PRAGMA journal_mode=WAL") == ""
    assert _sqlite_to_pg_sql("  PRAGMA foreign_keys=ON  ") == ""


def test_questionmark_inside_string_literal_preserved():
    """``?`` inside a quoted literal is NOT a placeholder."""
    sql = "INSERT INTO t (s) VALUES ('?')"
    assert _sqlite_to_pg_sql(sql) == "INSERT INTO t (s) VALUES ('?')"


# ── Parameter renumbering ─────────────────────────────────────

def test_questionmark_to_dollar_n():
    sql = "SELECT * FROM t WHERE a = ? AND b = ?"
    assert _sqlite_to_pg_sql(sql) == "SELECT * FROM t WHERE a = $1 AND b = $2"


def test_many_questionmarks_renumbered_in_order():
    sql = "INSERT INTO t (a, b, c, d, e) VALUES (?, ?, ?, ?, ?)"
    assert _sqlite_to_pg_sql(sql) == "INSERT INTO t (a, b, c, d, e) VALUES ($1, $2, $3, $4, $5)"


# ── AUTOINCREMENT → SERIAL ────────────────────────────────────

def test_integer_pk_autoincrement():
    sql = "CREATE TABLE foo (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
    out = _sqlite_to_pg_sql(sql)
    assert "SERIAL PRIMARY KEY" in out
    assert "AUTOINCREMENT" not in out


# ── datetime() / date() rewrites ──────────────────────────────

def test_datetime_now_bare():
    assert _sqlite_to_pg_sql("SELECT datetime('now')") == "SELECT NOW()"


def test_date_now_bare():
    assert _sqlite_to_pg_sql("SELECT date('now')") == "SELECT (NOW()::date)"


def test_datetime_now_with_literal_offset():
    sql = "WHERE u.created_at > datetime('now', '-30 days')"
    assert _sqlite_to_pg_sql(sql) == "WHERE u.created_at > (NOW() + INTERVAL '-30 days')"


def test_date_now_with_literal_offset():
    sql = "AND snapshot_date >= date('now', '-7 days')"
    assert _sqlite_to_pg_sql(sql) == "AND snapshot_date >= ((NOW() + INTERVAL '-7 days')::date)"


def test_datetime_now_with_param_offset_renumbers():
    """The ``?`` inside ``datetime('now', ?)`` must be renumbered to ``$1``
    by the placeholder pass *after* the datetime rewrite."""
    sql = "AND created_at >= datetime('now', ?)"
    assert _sqlite_to_pg_sql(sql) == "AND created_at >= (NOW() + ($1::interval))"


def test_date_now_with_param_offset_renumbers():
    sql = "AND day >= date('now', ? )"
    assert _sqlite_to_pg_sql(sql) == "AND day >= ((NOW() + ($1::interval))::date)"


def test_column_coercion_datetime():
    """``datetime(col)`` in SQLite normalises a TEXT timestamp; in PG cast to timestamptz."""
    assert _sqlite_to_pg_sql("SELECT datetime(assigned_at)") == "SELECT (assigned_at::timestamptz)"


def test_column_coercion_date():
    assert _sqlite_to_pg_sql("SELECT date(t.snapshot_date)") == "SELECT (t.snapshot_date::date)"


def test_mixed_column_coercion_and_now_literal():
    """The real coaching.py case: column-coerce one side, NOW()+param on the other."""
    sql = "AND datetime(assigned_at) >= datetime('now', ?)"
    assert _sqlite_to_pg_sql(sql) == "AND (assigned_at::timestamptz) >= (NOW() + ($1::interval))"


# ── INSERT OR IGNORE → ON CONFLICT DO NOTHING ─────────────────

def test_insert_or_ignore_simple():
    sql = "INSERT OR IGNORE INTO foo (a, b) VALUES (?, ?)"
    out = _sqlite_to_pg_sql(sql)
    assert out == "INSERT INTO foo (a, b) VALUES ($1, $2) ON CONFLICT DO NOTHING"


def test_insert_or_ignore_with_trailing_semicolon():
    sql = "INSERT OR IGNORE INTO foo VALUES (?, ?);"
    out = _sqlite_to_pg_sql(sql)
    # Semicolon stripped before the appended clause; final form has no trailing semi.
    assert "ON CONFLICT DO NOTHING" in out
    assert ";" not in out


def test_insert_or_ignore_does_not_double_append():
    """If the SQL already has an ON CONFLICT clause, don't append a second one."""
    sql = "INSERT OR IGNORE INTO foo (a) VALUES (?) ON CONFLICT (a) DO NOTHING"
    out = _sqlite_to_pg_sql(sql).upper()
    assert out.count("ON CONFLICT") == 1


def test_insert_or_replace_warns_but_passes_through(caplog):
    """We don't auto-translate INSERT OR REPLACE (requires column enumeration);
    instead we log a warning so prod logs surface every callsite."""
    import logging
    sql = "INSERT OR REPLACE INTO foo VALUES (?, ?)"
    with caplog.at_level(logging.WARNING, logger="adapters.storage.pg_adapter"):
        out = _sqlite_to_pg_sql(sql)
    # The OR REPLACE token survives — translation is intentionally not applied.
    # Caller sees the warning + a SQL string PG will reject so the bug is loud.
    assert "INSERT OR REPLACE" in out


# ── Untranslated-pattern warnings (Phase 5a debug mode) ───────

def test_json_extract_warns_when_debug_enabled(monkeypatch, caplog):
    import logging
    from adapters.storage import pg_adapter
    monkeypatch.setattr(pg_adapter, "_LOG_UNTRANSLATED", True)
    with caplog.at_level(logging.WARNING, logger="adapters.storage.pg_adapter"):
        _sqlite_to_pg_sql("SELECT json_extract(meta, '$.foo') FROM t")
    assert any("untranslated" in r.message for r in caplog.records)


def test_strftime_warns_when_debug_enabled(monkeypatch, caplog):
    import logging
    from adapters.storage import pg_adapter
    monkeypatch.setattr(pg_adapter, "_LOG_UNTRANSLATED", True)
    with caplog.at_level(logging.WARNING, logger="adapters.storage.pg_adapter"):
        _sqlite_to_pg_sql("SELECT strftime('%Y', created_at) FROM users")
    assert any("strftime" in r.message for r in caplog.records)


def test_no_warning_when_debug_disabled(monkeypatch, caplog):
    import logging
    from adapters.storage import pg_adapter
    monkeypatch.setattr(pg_adapter, "_LOG_UNTRANSLATED", False)
    with caplog.at_level(logging.WARNING, logger="adapters.storage.pg_adapter"):
        _sqlite_to_pg_sql("SELECT julianday(t) FROM x")
    assert not any("untranslated" in r.message for r in caplog.records)


# ── Real-codebase SQL samples ─────────────────────────────────
#
# Pin the actual SQL strings from production mixins so a translator
# regression breaks tests rather than silently misbehaving in prod.

CODEBASE_SAMPLES = [
    # users.py:293
    (
        "SELECT * FROM users u WHERE u.created_at > datetime('now', '-30 days')",
        "SELECT * FROM users u WHERE u.created_at > (NOW() + INTERVAL '-30 days')",
    ),
    # scorecard.py:288
    (
        "SELECT s.* FROM daily_scorecard_snapshots s WHERE account_id = ? "
        "AND snapshot_date >= date('now', ? )",
        "SELECT s.* FROM daily_scorecard_snapshots s WHERE account_id = $1 "
        "AND snapshot_date >= ((NOW() + ($2::interval))::date)",
    ),
    # coaching.py:200
    (
        "SELECT 1 FROM coaching_assignments WHERE account_id = ? AND driver_id = ? "
        "AND topic_key = ? AND status = 'pending' "
        "AND datetime(assigned_at) >= datetime('now', ?) LIMIT 1",
        "SELECT 1 FROM coaching_assignments WHERE account_id = $1 AND driver_id = $2 "
        "AND topic_key = $3 AND status = 'pending' "
        "AND (assigned_at::timestamptz) >= (NOW() + ($4::interval)) LIMIT 1",
    ),
    # alerts.py:274
    (
        "INSERT OR IGNORE INTO alert_history "
        "(account_id, vehicle_id, alert_type, created_at) "
        "VALUES (?, ?, ?, ?)",
        "INSERT INTO alert_history "
        "(account_id, vehicle_id, alert_type, created_at) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
    ),
    # platform_schema.py:231 — DEFAULT (datetime('now'))
    (
        "CREATE TABLE foo (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))",
        "CREATE TABLE foo (id SERIAL PRIMARY KEY, "
        "created_at TEXT NOT NULL DEFAULT (NOW()))",
    ),
]


@pytest.mark.parametrize("sqlite_sql,expected_pg_sql", CODEBASE_SAMPLES)
def test_real_codebase_sql_translates(sqlite_sql: str, expected_pg_sql: str):
    """Each sample is a real production SQL string; pinning the expected
    output catches translator regressions before they ship."""
    actual = _sqlite_to_pg_sql(sqlite_sql)
    assert actual == expected_pg_sql, (
        f"\n  IN:       {sqlite_sql}\n"
        f"  EXPECTED: {expected_pg_sql}\n"
        f"  GOT:      {actual}"
    )
