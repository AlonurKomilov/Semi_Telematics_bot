"""Persona-group delivery-health stamps.

Per-persona group posting never auto-disables (bot kicked / group
deleted / lost admin all need a human), which used to mean it failed
silently forever.  The pipeline stamps the failure on the group row and
the next success heals it — the Group delivery roster surfaces the
stamp.

  • a failed post on a persona target records the error
  • a successful post CLEARS an existing stamp
  • legacy (single-group) targets never touch the stamps — they have
    their own auto-disable drift handling
  • a stamp-write failure never changes the post's outcome

This file used to drive `_post_one_target`, which the transport-wall
refactor deleted ("legacy delivery code burned") without updating the
test.  The import error meant the module could not even be COLLECTED —
and because CI runs `pytest -x`, the whole suite stopped here.  Under
that cover the heal half was lost too: `clear_persona_group_failure`
sat defined in the adapter and called from nowhere, so any group that
failed once kept its failure stamp forever.

Both delivery paths now share one policy helper, and this tests that
helper directly — the behaviour is the stamp decision, not the
transport, so reconstructing a delivery plan here would test the spine
instead of the rule.

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import pytest

import capabilities.alerting.pipeline as pipeline_mod
from capabilities.alerting.pipeline import _stamp_persona_delivery


class _RecorderDb:
    def __init__(self):
        self.recorded: list[tuple] = []
        self.cleared: list[tuple] = []

    async def record_persona_group_failure(self, account_id, persona, error):
        self.recorded.append((account_id, persona, error))

    async def clear_persona_group_failure(self, account_id, persona):
        self.cleared.append((account_id, persona))


class _Target:
    """Only the attribute the stamp policy reads."""
    def __init__(self, persona: str):
        self.persona = persona


class _Res:
    def __init__(self, ok: bool, error: str = ""):
        self.ok = ok
        self.error = error


async def _stamp(db, target, res, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "get_platform_db", lambda: db)
    return await _stamp_persona_delivery(7, target, res)


@pytest.mark.asyncio
async def test_persona_failure_is_stamped(monkeypatch):
    db = _RecorderDb()
    await _stamp(db, _Target("safety"),
                 _Res(False, "Forbidden: bot was kicked from the supergroup chat"),
                 monkeypatch)
    assert len(db.recorded) == 1
    account_id, persona, error = db.recorded[0]
    assert (account_id, persona) == (7, "safety")
    assert "kicked" in error
    assert db.cleared == []


@pytest.mark.asyncio
async def test_persona_success_heals_the_stamp(monkeypatch):
    """The half the refactor lost: without this, a group that failed
    once shows a failure on the roster forever."""
    db = _RecorderDb()
    await _stamp(db, _Target("safety"), _Res(True), monkeypatch)
    assert db.cleared == [(7, "safety")]
    assert db.recorded == []


@pytest.mark.asyncio
async def test_failure_without_an_error_string_still_stamps(monkeypatch):
    db = _RecorderDb()
    await _stamp(db, _Target("safety"), _Res(False, ""), monkeypatch)
    assert db.recorded == [(7, "safety", "send failed")]


@pytest.mark.asyncio
async def test_legacy_target_never_touches_stamps(monkeypatch):
    """No persona = a legacy single-group target, which owns its own
    auto-disable drift handling; stamping it would double-report."""
    db = _RecorderDb()
    await _stamp(db, _Target(""), _Res(False, "boom"), monkeypatch)
    await _stamp(db, _Target(""), _Res(True), monkeypatch)
    assert db.recorded == [] and db.cleared == []


@pytest.mark.asyncio
async def test_stamp_write_failure_never_changes_outcome(monkeypatch):
    class _BrokenDb(_RecorderDb):
        async def record_persona_group_failure(self, *a):
            raise RuntimeError("db down")

        async def clear_persona_group_failure(self, *a):
            raise RuntimeError("db down")

    db = _BrokenDb()
    # neither call may raise — the post already happened either way
    await _stamp(db, _Target("safety"), _Res(False, "boom"), monkeypatch)
    await _stamp(db, _Target("safety"), _Res(True), monkeypatch)


@pytest.mark.asyncio
async def test_both_delivery_paths_share_one_policy():
    """The heal was once written at one path only, then lost.  Pin that
    every delivery loop routes its stamp through the same helper."""
    import inspect
    src = inspect.getsource(pipeline_mod)
    assert src.count("await _stamp_persona_delivery(") == 4, (
        "expected both delivery paths to stamp on BOTH success and "
        "failure — a path that stamps only one outcome is how the heal "
        "went missing the first time"
    )
    assert "record_persona_group_failure" not in src.split(
        "async def _stamp_persona_delivery")[1].split("async def post_alert_to_topic")[1], (
        "stamp writes must live only in the policy helper"
    )
