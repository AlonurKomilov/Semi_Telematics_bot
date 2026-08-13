"""Tests for the four backfill-recovery mechanisms:

  1. Heartbeat: every ``_publish_status`` stamps ``last_heartbeat``.
  2. Staleness coercion: ``get_backfill_status`` returns ``failed``
     for records stuck at ``running`` with a stale heartbeat.
  3. Reset: ``reset_backfill_status`` clears the Redis record.
  4. Shutdown sweep: ``mark_running_states_failed_on_shutdown``
     flips every ``running`` record to ``failed``.

These collectively recover the stuck-backfill scenario the user hit:
API restart → asyncio task dies → Redis state stays at ``running`` →
TOCTOU guard returns 409 to every re-trigger for 24h.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────


def _make_redis_stub():
    """In-memory stand-in for ``infra.cache`` covering the surface
    used by the backfill recovery code: ``is_available``, ``get``,
    ``cache_set``, ``delete``, ``scan_keys``."""
    store: dict[str, dict] = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl=120):
        store[key] = dict(value) if isinstance(value, dict) else value

    async def fake_delete(key):
        store.pop(key, None)

    async def fake_scan(pattern, batch=500):
        # Trivial glob — only the ``backfill_history:*`` shape is used.
        prefix = pattern.rstrip("*")
        return [k for k in store.keys() if k.startswith(prefix)]

    def fake_available():
        return True

    return store, fake_get, fake_set, fake_delete, fake_scan, fake_available


# ── 1. Heartbeat stamping ────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_status_stamps_last_heartbeat(monkeypatch):
    from capabilities.integrations.shared import history_backfill

    store, fake_get, fake_set, fake_delete, fake_scan, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)

    await history_backfill._publish_status(
        42, "samsara", {"state": "running", "days_done": 4},
    )

    payload = store["backfill_history:42:samsara"]
    assert "last_heartbeat" in payload
    # Roughly now, within a wide margin.
    assert abs(payload["last_heartbeat"] - time.time()) < 5
    # Original keys preserved.
    assert payload["state"] == "running"
    assert payload["days_done"] == 4


@pytest.mark.asyncio
async def test_publish_status_does_not_mutate_caller_payload(monkeypatch):
    """Caller often reuses the payload dict — we must not contaminate
    it with the heartbeat (would leak into asdict(result) in subsequent
    publishes and shadow the dataclass field if added later)."""
    from capabilities.integrations.shared import history_backfill

    _, _, fake_set, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)

    caller_payload = {"state": "running"}
    await history_backfill._publish_status(42, "samsara", caller_payload)

    assert "last_heartbeat" not in caller_payload


# ── 2. Staleness coercion ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_backfill_status_coerces_stale_running_to_failed(monkeypatch):
    """The headline recovery path: API restarted mid-flight,
    Redis still says ``running`` with an old heartbeat, but the
    next status read coerces to ``failed`` so the dashboard clears
    and the TOCTOU guard unblocks re-trigger."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)

    # Seed: ``running`` record with a heartbeat from 10 minutes ago
    # (well past the 5-min stale threshold).
    store["backfill_history:42:samsara"] = {
        "state": "running",
        "days_done": 4,
        "last_heartbeat": time.time() - 600,
    }
    result = await history_backfill.get_backfill_status(42, "samsara")
    assert result is not None
    assert result["state"] == "failed"
    assert "stale heartbeat" in result["reason"]
    # Underlying Redis NOT rewritten on read — coercion is a view.
    assert store["backfill_history:42:samsara"]["state"] == "running"


@pytest.mark.asyncio
async def test_get_backfill_status_passes_fresh_running_through(monkeypatch):
    """A healthy backfill with a recent heartbeat must NOT be
    coerced — that would prematurely fail a legitimately slow run."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)

    store["backfill_history:42:samsara"] = {
        "state": "running",
        "days_done": 4,
        "last_heartbeat": time.time() - 30,  # 30s ago — fresh
    }
    result = await history_backfill.get_backfill_status(42, "samsara")
    assert result is not None
    assert result["state"] == "running"


@pytest.mark.asyncio
async def test_get_backfill_status_treats_missing_heartbeat_as_stale(monkeypatch):
    """A record with NO ``last_heartbeat`` field is by definition pre-
    heartbeat code — written by the build that shipped before this
    fix.  A live writer would have stamped one already, so missing-
    heartbeat means the task is presumed dead.

    This is the HEADLINE recovery: the user's currently-stuck
    "4/28 days" record was written by the old code (no heartbeat),
    and must auto-recover on the next status poll without manual
    Redis surgery."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)

    # No ``last_heartbeat`` field at all — pre-fix shape.
    store["backfill_history:42:samsara"] = {"state": "running", "days_done": 4}
    result = await history_backfill.get_backfill_status(42, "samsara")
    assert result["state"] == "failed"
    assert "stale" in result["reason"]


@pytest.mark.asyncio
async def test_get_backfill_status_passes_completed_through(monkeypatch):
    """Only ``running`` records get the staleness check.  ``completed``
    and ``failed`` shouldn't be re-coerced — they're terminal states."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)

    store["backfill_history:42:samsara"] = {
        "state": "completed",
        "last_heartbeat": time.time() - 99999,  # ancient
    }
    result = await history_backfill.get_backfill_status(42, "samsara")
    assert result["state"] == "completed"


# ── 3. Reset endpoint ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_backfill_status_clears_record(monkeypatch):
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, fake_delete, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "delete", fake_delete)

    store["backfill_history:42:samsara"] = {"state": "running"}
    cleared = await history_backfill.reset_backfill_status(42, "samsara")
    assert cleared is True
    assert "backfill_history:42:samsara" not in store


@pytest.mark.asyncio
async def test_reset_backfill_status_returns_false_when_no_record(monkeypatch):
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, fake_delete, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "delete", fake_delete)

    cleared = await history_backfill.reset_backfill_status(42, "samsara")
    assert cleared is False


@pytest.mark.asyncio
async def test_reset_backfill_status_refuses_to_clear_completed(monkeypatch):
    """A successful completion writes ``state="completed"`` to Redis.
    The dashboard uses that to render "Last backfill done · N rows".
    An accidental Reset click on a completed badge would erase that
    legitimate state — so we refuse and return False."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, fake_delete, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "delete", fake_delete)

    store["backfill_history:42:samsara"] = {
        "state": "completed", "rows_inserted": 17182,
    }
    cleared = await history_backfill.reset_backfill_status(42, "samsara")
    assert cleared is False
    # Record preserved untouched.
    assert store["backfill_history:42:samsara"]["state"] == "completed"


# ── Same-account clobber suppression ─────────────────────────────


@pytest.mark.asyncio
async def test_skipped_does_not_clobber_running_state_for_same_account(monkeypatch):
    """The headline race: task A holds the lock and has published
    ``state="running"``.  Task B for the SAME account (auto-trigger
    or double-click) hits the lock check.  Without the suppress, B
    publishes ``state="skipped"`` to the same Redis key, clobbering
    A's progress and stopping the badge poller (which only refetches
    on running/queued).

    Result: dashboard shows "Skipped" forever even though A is still
    running, because polling stopped and progress is invisible until
    A completes."""
    from capabilities.integrations.shared import history_backfill
    from adapters.telematics.samsara import throttle as _throttle

    store, fake_get, fake_set, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)

    # Seed: task A's "running 5/30" already in Redis.
    store["backfill_history:42:samsara"] = {
        "state": "running",
        "days_done": 5,
        "rows_inserted": 17182,
        "last_heartbeat": time.time(),
    }

    # Simulate the lock being held (task A is inside ``async with``).
    await _throttle.backfill_account_lock.acquire()
    try:
        result = await history_backfill.backfill_vehicle_history(
            42, days=30, provider_id="samsara",
        )
    finally:
        _throttle.backfill_account_lock.release()

    # Task B got the early-exit reason.
    assert result.state == "skipped"
    # CRITICAL: task A's progress is preserved — NOT clobbered.
    assert store["backfill_history:42:samsara"]["state"] == "running"
    assert store["backfill_history:42:samsara"]["days_done"] == 5


@pytest.mark.asyncio
async def test_skipped_suppress_uses_raw_payload_not_coerced(monkeypatch):
    """The suppression must read the RAW Redis payload, not the
    heartbeat-coerced view from ``get_backfill_status``.  Otherwise
    a long-running backfill whose last heartbeat is older than the
    5-min stale window appears as ``state="failed"`` to the
    suppression check, which then publishes 'skipped' and clobbers
    the live record the moment the long task heartbeats again.

    This regression check sets up exactly that condition: a running
    record with a stale heartbeat.  The raw payload still says
    ``state="running"``, so suppression should fire even though
    the coerced view would say ``failed``."""
    from capabilities.integrations.shared import history_backfill
    from adapters.telematics.samsara import throttle as _throttle

    store, fake_get, fake_set, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)

    # Running state, but heartbeat is 10 minutes old — the coerced
    # view would say "failed".  Raw payload still says running.
    store["backfill_history:42:samsara"] = {
        "state": "running",
        "days_done": 5,
        "rows_inserted": 17182,
        "last_heartbeat": time.time() - 600,
    }

    await _throttle.backfill_account_lock.acquire()
    try:
        result = await history_backfill.backfill_vehicle_history(
            42, days=30, provider_id="samsara",
        )
    finally:
        _throttle.backfill_account_lock.release()

    assert result.state == "skipped"
    # CRITICAL: the running record is preserved untouched even though
    # the heartbeat is stale — suppression read the raw payload.
    assert store["backfill_history:42:samsara"]["state"] == "running"
    assert store["backfill_history:42:samsara"]["days_done"] == 5


@pytest.mark.asyncio
async def test_skipped_does_publish_when_no_running_state_for_account(monkeypatch):
    """The cross-account / first-attempt case: no live record exists
    for this account.  The 'skipped' payload SHOULD be published so
    the operator sees a clear "your request was skipped because the
    platform queue is busy" message, rather than a silent no-op."""
    from capabilities.integrations.shared import history_backfill
    from adapters.telematics.samsara import throttle as _throttle

    store, fake_get, fake_set, _, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)

    # No prior state.
    await _throttle.backfill_account_lock.acquire()
    try:
        result = await history_backfill.backfill_vehicle_history(
            99, days=30, provider_id="samsara",
        )
    finally:
        _throttle.backfill_account_lock.release()

    assert result.state == "skipped"
    # Skipped IS published because there was nothing to clobber.
    assert store["backfill_history:99:samsara"]["state"] == "skipped"


@pytest.mark.asyncio
async def test_reset_backfill_status_clears_failed_record(monkeypatch):
    """``failed`` IS clearable — that's the operator-facing recovery
    path after the staleness coercion writes a stuck record to
    ``failed``."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, _, fake_delete, _, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "delete", fake_delete)

    store["backfill_history:42:samsara"] = {"state": "failed", "reason": "x"}
    cleared = await history_backfill.reset_backfill_status(42, "samsara")
    assert cleared is True
    assert "backfill_history:42:samsara" not in store


# ── 4. Shutdown sweep ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_sweep_marks_running_records_failed(monkeypatch):
    """The lifespan hook calls this after cancelling background
    tasks.  Every record still at ``running`` should flip to
    ``failed`` with reason='api restart in flight' so the badge
    clears immediately on next boot."""
    from capabilities.integrations.shared import history_backfill

    store, fake_get, fake_set, _, fake_scan, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)
    monkeypatch.setattr(history_backfill._redis, "scan_keys", fake_scan)

    store["backfill_history:42:samsara"] = {"state": "running", "days_done": 4}
    store["backfill_history:99:samsara"] = {"state": "running", "days_done": 12}
    # A completed one should NOT be re-marked.
    store["backfill_history:7:samsara"] = {"state": "completed"}

    marked = await history_backfill.mark_running_states_failed_on_shutdown()
    assert marked == 2

    assert store["backfill_history:42:samsara"]["state"] == "failed"
    assert store["backfill_history:42:samsara"]["reason"] == "api restart in flight"
    assert store["backfill_history:99:samsara"]["state"] == "failed"
    # Untouched.
    assert store["backfill_history:7:samsara"]["state"] == "completed"


@pytest.mark.asyncio
async def test_shutdown_sweep_handles_no_records_cleanly(monkeypatch):
    from capabilities.integrations.shared import history_backfill

    store, fake_get, fake_set, _, fake_scan, fake_avail = _make_redis_stub()
    monkeypatch.setattr(history_backfill._redis, "is_available", fake_avail)
    monkeypatch.setattr(history_backfill._redis, "get", fake_get)
    monkeypatch.setattr(history_backfill._redis, "cache_set", fake_set)
    monkeypatch.setattr(history_backfill._redis, "scan_keys", fake_scan)

    marked = await history_backfill.mark_running_states_failed_on_shutdown()
    assert marked == 0


# ── HTTP route: reset endpoint ───────────────────────────────────


@pytest.mark.asyncio
async def test_reset_endpoint_returns_cleared_true_on_existing_record(monkeypatch):
    # The endpoint moved to the Samsara router in the per-provider
    # split; it is provider-fixed, so no provider argument.
    from capabilities.integrations.samsara import router as samsara_module

    monkeypatch.setattr(
        samsara_module, "reset_backfill_status",
        AsyncMock(return_value=True),
    )

    async def fake_audit(*a, **kw):
        return None

    monkeypatch.setattr(samsara_module, "audit", fake_audit)

    result = await samsara_module.backfill_history_reset(
        user={"account_id": 42, "id": 1},
    )
    assert result == {"cleared": True}


@pytest.mark.asyncio
async def test_reset_endpoint_returns_cleared_false_when_absent(monkeypatch):
    from capabilities.integrations.samsara import router as samsara_module

    monkeypatch.setattr(
        samsara_module, "reset_backfill_status",
        AsyncMock(return_value=False),
    )

    async def fake_audit(*a, **kw):
        return None

    monkeypatch.setattr(samsara_module, "audit", fake_audit)

    result = await samsara_module.backfill_history_reset(
        user={"account_id": 42, "id": 1},
    )
    assert result == {"cleared": False}
