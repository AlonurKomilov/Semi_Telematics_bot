"""Account-bot health checks — verify delivery WORKS, not just that a
token was saved.

The connect flow validates a token once (``getMe``) and never looks
again.  Everything after that fails silently: a webhook planted on the
token quietly swallows polling, the bot gets kicked from the alerts
group, a forum topic is deleted and every alert routed to it dies with
"message thread not found" in a log nobody reads.  The 2026-08 bot
rotation surfaced exactly this class: state that LOOKS configured and
delivers nothing.

This module probes the actual delivery surface with the real tokens:

  token       getMe — revoked/transferred token is the loudest failure.
  webhook     getWebhookInfo — we poll; ANY webhook URL means another
              consumer (stale host or hijack) is eating the updates.
  avatar      getUserProfilePhotos on the bot itself — cosmetic, but a
              blank-avatar bot looks untrustworthy to drivers.  The Bot
              API cannot SET a profile photo, so this is detect+guide.
  menu        getChatMenuButton — the mini-app button the registry
              asserts on start; drift means the registry never ran.
  branding    getMyShortDescription — empty means the profile page
              shows a bare bot; the registry seeds it when empty.
  group:*     per bound destination (forum group or persona groups):
              is the bot IN the chat, is it an admin, does the chat
              still exist, was it renamed since binding.
  topic:*     per single-group alert route with a thread id: a
              ``sendChatAction`` probe into the thread — the only way
              to learn a topic was deleted without posting a message.
              (The probe shows a ~2s "typing…" flicker; acceptable for
              a health check, and it proves end-to-end postability.)
  subbot:*    per attached persona sender bot: getMe + membership in
              its persona's group.

Checks return machine facts (stable ``id``, ``status``, params); the
dashboard maps ids to localized labels/fix instructions.  ``status``
is ``ok`` / ``warn`` (works, but worth a human glance) / ``fail``
(delivery is broken on this surface).

Reports are stored in Redis (7-day TTL) so the dashboard can show the
last known state instantly and the daily job can diff against it.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from infra import cache as _redis

logger = logging.getLogger(__name__)

_REPORT_TTL = 7 * 86400

# Telegram probe budget.  Health must never hang a request handler: a
# whole run is bounded by (#calls x timeout); a 10-destination account
# stays under ~30s worst-case and ~2s typical.
_HTTP_TIMEOUT = 8.0

ApiFn = Callable[..., Awaitable[dict]]


def _report_key(account_id: int) -> str:
    return f"bot_health:{account_id}"


def _mk_api(token: str) -> ApiFn:
    """Raw Bot API caller bound to one token.

    aiohttp is imported lazily (same pattern as the connect endpoint)
    so unit tests can inject a fake ``api`` without the dependency.
    """
    import aiohttp

    async def api(method: str, **params: Any) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{token}/{method}",
                    json=params,
                    timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT),
                ) as resp:
                    return await resp.json()
        except Exception as e:  # network failure == Telegram unreachable
            return {"ok": False, "description": f"network: {e}"}

    return api


def _check(check_id: str, status: str, **facts: Any) -> dict:
    return {"id": check_id, "status": status, **facts}


async def _probe_bot_surface(
    api: ApiFn, *, expect_username: str = "",
) -> tuple[list[dict], dict]:
    """The per-bot checks that need no chat bindings.  Returns
    (checks, getMe result-or-empty) — bindings probing needs the id."""
    checks: list[dict] = []
    me_resp = await api("getMe")
    me = me_resp.get("result") or {}
    if not me_resp.get("ok"):
        checks.append(_check(
            "token", "fail", error=str(me_resp.get("description") or ""),
        ))
        return checks, {}
    username = str(me.get("username") or "")
    if expect_username and username.lower() != expect_username.lower():
        # The stored username no longer matches the token — the token
        # was swapped behind the stored row.  Everything that renders
        # @username (login widget, "Open in Telegram") points at the
        # wrong bot.
        checks.append(_check(
            "token", "warn", username=username, expected=expect_username,
        ))
    else:
        checks.append(_check("token", "ok", username=username))

    wh = (await api("getWebhookInfo")).get("result") or {}
    wh_url = str(wh.get("url") or "")
    if wh_url:
        checks.append(_check(
            "webhook", "fail", url=wh_url,
            last_error=str(wh.get("last_error_message") or ""),
        ))
    else:
        checks.append(_check("webhook", "ok"))

    photos = (await api(
        "getUserProfilePhotos", user_id=me.get("id"), limit=1,
    )).get("result") or {}
    has_avatar = int(photos.get("total_count") or 0) > 0
    checks.append(_check("avatar", "ok" if has_avatar else "warn"))

    mb = (await api("getChatMenuButton")).get("result") or {}
    menu_ok = mb.get("type") == "web_app"
    checks.append(_check(
        "menu", "ok" if menu_ok else "warn", type=str(mb.get("type") or ""),
    ))

    short = ((await api("getMyShortDescription")).get("result") or {}
             ).get("short_description") or ""
    checks.append(_check("branding", "ok" if short else "warn"))
    return checks, me


async def _probe_destination(
    api: ApiFn,
    bot_id: int,
    *,
    prefix: str,
    chat_id: int,
    stored_title: str = "",
    thread_ids: list[tuple[str, int]] | None = None,
) -> list[dict]:
    """Membership/rights/topic checks for one bound chat.

    ``thread_ids`` is ``[(label, message_thread_id), ...]`` — each gets
    a postability probe.  ``prefix`` namespaces the check ids
    (``group:fleet`` / ``subbot:safety`` / ``group:main``).
    """
    checks: list[dict] = []
    chat_resp = await api("getChat", chat_id=chat_id)
    if not chat_resp.get("ok"):
        # Chat unreachable = bot kicked or group deleted.  One fail for
        # the destination; topic probes would only repeat the story.
        checks.append(_check(
            f"{prefix}:member", "fail", chat_id=chat_id, title=stored_title,
            error=str(chat_resp.get("description") or ""),
        ))
        return checks
    chat = chat_resp.get("result") or {}
    live_title = str(chat.get("title") or "")

    member = (await api(
        "getChatMember", chat_id=chat_id, user_id=bot_id,
    )).get("result") or {}
    m_status = str(member.get("status") or "")
    if m_status in ("left", "kicked", ""):
        checks.append(_check(
            f"{prefix}:member", "fail", chat_id=chat_id, title=live_title,
            member_status=m_status or "unknown",
        ))
        return checks
    if m_status != "administrator":
        # Posting works as a plain member until someone flips the group
        # to admin-only or enables topics; admin is the resilient state.
        checks.append(_check(
            f"{prefix}:member", "warn", chat_id=chat_id, title=live_title,
            member_status=m_status,
        ))
    else:
        checks.append(_check(
            f"{prefix}:member", "ok", chat_id=chat_id, title=live_title,
        ))
    if stored_title and live_title and stored_title != live_title:
        # Not a delivery problem — but the admin UI still shows the old
        # name, and "which group is Safety bound to?" deserves a true
        # answer.  The caller refreshes the stored title on this fact.
        checks.append(_check(
            f"{prefix}:renamed", "warn",
            chat_id=chat_id, stored=stored_title, live=live_title,
        ))

    for label, thread_id in (thread_ids or []):
        act = await api(
            "sendChatAction", chat_id=chat_id, action="typing",
            message_thread_id=thread_id,
        )
        if act.get("ok"):
            checks.append(_check(
                f"{prefix}:topic:{label}", "ok", chat_id=chat_id,
            ))
        else:
            checks.append(_check(
                f"{prefix}:topic:{label}", "fail", chat_id=chat_id,
                error=str(act.get("description") or ""),
            ))
    return checks


async def run_bot_health(
    account_id: int,
    *,
    platform_db: Any,
    include_topic_probes: bool = True,
    api_factory: Callable[[str], ApiFn] | None = None,
) -> dict:
    """Run the full health suite for one account.  Returns the report
    (also persisted to Redis).  Never raises — an account without a
    bot returns a one-check "no bot" report.
    """
    from infra.crypto import decrypt

    factory = api_factory or _mk_api
    checks: list[dict] = []
    account = await platform_db.get_account(account_id)
    if not account or not getattr(account, "bot_token_encrypted", ""):
        report = {
            "account_id": account_id,
            "checked_at": time.time(),
            "summary": "none",
            "checks": [_check("token", "fail", error="no bot configured")],
        }
        await _store(account_id, report)
        return report

    try:
        token = decrypt(account.bot_token_encrypted)
    except Exception:
        token = ""
    if not token:
        report = {
            "account_id": account_id,
            "checked_at": time.time(),
            "summary": "fail",
            "checks": [_check("token", "fail", error="stored token unreadable")],
        }
        await _store(account_id, report)
        return report

    api = factory(token)
    bot_checks, me = await _probe_bot_surface(
        api, expect_username=str(getattr(account, "bot_username", "") or ""),
    )
    checks.extend(bot_checks)
    bot_id = int(me.get("id") or 0)

    if bot_id:
        mode = str(getattr(account, "alert_routing_mode", "") or "single_group")
        try:
            if mode == "per_persona_groups":
                for row in await platform_db.list_persona_groups(account_id):
                    if not getattr(row, "is_active", True):
                        continue
                    checks.extend(await _probe_destination(
                        api, bot_id,
                        prefix=f"group:{row.persona}",
                        chat_id=int(row.chat_id),
                        stored_title=str(getattr(row, "chat_title", "") or ""),
                    ))
                for inst in await platform_db.list_bot_instances(account_id):
                    if not getattr(inst, "is_active", True):
                        continue
                    try:
                        sub_token = decrypt(inst.token_encrypted)
                    except Exception:
                        sub_token = ""
                    if not sub_token:
                        checks.append(_check(
                            f"subbot:{inst.persona}", "fail",
                            error="stored token unreadable",
                        ))
                        continue
                    sub_api = factory(sub_token)
                    sub_me_resp = await sub_api("getMe")
                    if not sub_me_resp.get("ok"):
                        checks.append(_check(
                            f"subbot:{inst.persona}", "fail",
                            error=str(sub_me_resp.get("description") or ""),
                        ))
                        continue
                    checks.append(_check(
                        f"subbot:{inst.persona}", "ok",
                        username=str((sub_me_resp.get("result") or {}
                                      ).get("username") or ""),
                    ))
                    # The sub bot posts into its persona's group — its
                    # membership there is what delivery rides on.
                    grp = await platform_db.get_persona_group(
                        account_id, inst.persona,
                    )
                    if grp is not None and getattr(grp, "is_active", True):
                        checks.extend(await _probe_destination(
                            sub_api,
                            int((sub_me_resp.get("result") or {}).get("id") or 0),
                            prefix=f"subbot:{inst.persona}",
                            chat_id=int(grp.chat_id),
                            stored_title=str(getattr(grp, "chat_title", "") or ""),
                        ))
            else:
                fg = await platform_db.get_forum_group(account_id)
                if fg is not None:
                    threads: list[tuple[str, int]] = []
                    if include_topic_probes:
                        for route in await platform_db.list_alert_routes(
                            account_id,
                        ):
                            if (getattr(route, "is_active", True)
                                    and int(getattr(
                                        route, "message_thread_id", 0) or 0)):
                                threads.append((
                                    route.alert_type,
                                    int(route.message_thread_id),
                                ))
                    checks.extend(await _probe_destination(
                        api, bot_id,
                        prefix="group:main",
                        chat_id=int(fg.chat_id),
                        stored_title=str(getattr(fg, "chat_title", "") or ""),
                        thread_ids=threads,
                    ))
        except Exception as e:
            # A storage hiccup must not masquerade as "delivery is
            # fine" — report the blind spot honestly.
            logger.exception("bot_health bindings probe failed acct=%d", account_id)
            checks.append(_check("bindings", "warn", error=str(e)))

    worst = "ok"
    for c in checks:
        if c["status"] == "fail":
            worst = "fail"
            break
        if c["status"] == "warn":
            worst = "warn"
    report = {
        "account_id": account_id,
        "checked_at": time.time(),
        "summary": worst,
        "checks": checks,
    }
    await _store(account_id, report)
    return report


async def _store(account_id: int, report: dict) -> None:
    try:
        if _redis.is_available():
            await _redis.cache_set(
                _report_key(account_id), report, ttl=_REPORT_TTL,
            )
    except Exception as e:
        logger.debug("bot_health store failed acct=%d: %s", account_id, e)


async def get_bot_health_report(account_id: int) -> dict | None:
    """Last stored report, or None when never checked / expired."""
    try:
        if not _redis.is_available():
            return None
        payload = await _redis.get(_report_key(account_id))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


# Human-readable failure lines for the owner notice.  Only ids that can
# appear with status=fail need words; dynamic ids (group:*, topic:*)
# are prefix-matched.
_FAIL_WORDS: list[tuple[str, str]] = [
    ("token",   "bot token no longer works (revoked or replaced)"),
    ("webhook", "a webhook is set on the bot — something else is "
                "consuming its messages"),
    ("subbot",  "a role's sender bot is broken"),
    ("member",  "the bot was removed from a bound group"),
    ("topic",   "an alert topic no longer accepts posts"),
]


def _describe_failure(check: dict) -> str:
    cid = str(check.get("id", ""))
    for needle, words in _FAIL_WORDS:
        if needle in cid:
            title = check.get("title") or check.get("error") or ""
            return f"{words}" + (f" ({title})" if title else "")
    return cid or "delivery check failed"


async def job_bot_health_daily(_app=None) -> None:
    """Daily platform-wide bot-health sweep.

    Re-probes every account that has a bot, refreshes the stored
    report the dashboard card reads, and tells the account's admins
    when delivery BROKE since the last look (new failing checks only —
    a standing, already-reported failure stays on the dashboard card
    without a daily nag).

    Failure notices ride the notifications capability like any other
    targeted alert; if the account's own bot is the broken channel,
    remaining channels (in-app, email, web push) still carry it —
    which is exactly why this can't just DM from the bot itself.
    """
    from infra.platform import get_platform_db

    db = get_platform_db()
    try:
        accounts = await db.get_accounts_with_bot_tokens()
    except Exception:
        logger.exception("bot_health daily: account listing failed")
        return

    swept = 0
    for account in accounts:
        account_id = int(account.id)
        try:
            previous = await get_bot_health_report(account_id) or {}
            prev_failing = {
                str(c.get("id")) for c in previous.get("checks", [])
                if c.get("status") == "fail"
            }
            report = await run_bot_health(account_id, platform_db=db)
            swept += 1
            new_failures = [
                c for c in report.get("checks", [])
                if c.get("status") == "fail"
                and str(c.get("id")) not in prev_failing
            ]
            if not new_failures:
                continue
            lines = "\n".join(
                f"• {_describe_failure(c)}" for c in new_failures[:6]
            )
            from capabilities.notifications import (
                NotificationContent, notify_user,
            )
            admins = await db.get_account_admins(account_id)
            for admin in admins:
                try:
                    await notify_user(
                        db, account_id, admin.id,
                        NotificationContent(
                            title="",
                            body=(
                                "🤖 Telegram bot delivery problem "
                                "detected:\n" + lines +
                                "\nOpen Settings → Telegram Bot to fix."
                            ),
                            category="alert.bot_health",
                            severity="warning",
                        ),
                    )
                except Exception as err:
                    logger.error(
                        "bot_health notice to user %s failed: %s",
                        admin.id, err,
                    )
        except Exception:
            logger.exception("bot_health daily failed acct=%d", account_id)
    if swept:
        logger.info("bot_health daily: swept %d account bot(s)", swept)
