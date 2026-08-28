"""Lite group posts (maintenance / documents / scorecard / camera digest
/ samsara-sync) ride the SAME delivery plan as the heavy path.

Replaces the pre-plan suite that pinned the deleted in-module send loop.
Pins the surviving guarantees:

  • the lite call builds ONE plan: per-target on-shift mention prefixes
    (CRITICAL persona primaries only), Sub-bot sender hints (aggregate →
    primary), a caller-supplied PTB reply_markup crossing as generic
    ``tg_buttons`` specs, and NO action row (lite posts have no history
    to correlate — the trio isn't ackable by profile)
  • failure policy stays alerting's: legacy target + "topic deleted" →
    route auto-disable; persona target failures → roster stamp
  • returns True iff ≥1 team copy landed (callers' DM-fallback contract)
  • per-destination serialization now lives in the TOPIC CHANNEL: two
    concurrent sends to one (chat, thread) never interleave

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

import asyncio

import pytest

import capabilities.alerting.pipeline as pipeline_mod
import capabilities.notifications.telegram as tg_mod
from capabilities.alerting.pipeline import post_alert_to_topic
from capabilities.alerting.routing_resolver import AlertTarget
from capabilities.notifications.channels import DeliveryResult, Payload, Recipient
from capabilities.notifications.plan import PlanResult
from capabilities.notifications.telegram import TelegramTopicChannel


class _Deliver:
    def __init__(self, results=None):
        self.plans: list = []
        self._results = results

    async def __call__(self, db, account_id, plan):
        self.plans.append(plan)
        res = PlanResult()
        for i, t in enumerate(plan.shared):
            scripted = (self._results or {}).get(t.address)
            res.shared.append((t, scripted or DeliveryResult(
                ok=True, handle={"chat_id": 1, "message_id": i, "kind": "text"})))
        return res


class _Platform:
    def __init__(self):
        self.disabled: list = []
        self.stamps: list = []

    async def set_alert_route_active(self, account_id, rk, active):
        self.disabled.append((rk, active))

    async def record_persona_group_failure(self, account_id, persona, error):
        self.stamps.append((persona, error))


def _tgt(chat=-100, thread=None, persona="", aggregate=False) -> AlertTarget:
    return AlertTarget(chat_id=chat, message_thread_id=thread,
                       is_aggregate=aggregate, persona=persona)


async def _post(monkeypatch, targets, *, deliver=None, platform=None,
                severity="warning", reply_markup=None, alert_type="maintenance"):
    deliver = deliver or _Deliver()
    platform = platform or _Platform()

    async def _resolve(**kw):
        return list(targets)
    monkeypatch.setattr(
        "capabilities.alerting.routing_resolver.resolve_alert_targets",
        _resolve)
    monkeypatch.setattr("capabilities.notifications.deliver", deliver)
    monkeypatch.setattr(pipeline_mod, "get_platform_db", lambda: platform)
    monkeypatch.setattr(pipeline_mod, "_FORUM_ROUTING_ENABLED", True)

    async def _mention(*, account_id, target, severity_is_critical):
        return "<a>@shift</a>" if (severity_is_critical and target.persona
                                   and not target.is_aggregate) else ""
    monkeypatch.setattr(
        pipeline_mod, "_compose_persona_critical_mention", _mention)
    posted = await post_alert_to_topic(
        None, account_id=1, alert_type=alert_type,
        text="<b>Camera Issues</b> body", severity=severity,
        reply_markup=reply_markup)
    return posted, deliver, platform


@pytest.mark.asyncio
async def test_lite_builds_one_plan_with_hints_and_mentions(monkeypatch):
    posted, deliver, _ = await _post(monkeypatch, [
        _tgt(chat=-7, thread=9, persona="safety"),
        _tgt(chat=-8, persona="owner_admin", aggregate=True),
    ], severity="critical")
    assert posted is True
    plan = deliver.plans[0]
    t1, t2 = plan.shared
    assert t1.address == "-7:9" and t1.sender_hint == "safety"
    assert t1.prefix_html == "<a>@shift</a>"
    assert t2.sender_hint == ""                    # aggregate → primary bot
    assert plan.contents[0].actions == []          # lite: never an action row
    assert "<b>" not in plan.contents[0].body      # raw plain text


@pytest.mark.asyncio
async def test_caller_reply_markup_crosses_as_specs(monkeypatch):
    class _Btn:
        def __init__(self):
            self.text = "✓ Done"
            self.callback_data = "maint_done_5"
            self.url = None

    class _Markup:
        inline_keyboard = [[_Btn()]]

    posted, deliver, _ = await _post(
        monkeypatch, [_tgt(chat=-1, persona="fleet")], reply_markup=_Markup())
    specs = deliver.plans[0].contents[0].meta["tg_buttons"]
    assert specs == [[{"text": "✓ Done", "callback_data": "maint_done_5"}]]


@pytest.mark.asyncio
async def test_legacy_topic_deleted_disables_route(monkeypatch):
    deliver = _Deliver(results={"-1:9": DeliveryResult(
        ok=False, error="BadRequest: topic was deleted")})
    posted, _, platform = await _post(
        monkeypatch, [_tgt(chat=-1, thread=9, persona="")], deliver=deliver)
    assert posted is False
    assert platform.disabled == [("maintenance", False)]


@pytest.mark.asyncio
async def test_persona_failure_stamped(monkeypatch):
    deliver = _Deliver(results={"-2": DeliveryResult(
        ok=False, error="Forbidden: bot was kicked")})
    posted, _, platform = await _post(
        monkeypatch, [_tgt(chat=-2, persona="hr")], deliver=deliver)
    assert posted is False
    assert platform.stamps and platform.stamps[0][0] == "hr"


# ── per-destination serialization (now channel-owned) ───────────────

class _SlowBot:
    def __init__(self):
        self.order: list[str] = []

    async def send_message(self, **kw):
        self.order.append(f"start:{kw['text']}")
        await asyncio.sleep(0.01)
        self.order.append(f"end:{kw['text']}")

        class _R:
            message_id = 1
        return _R()


@pytest.mark.asyncio
async def test_topic_channel_serializes_same_destination(monkeypatch):
    bot = _SlowBot()
    app = type("A", (), {"bot": bot})()
    monkeypatch.setattr(tg_mod, "_bot_for",
                        lambda account_id, override=None: override or app)
    ch = TelegramTopicChannel()
    rcpt = Recipient(account_id=1, type="topic", id="x", address="-5:7")
    await asyncio.gather(
        ch.send(rcpt, Payload(text="A")),
        ch.send(rcpt, Payload(text="B")),
    )
    # Serialized: each send completes before the next begins.
    assert bot.order in (
        ["start:A", "end:A", "start:B", "end:B"],
        ["start:B", "end:B", "start:A", "end:A"],
    )
