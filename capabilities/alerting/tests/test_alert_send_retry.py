"""Flood-control retry on alert sends (capabilities/alerting/pipeline).

Telegram's per-group window (~20 msg/min) can reject a send with
``RetryAfter`` during an alert burst even with the client-side rate
limiter — before ``_tg_send_with_retry`` that alert was silently lost.
Pins: one retry after the advertised delay, success on the second
attempt, and a second ``RetryAfter`` propagating (counted as dropped)
so the caller's existing failure handling still runs.
"""

from __future__ import annotations

import pytest
from telegram.error import RetryAfter

from capabilities.notifications.telegram import _tg_send_with_retry


class _FlakySend:
    """Coroutine factory failing with RetryAfter the first *fail_times* calls."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def _send(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RetryAfter(0)  # 0s so the test doesn't sleep for real
        return f"sent-on-attempt-{self.calls}"

    def __call__(self):
        return self._send()


@pytest.mark.asyncio
async def test_retries_once_after_flood_and_delivers():
    send = _FlakySend(fail_times=1)
    result = await _tg_send_with_retry(send, what="test")
    assert result == "sent-on-attempt-2"
    assert send.calls == 2


@pytest.mark.asyncio
async def test_success_path_sends_exactly_once():
    send = _FlakySend(fail_times=0)
    assert await _tg_send_with_retry(send, what="test") == "sent-on-attempt-1"
    assert send.calls == 1


@pytest.mark.asyncio
async def test_second_flood_propagates_as_dropped(monkeypatch):
    outcomes: list[str] = []
    monkeypatch.setattr(
        "infra.observability.record_alert_flood", outcomes.append,
    )
    send = _FlakySend(fail_times=2)
    with pytest.raises(RetryAfter):
        await _tg_send_with_retry(send, what="test")
    assert send.calls == 2          # exactly one retry, never a loop
    assert outcomes == ["retried", "dropped"]
