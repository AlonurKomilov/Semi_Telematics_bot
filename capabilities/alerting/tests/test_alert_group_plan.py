"""Group-topic delivery through the notifications plan.

Pins the flip's contract (capabilities/alerting/docs/alert-dm-migration.md):

  • resolved AlertTargets become plan Targets: address "<chat>[:thread]",
    sender_hint = persona (aggregate → primary bot via ""), stable id
  • per-persona AI toggle picks WITH-AI vs plain content (aggregate and
    legacy single-mode follow the account default)
  • group posts carry NO Acknowledge action at any severity (owner
    decision 2026-07-27 — acking is personal, DM copies only); the
    spine REMINDER edit targets telegram_dm rows exclusively
  • ack ROWS are written from the returned handles (sent_to=0) so
    escalation / resolve threading / shift report keep their state
  • failure policy stays alerting's: legacy target + "topic deleted" →
    route auto-disable; persona target failure → roster stamp
  • returns True iff at least one team copy landed

All fakes — no Telegram, no Postgres.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import capabilities.alerting.pipeline as pipeline_mod
from capabilities.alerting.pipeline import AlertSeverity, _deliver_groups_via_plan
from capabilities.alerting.routing_resolver import AlertTarget
from capabilities.notifications.channels import DeliveryResult
from capabilities.notifications.plan import PlanResult


class _FakeTenant:
    def __init__(self, settings=None):
        self._settings = settings or {}
        self.acks: list[dict] = []
        self.info_acks: list[dict] = []

    async def get_account_setting(self, account_id, key, default=""):
        return self._settings.get(key, default)

    async def create_alert_ack(self, **kw):
        self.acks.append(kw)
        return 1

    async def create_info_alert_ack(self, **kw):
        self.info_acks.append(kw)
        return 1


class _FakePlatform:
    def __init__(self):
        self.disabled: list[tuple] = []
        self.stamps: list[tuple] = []

    async def set_alert_route_active(self, account_id, route_key, active):
        self.disabled.append((route_key, active))

    async def record_persona_group_failure(self, account_id, persona, error):
        self.stamps.append((persona, error))


class _Deliver:
    """Records the plan; returns scripted per-target results."""

    def __init__(self, results=None):
        self.plans: list = []
        self._results = results

    async def __call__(self, db, account_id, plan):
        self.plans.append(plan)
        res = PlanResult()
        for i, t in enumerate(plan.shared):
            scripted = (self._results or {}).get(t.address)
            res.shared.append((t, scripted or DeliveryResult(
                ok=True, handle={"chat_id": int(t.address.split(":")[0]),
                                 "message_id": 100 + i, "kind": "text"})))
        return res


def _tgt(chat=-100, thread=None, persona="", aggregate=False) -> AlertTarget:
    return AlertTarget(chat_id=chat, message_thread_id=thread,
                       is_aggregate=aggregate, persona=persona)


async def _run(monkeypatch, targets, *, deliver=None, tenant=None,
               platform=None, severity=AlertSeverity.CRITICAL,
               needs_ack=True, history_id=42, send_text="<b>with AI</b>",
               send_text_plain="plain", ai_default=True):
    deliver = deliver or _Deliver()
    tenant = tenant or _FakeTenant()
    platform = platform or _FakePlatform()

    async def _resolve(**kw):
        return list(targets)
    monkeypatch.setattr(
        "capabilities.alerting.routing_resolver.resolve_alert_targets",
        _resolve)
    monkeypatch.setattr("capabilities.notifications.deliver", deliver)

    async def _tdb(aid):
        return tenant
    monkeypatch.setattr(pipeline_mod, "get_tenant_db", _tdb)
    monkeypatch.setattr(pipeline_mod, "get_platform_db", lambda: platform)
    monkeypatch.setattr(pipeline_mod, "_FORUM_ROUTING_ENABLED", True)

    async def _mention(*, account_id, target, severity_is_critical):
        return "<a>@shift</a>" if (severity_is_critical and target.persona
                                   and not target.is_aggregate) else ""
    monkeypatch.setattr(
        pipeline_mod, "_compose_persona_critical_mention", _mention)

    ok = await _deliver_groups_via_plan(
        account_id=1, alert_type="fault", severity=severity, co="G1",
        vehicle_id="v1", vehicle_name="Truck 105",
        send_text=send_text, send_text_plain=send_text_plain,
        ai_account_default=ai_default, photo_bytes=None, video_url="",
        history_id=history_id, needs_ack=needs_ack,
        alert_key_detail="k")
    return ok, deliver, tenant, platform


@pytest.mark.asyncio
async def test_targets_become_plan_targets(monkeypatch):
    ok, deliver, _, _ = await _run(monkeypatch, [
        _tgt(chat=-7, thread=55, persona="safety"),
        _tgt(chat=-9, persona="owner_admin", aggregate=True),
    ])
    assert ok is True
    plan = deliver.plans[0]
    t1, t2 = plan.shared
    assert t1.address == "-7:55" and t1.sender_hint == "safety"
    assert t1.prefix_html == "<a>@shift</a>"          # critical persona mention
    assert t2.address == "-9" and t2.sender_hint == ""  # aggregate → primary
    assert t2.prefix_html == ""
    assert plan.correlation_key == "alert:42"
    # Group posts carry NO Acknowledge action (owner decision
    # 2026-07-27): acking is personal and lives on the DM copies; a
    # shared topic button let anyone clear an alert mid-triage.
    assert plan.contents[0].actions == []


@pytest.mark.asyncio
async def test_persona_ai_toggle_picks_plain_content(monkeypatch):
    tenant = _FakeTenant(settings={"persona_ai.hr.faults": "0"})
    ok, deliver, _, _ = await _run(monkeypatch, [
        _tgt(chat=-1, persona="safety"),   # default → with-AI (index 0)
        _tgt(chat=-2, persona="hr"),       # toggled off → plain (index 1)
    ], tenant=tenant)
    plan = deliver.plans[0]
    assert plan.shared[0].content_index == 0
    assert plan.shared[1].content_index == 1
    assert plan.contents[1].body == "plain"


@pytest.mark.asyncio
async def test_info_carries_no_ack_action(monkeypatch):
    ok, deliver, tenant, _ = await _run(
        monkeypatch, [_tgt(chat=-1, persona="fleet")],
        severity=AlertSeverity.INFO, needs_ack=False)
    assert deliver.plans[0].contents[0].actions == []
    assert tenant.info_acks and not tenant.acks   # info rows, no ack rows


@pytest.mark.asyncio
async def test_ack_rows_written_from_handles(monkeypatch):
    ok, _, tenant, _ = await _run(monkeypatch, [
        _tgt(chat=-7, thread=55, persona="safety"),
        _tgt(chat=-9, persona="owner_admin", aggregate=True),
    ])
    assert len(tenant.acks) == 2
    row = tenant.acks[0]
    assert row["sent_to"] == 0 and row["message_id"] == 100
    assert row["alert_key"] == "G1:v1:k"


@pytest.mark.asyncio
async def test_legacy_topic_deleted_disables_route(monkeypatch):
    deliver = _Deliver(results={"-1:9": DeliveryResult(
        ok=False, error="BadRequest: message thread not found (topic deleted)")})
    ok, _, _, platform = await _run(
        monkeypatch, [_tgt(chat=-1, thread=9, persona="")], deliver=deliver)
    assert ok is False
    assert platform.disabled == [("faults", False)]
    assert platform.stamps == []


@pytest.mark.asyncio
async def test_persona_failure_stamped_not_disabled(monkeypatch):
    deliver = _Deliver(results={"-2": DeliveryResult(
        ok=False, error="Forbidden: bot was kicked")})
    ok, _, _, platform = await _run(
        monkeypatch, [_tgt(chat=-2, persona="hr")], deliver=deliver)
    assert ok is False
    assert platform.disabled == []
    assert platform.stamps and platform.stamps[0][0] == "hr"


@pytest.mark.asyncio
async def test_partial_success_returns_true(monkeypatch):
    deliver = _Deliver(results={"-2": DeliveryResult(ok=False, error="x")})
    ok, _, tenant, _ = await _run(monkeypatch, [
        _tgt(chat=-2, persona="hr"),
        _tgt(chat=-3, persona="safety"),
    ], deliver=deliver)
    assert ok is True
    assert len(tenant.acks) == 1                  # only the landed copy


@pytest.mark.asyncio
async def test_spine_reminder_edits_dm_copies_only(monkeypatch):
    """Reminders are personal: the spine edit must target the
    telegram_dm rows and never the group-topic copies (owner decision
    2026-07-27 — a topic edit replaced the alert text for the whole
    team and hung a personal Acknowledge button in a shared group)."""
    import capabilities.notifications as notif
    from capabilities.alerting.escalation import _spine_remind

    calls = {}

    async def fake_update_delivery(tenant, account_id, correlation_key,
                                   content, *, channels=None, clear=False):
        calls["channels"] = channels
        calls["correlation_key"] = correlation_key
        calls["actions"] = content.actions
        calls["clear"] = clear
        return [SimpleNamespace(ok=True)]

    monkeypatch.setattr(notif, "update_delivery", fake_update_delivery)
    ok = await _spine_remind(
        object(), 1, 42, vname="132", atype="fault",
        age_str="10h 57m", attempt_n=2,
    )
    assert ok is True
    assert calls["channels"] == ("telegram_dm",)
    assert calls["correlation_key"] == "alert:42"
    assert calls["clear"] is False
    # the DM reminder KEEPS the personal Acknowledge action
    assert [a["id"] for a in calls["actions"]] == ["work", "ack"]
