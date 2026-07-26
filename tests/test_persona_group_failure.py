"""Persona-group delivery-health stamps.

Per-persona group posting never auto-disables (bot kicked / group
deleted / lost admin need a human), which used to mean it failed
silently forever.  Now the pipeline stamps the failure on the group row
and the next success heals it — the Group delivery roster surfaces the
stamp.

  • a failed post on a persona target records last_error/last_error_at
  • a successful post clears an existing stamp
  • legacy (single-group) targets never touch the stamps — they have
    their own auto-disable drift handling
  • a stamp-write failure never changes the post's outcome

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import pytest

import capabilities.alerting.pipeline as pipeline_mod
from capabilities.alerting.pipeline import AlertSeverity, _post_one_target
from capabilities.alerting.routing_resolver import AlertTarget


class _RecorderDb:
    def __init__(self):
        self.recorded: list[tuple] = []
        self.cleared: list[tuple] = []

    async def record_persona_group_failure(self, account_id, persona, error):
        self.recorded.append((account_id, persona, error))

    async def clear_persona_group_failure(self, account_id, persona):
        self.cleared.append((account_id, persona))

    async def create_info_alert_ack(self, **kw):
        return 0


class _BoomBot:
    async def send_message(self, **kw):
        raise RuntimeError("Forbidden: bot was kicked from the supergroup chat")


class _OkBot:
    async def send_message(self, **kw):
        class _R:
            message_id = 5
        return _R()


class _App:
    def __init__(self, bot):
        self.bot = bot


def _target(persona: str) -> AlertTarget:
    return AlertTarget(chat_id=-1009, message_thread_id=None,
                       is_aggregate=False, persona=persona)


async def _post(app, db, target, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "get_platform_db", lambda: db)

    async def _tenant(_aid):
        return db
    monkeypatch.setattr(pipeline_mod, "get_tenant_db", _tenant)
    return await _post_one_target(
        app, target=target, account_id=7, alert_type="fault",
        severity=AlertSeverity.INFO, co="G1", vehicle_id="v1",
        vehicle_name="Truck 1", send_text="x", video_url="",
        photo_bytes=None, event_id="", event_time="", maps_url=None,
    )


@pytest.mark.asyncio
async def test_persona_failure_is_stamped(monkeypatch):
    db = _RecorderDb()
    ok = await _post(_App(_BoomBot()), db, _target("safety"), monkeypatch)
    assert ok is False
    assert len(db.recorded) == 1
    account_id, persona, error = db.recorded[0]
    assert (account_id, persona) == (7, "safety")
    assert "kicked" in error


@pytest.mark.asyncio
async def test_persona_success_heals_the_stamp(monkeypatch):
    db = _RecorderDb()
    ok = await _post(_App(_OkBot()), db, _target("safety"), monkeypatch)
    assert ok is True
    assert db.cleared == [(7, "safety")]
    assert db.recorded == []


@pytest.mark.asyncio
async def test_legacy_target_never_touches_stamps(monkeypatch):
    db = _RecorderDb()
    ok = await _post(_App(_BoomBot()), db, _target(""), monkeypatch)
    assert ok is False
    assert db.recorded == [] and db.cleared == []


@pytest.mark.asyncio
async def test_stamp_write_failure_never_changes_outcome(monkeypatch):
    class _BrokenDb(_RecorderDb):
        async def record_persona_group_failure(self, *a):
            raise RuntimeError("db down")

    db = _BrokenDb()
    ok = await _post(_App(_BoomBot()), db, _target("safety"), monkeypatch)
    assert ok is False               # outcome unchanged, no raise
