"""Bot-health probe engine — the delivery surface, checked for real.

Covers the failure classes the 2026-08 bot rotation surfaced: a
webhook quietly eating a polling bot's updates, the bot kicked from
the bound group, a deleted forum topic, a token swapped behind the
stored username — plus the healthy path and the daily job's
"notify on NEW breakage only" contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import infra.crypto as crypto
from capabilities.notifications import bot_health


def _fake_api_factory(responses: dict, calls: list | None = None):
    """api(method, **params) answering from ``responses`` (method →
    payload, or (method, key) → payload for per-target overrides)."""

    def factory(token: str):
        async def api(method: str, **params):
            if calls is not None:
                calls.append((token, method, params))
            for key in (
                (method, params.get("chat_id"), params.get("message_thread_id")),
                (method, params.get("chat_id")),
                method,
            ):
                if key in responses:
                    return responses[key]
            return {"ok": True, "result": {}}
        return api

    return factory


def _account(**over):
    base = dict(
        id=42, bot_token_encrypted="enc", bot_username="acme_bot",
        alert_routing_mode="single_group",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Db:
    def __init__(self, account, forum_group=None, routes=(),
                 persona_groups=(), bot_instances=()):
        self._account = account
        self._fg = forum_group
        self._routes = list(routes)
        self._pgs = list(persona_groups)
        self._instances = list(bot_instances)

    async def get_account(self, _account_id):
        return self._account

    async def get_forum_group(self, _account_id):
        return self._fg

    async def list_alert_routes(self, _account_id):
        return self._routes

    async def list_persona_groups(self, _account_id):
        return self._pgs

    async def get_persona_group(self, _account_id, persona):
        for g in self._pgs:
            if g.persona == persona:
                return g
        return None

    async def list_bot_instances(self, _account_id):
        return self._instances


_ME_OK = {"ok": True, "result": {"id": 999, "username": "acme_bot"}}
_HEALTHY = {
    "getMe": _ME_OK,
    "getWebhookInfo": {"ok": True, "result": {"url": ""}},
    "getUserProfilePhotos": {"ok": True, "result": {"total_count": 1}},
    "getChatMenuButton": {"ok": True, "result": {"type": "web_app"}},
    "getMyShortDescription": {"ok": True,
                              "result": {"short_description": "x"}},
    "getChat": {"ok": True, "result": {"title": "Ops"}},
    "getChatMember": {"ok": True, "result": {"status": "administrator"}},
    "sendChatAction": {"ok": True, "result": True},
}


def _by_id(report):
    return {c["id"]: c for c in report["checks"]}


@pytest.mark.asyncio
async def test_healthy_single_group_reports_ok(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    fg = SimpleNamespace(chat_id=-100, chat_title="Ops")
    route = SimpleNamespace(alert_type="maintenance", message_thread_id=7,
                            is_active=True)
    db = _Db(_account(), forum_group=fg, routes=[route])
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=_fake_api_factory(dict(_HEALTHY)),
    )
    assert report["summary"] == "ok"
    ids = _by_id(report)
    assert ids["token"]["status"] == "ok"
    assert ids["webhook"]["status"] == "ok"
    assert ids["group:main:member"]["status"] == "ok"
    assert ids["group:main:topic:maintenance"]["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_on_polling_bot_is_a_failure(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    responses["getWebhookInfo"] = {
        "ok": True,
        "result": {"url": "https://evil.example/hook",
                   "last_error_message": ""},
    }
    db = _Db(_account())
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=_fake_api_factory(responses),
    )
    assert report["summary"] == "fail"
    assert _by_id(report)["webhook"]["url"] == "https://evil.example/hook"


@pytest.mark.asyncio
async def test_deleted_topic_fails_its_probe(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    responses[("sendChatAction", -100, 7)] = {
        "ok": False, "description": "Bad Request: message thread not found",
    }
    fg = SimpleNamespace(chat_id=-100, chat_title="Ops")
    route = SimpleNamespace(alert_type="maintenance", message_thread_id=7,
                            is_active=True)
    db = _Db(_account(), forum_group=fg, routes=[route])
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=_fake_api_factory(responses),
    )
    topic = _by_id(report)["group:main:topic:maintenance"]
    assert topic["status"] == "fail"
    assert "thread not found" in topic["error"]


@pytest.mark.asyncio
async def test_kicked_bot_fails_membership_and_skips_topics(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    responses["getChat"] = {
        "ok": False, "description": "Forbidden: bot was kicked",
    }
    fg = SimpleNamespace(chat_id=-100, chat_title="Ops")
    route = SimpleNamespace(alert_type="maintenance", message_thread_id=7,
                            is_active=True)
    db = _Db(_account(), forum_group=fg, routes=[route])
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=_fake_api_factory(responses),
    )
    ids = _by_id(report)
    assert ids["group:main:member"]["status"] == "fail"
    # Topic probes would only repeat the kicked story.
    assert "group:main:topic:maintenance" not in ids


@pytest.mark.asyncio
async def test_swapped_token_warns_on_username_mismatch(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    responses["getMe"] = {
        "ok": True, "result": {"id": 999, "username": "someone_elses_bot"},
    }
    db = _Db(_account(bot_username="acme_bot"))
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=_fake_api_factory(responses),
    )
    token = _by_id(report)["token"]
    assert token["status"] == "warn"
    assert token["expected"] == "acme_bot"


@pytest.mark.asyncio
async def test_group_rename_is_reported(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    responses["getChat"] = {"ok": True, "result": {"title": "PTG Ops 2.0"}}
    fg = SimpleNamespace(chat_id=-100, chat_title="Ops")
    db = _Db(_account(), forum_group=fg)
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=_fake_api_factory(responses),
    )
    renamed = _by_id(report)["group:main:renamed"]
    assert renamed["status"] == "warn"
    assert renamed["live"] == "PTG Ops 2.0"


@pytest.mark.asyncio
async def test_no_bot_reports_none_summary():
    db = _Db(_account(bot_token_encrypted=""))
    report = await bot_health.run_bot_health(42, platform_db=db)
    assert report["summary"] == "none"


@pytest.mark.asyncio
async def test_persona_mode_probes_groups_and_sub_bots(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    pg = SimpleNamespace(persona="safety", chat_id=-200, chat_title="Safety",
                         is_active=True)
    dead_sub = SimpleNamespace(persona="safety", token_encrypted="enc2",
                               is_active=True, bot_username="sub")
    calls: list = []
    factory = _fake_api_factory(responses, calls)

    def picky_factory(token):
        if token == "tok":
            return factory(token)
        async def dead_api(method, **params):
            return {"ok": False, "description": "Unauthorized"}
        return dead_api

    monkeypatch.setattr(crypto, "decrypt",
                        lambda s: "tok" if s == "enc" else "sub_tok")
    db = _Db(_account(alert_routing_mode="per_persona_groups"),
             persona_groups=[pg], bot_instances=[dead_sub])
    report = await bot_health.run_bot_health(
        42, platform_db=db, api_factory=picky_factory,
    )
    ids = _by_id(report)
    assert ids["group:safety:member"]["status"] == "ok"
    assert ids["subbot:safety"]["status"] == "fail"
    assert report["summary"] == "fail"


@pytest.mark.asyncio
async def test_daily_job_notifies_admins_on_new_failure(monkeypatch):
    monkeypatch.setattr(crypto, "decrypt", lambda s: "tok")
    responses = dict(_HEALTHY)
    responses["getMe"] = {"ok": False, "description": "Unauthorized"}

    account = _account()
    db = _Db(account)
    db.get_accounts_with_bot_tokens = None  # replaced below

    async def _accounts():
        return [account]

    async def _admins(_account_id):
        return [SimpleNamespace(id=7)]

    db.get_accounts_with_bot_tokens = _accounts
    db.get_account_admins = _admins
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    sent: list = []

    async def _notify(_db, account_id, user_id, content, **_kw):
        sent.append((account_id, user_id, content.category, content.body))
        return []

    import capabilities.notifications as notif_pkg
    monkeypatch.setattr(notif_pkg, "notify_user", _notify)

    async def _run(account_id, *, platform_db):
        return {
            "account_id": account_id,
            "checked_at": 0,
            "summary": "fail",
            "checks": [{"id": "token", "status": "fail",
                        "error": "Unauthorized"}],
        }

    monkeypatch.setattr(bot_health, "run_bot_health", _run)

    await bot_health.job_bot_health_daily()
    assert len(sent) == 1
    acct_id, user_id, category, body = sent[0]
    assert (acct_id, user_id) == (42, 7)
    assert category == "alert.bot_health"
    assert "token" in body
