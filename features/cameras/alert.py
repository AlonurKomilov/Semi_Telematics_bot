"""Camera alert checks — AI-powered dashcam analysis."""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from adapters.storage import Role
from infra.bot_registry import get_app_for_account
from infra.isolation import run_account_job, CAMERA_JOB_TIMEOUT
from infra.services import get_platform_db, get_tenant_db
from capabilities.alerting.registry import register_alert_source

logger = logging.getLogger("bot")


def _issues_for_subscriber(sub, all_issues: list[dict]) -> list[dict]:
    """Apply driver-role isolation to camera issues.

    A driver should only ever receive camera alerts for their own
    assigned truck.  This mirrors the pattern in
    ``capabilities/alerting/pipeline.py:286-288`` which the universal
    ``send_alert()`` pipeline uses for fault / health / fuel / parking
    alerts.  Camera alerts previously bypassed that pipeline and
    broadcast every issue to every subscriber — a real privacy
    regression where drivers in company G1 were seeing dashcam reports
    for trucks in CFT / OSY / PTG.

    Non-driver roles (owner, admin, fleet manager, safety) keep the
    account-wide view they had before; account / company scoping for
    those roles is a separate concern handled by their permissions.
    """
    if sub.role == Role.DRIVER and sub.truck_num:
        my_truck = sub.truck_num.strip().lower()
        return [
            r for r in all_issues
            if my_truck in str(r.get("vehicle", "")).strip().lower()
        ]
    return list(all_issues)


def _issues_for_companies(
    all_issues: list[dict], allowed_codes: set[str],
) -> list[dict]:
    """Apply the company wall to camera issues, fail-CLOSED.

    Keyed on ``_org`` — the wire code — never on ``company``, which is a
    display label carrying a ``"?"`` placeholder for unknown.  A filter
    that reads the label is one placeholder away from either leaking a
    row or naming a folder ``?``.

    An issue we cannot attribute to a company does NOT reach a
    restricted subscriber.  The old rule let it through to everyone, so
    a fleet manager scoped to one company saw another company's truck
    whenever the org tag was missing — which, before the storage fix,
    was every snapshot.  Blank company = denied, matching the wall every
    other feature applies.

    Callers pass a NON-EMPTY ``allowed_codes``; an account-wide
    subscriber has no company restriction and must skip this entirely
    rather than pass an empty set (which would deny everything).
    """
    allow = {c.upper() for c in allowed_codes}
    return [r for r in all_issues if (r.get("_org") or "").upper() in allow]


# Dedup: account_id → set of "vehicle:camera_type" that already alerted
_known_camera_issues: dict[int, set[str]] = {}

_camera_warmup_done: set[int] = set()


@register_alert_source("camera_check", trigger="interval", hours=6)
async def check_camera_alerts(app: Application):
    """Periodic camera check — alerts on PROBLEM/WARNING dashcams.

    Runs less frequently (daily or every few hours). Downloads fresh
    dashcam snapshots, runs AI vision analysis, and sends alerts for
    cameras that have problems (obstruction, misalignment, etc.).

    Only sends alerts for *new* issues not already flagged.
    """

    try:
        subscribers = await get_platform_db().get_all_typed_subscribers("camera")
        # Effective per-account permission gate (§9d) — same rule as the
        # typed-subscriber fetch; the cross-account list is fine, the
        # filter memoizes per (account, role, tier).
        from capabilities.alerting.relevance import filter_users_by_alert_access
        subscribers = await filter_users_by_alert_access(subscribers, "camera")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        for account_id, subs in acct_subs.items():
            is_warmup = account_id not in _camera_warmup_done

            bot_app = get_app_for_account(account_id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping camera check", account_id)
                continue
            tenant = await get_tenant_db(account_id)
            await run_account_job(
                _check_cameras_account(bot_app, account_id, subs, is_warmup),
                account_id=account_id,
                job_name="camera_check",
                timeout=CAMERA_JOB_TIMEOUT,
                tenant_db=tenant,
            )

            if is_warmup:
                _camera_warmup_done.add(account_id)
                logger.info("Camera alerts: warm-up cycle done for account %d, "
                            "cached %d issue(s)", account_id,
                            len(_known_camera_issues.get(account_id, set())))

    except Exception as e:
        logger.error(f"check_camera_alerts failed: {e}")


async def _check_cameras_account(
    bot_app: Application, account_id: int, subs: list, is_warmup: bool,
):
    """Process camera alerts for a single account."""
    from .service import gather_snapshots, analyze_snapshot, save_camera_results
    import capabilities.ai as ai

    await ai.ensure_account_model(account_id)
    snapshots, show_co = await gather_snapshots(account_id)
    if not snapshots:
        return

    # DO NOT RAISE THIS on the current Vertex quota.  It looks like the
    # obvious lever for the job's runtime, and it is the wrong one:
    # 2,278 of the last 5,970 vision calls (38%) already came back 429.
    # The quota is the binding constraint, not our parallelism, so more
    # concurrency buys a higher rejection rate rather than throughput —
    # and there is no retry here, so a 429 means that camera is simply
    # not analysed this cycle.
    #
    # Revisit when the account moves off the limited tier; until then the
    # honest levers are fewer calls per run (not every camera every
    # cycle) or more quota.
    sem = asyncio.Semaphore(3)
    tasks = [analyze_snapshot(s, account_id, sem) for s in snapshots]
    results = await asyncio.gather(*tasks)

    await save_camera_results(account_id, results)

    issues = [
        r for r in results
        if r.get("status") in ("PROBLEM", "WARNING")
    ]

    if is_warmup:
        known = set()
        for r in issues:
            key = f"{r['vehicle']}:{r.get('camera_type', 'forward')}"
            known.add(key)
        _known_camera_issues[account_id] = known
        return

    previously_known = _known_camera_issues.get(account_id, set())
    new_issues = []
    current_keys = set()

    for r in issues:
        key = f"{r['vehicle']}:{r.get('camera_type', 'forward')}"
        current_keys.add(key)
        if key not in previously_known:
            new_issues.append(r)

    _known_camera_issues[account_id] = current_keys

    if not new_issues:
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 View Full Check", callback_data="cmd_camera_report")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])

    # Forum routing → Cameras topic.  Post an account-wide summary
    # (unfiltered) once when the account has a routing row; driver
    # DMs below stay filtered to each driver's assigned truck so
    # privacy isolation is preserved.  Admins who are members of the
    # group will see the topic AND their existing DM — same
    # mirror-behavior as CRITICAL alerts; they can mute the topic on
    # their phone if they prefer DM-only.
    try:
        from capabilities.alerting.pipeline import post_alert_to_topic
        from infra.platform import get_platform_db as _gp
        problems = sum(1 for r in new_issues if r.get("status") == "PROBLEM")
        warnings = sum(1 for r in new_issues if r.get("status") == "WARNING")

        # Per-account AI toggle for Cameras: when OFF the group gets
        # photo + vehicle name + "flagged for review" without the
        # AI's per-image description text.  Operators eyeball the
        # images and decide for themselves; useful when an account
        # wants vision-based detection but doesn't want AI prose in
        # the group.
        _include_ai = (
            await _gp().get_account_setting(
                account_id, "forum_ai.camera", default="1",
            )
        ) != "0"

        # Option A grammar.  Severity = critical when any PROBLEM rows,
        # warning otherwise.  The summary line replaces the bullet
        # list with a one-line roll-up; each issue gets its own photo
        # caption below so per-issue detail is not lost.
        from capabilities.formatting.severity import badge
        digest_sev = "critical" if problems else "warning"

        roll_parts: list[str] = []
        if problems:
            roll_parts.append(f"🔴 {problems} problem(s)")
        if warnings:
            roll_parts.append(f"🔸 {warnings} warning(s)")

        topic_lines = [
            f"<b>{badge(digest_sev)}</b> — Camera Issues",
            "",
            "📷 All vehicles · " + "  ·  ".join(roll_parts) if roll_parts else "📷 All vehicles",
            "",
        ]
        for r in new_issues[:10]:
            row_marker = "🔴" if r.get("status") == "PROBLEM" else "🔸"
            cam_icon = {"forward": "🎥", "inward": "🪞"}.get(
                r.get("camera_type", "forward"), "📷")
            co_label = f" ({r.get('company', '?')})" if show_co else ""
            if _include_ai:
                summary = r.get("summary", "")[:120]
                topic_lines.append(
                    f"{row_marker} <b>#{r['vehicle']}</b>{co_label} {cam_icon} — {summary}"
                )
            else:
                topic_lines.append(f"{row_marker} <b>#{r['vehicle']}</b>{co_label} {cam_icon}")
        if len(new_issues) > 10:
            topic_lines.append(f"\n… +{len(new_issues) - 10} more")
        if not _include_ai:
            topic_lines.append(
                "\n<i>AI analysis disabled — review images below.</i>"
            )
        topic_lines.append("")
        topic_lines.append("💡 Review images · contact driver for repeated issues")
        topic_text = "\n".join(filter(None, topic_lines))
        # No reply_markup for the group post: ``View Full Check`` and
        # ``Main Menu`` both rewrite the message in place via callback,
        # which would erase the alert for every other shift looking
        # at the topic.  The DM fanout below keeps those buttons since
        # each recipient is the sole viewer of their own DM.
        posted_to_topic = await post_alert_to_topic(
            bot_app, account_id=account_id,
            alert_type="camera", text=topic_text,
            severity=digest_sev,
        )

        # Per-issue photo posts in the group so each photo carries its
        # own full description as caption — fixes the earlier "grid of
        # photos + separate text" pattern where users couldn't tell
        # which photo matched which description.  One message per
        # issue mirrors the DM layout admins are already used to.
        #
        # Posts are paced at 0.5s each to stay under Telegram's per-
        # chat flood limit (~20 msg/min/chat).  10-issue cap means
        # max ~5s of posting per camera-check cycle.
        if posted_to_topic:
            try:
                route = await _gp().get_alert_route(account_id, "camera")
                if route:
                    import io as _io
                    issues_with_photos = [
                        r for r in new_issues[:10] if r.get("image_bytes")
                    ]
                    for r in issues_with_photos:
                        row_marker = "🔴" if r.get("status") == "PROBLEM" else "🔸"
                        cam_icon = {"forward": "🎥", "inward": "🪞"}.get(
                            r.get("camera_type", "forward"), "📷")
                        co_label = f" ({r.get('company', '?')})" if show_co else ""
                        summary = r.get("summary", "") if _include_ai else ""
                        caption = f"{row_marker} <b>#{r['vehicle']}{co_label}</b> {cam_icon}"
                        if summary:
                            caption += f"\n{summary[:900]}"
                        try:
                            photo = _io.BytesIO(r["image_bytes"])
                            photo.name = f"cam_{r['vehicle']}.jpg"
                            await bot_app.bot.send_photo(
                                chat_id=route.chat_id,
                                message_thread_id=route.message_thread_id,
                                photo=photo,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                read_timeout=20, write_timeout=20,
                            )
                        except Exception as photo_err:
                            logger.debug(
                                "Camera photo post to topic failed for %s: %s",
                                r.get("vehicle"), photo_err,
                            )
                        await asyncio.sleep(0.5)
            except Exception as media_err:
                logger.debug(
                    "Camera media-group post to topic failed: %s", media_err,
                )
    except Exception as e:
        logger.debug("Forum post for camera alerts failed: %s", e)

    import io
    # Company scope: restrict each subscriber's camera digest to the
    # companies they're allowed (per-user override, else role assignment).
    # Owner/unrestricted → full fleet; an issue we cannot attribute is
    # withheld from a restricted subscriber (see _issues_for_companies).
    from capabilities.alerting.company_scope import load_company_scope, subscriber_companies
    from capabilities.alerting.dnd import is_user_dnd_active
    from interfaces.bot.state import get_tenant_db as _get_tenant_db
    _user_co = await load_company_scope(account_id)
    # Tenant DB handle for the per-sub DND queue.  Fetched once
    # (cheap pool lookup) so the per-subscriber loop doesn't
    # re-resolve it on every iteration.
    _camera_tenant = await _get_tenant_db(account_id)
    for sub in subs:
        # Driver-role isolation: drivers only see camera alerts for
        # their own assigned truck.  Account-wide subscribers (owner /
        # admin / fleet manager) keep the full-fleet view.  Without
        # this, drivers received every camera issue across every
        # company in the account — a privacy regression.
        sub_issues = _issues_for_subscriber(sub, new_issues)
        _sub_co = subscriber_companies(sub, _user_co)
        if _sub_co:
            # Keyed on the WIRE code, and fail-CLOSED.  Two reasons this
            # is not "company":  that key is a display label carrying a
            # "?" placeholder, and the old test `not r.get("company")`
            # let any row we could not attribute through to every
            # restricted subscriber — a fleet manager scoped to one
            # company saw another's truck whenever the org tag was
            # missing.  Blank company = denied for a restricted viewer,
            # the same wall every other feature applies.  Account-wide
            # subscribers have no _sub_co and still see everything.
            sub_issues = _issues_for_companies(sub_issues, set(_sub_co))
        if not sub_issues:
            continue  # nothing relevant for this subscriber; skip silently

        sub_problems = sum(1 for r in sub_issues if r.get("status") == "PROBLEM")
        sub_warnings = sum(1 for r in sub_issues if r.get("status") == "WARNING")
        # DND gate — mirrors pipeline.send_alert's per-subscriber DND
        # check (capabilities/alerting/pipeline.py:1055).  Without this
        # the per-user digest pushes to Telegram regardless of the
        # subscriber's "Don't disturb me off-shift" toggle.  PROBLEM
        # rows are treated as critical (PROBLEM = camera detected
        # something wrong, equivalent to a critical-severity alert in
        # the main pipeline) so they bypass DND.  Warning-only
        # digests respect DND and queue for shift-start.
        bypasses_dnd = sub_problems > 0
        if not bypasses_dnd:
            try:
                if await is_user_dnd_active(sub, _camera_tenant):
                    # Queue a single rolled-up DM so the subscriber
                    # gets the digest when they come on-shift.  Body
                    # rebuilt to match what the live push would have
                    # said; the warning count + first vehicle name
                    # are enough to triage at shift-start.
                    first_v = sub_issues[0].get("vehicle", "?")
                    queued_text = (
                        f"📷 Camera alerts ({sub_warnings} warning"
                        f"{'s' if sub_warnings != 1 else ''}) — "
                        f"#{first_v}"
                        + (f" +{len(sub_issues) - 1} more" if len(sub_issues) > 1 else "")
                    )
                    # Spine quiet queue — delivered in the recipient's
                    # off-shift summary by the hourly quiet flush
                    # (replaces the retired private dnd_alert_queue).
                    from capabilities.notifications.quiet_hours import (
                        defer_notification)
                    await defer_notification(
                        _camera_tenant, account_id,
                        user_id=sub.id, address=str(sub.telegram_id),
                        category="alert.camera", line=queued_text,
                        severity="warning",
                    )
                    continue
            except Exception as e:
                logger.debug("Camera DND check failed for %s: %s — sending anyway", sub.telegram_id, e)
        from capabilities.formatting.severity import badge as _badge
        sub_sev = "critical" if sub_problems else "warning"
        sub_roll = []
        if sub_problems:
            sub_roll.append(f"🔴 {sub_problems} problem(s)")
        if sub_warnings:
            sub_roll.append(f"🔸 {sub_warnings} warning(s)")
        sub_header_lines = [
            f"<b>{_badge(sub_sev)}</b> — Camera Issues",
            "",
            "📷 " + "  ·  ".join(sub_roll) if sub_roll else "📷 issues detected",
        ]
        sub_header_text = "\n".join(sub_header_lines)
        if len(sub_issues) > 10:
            sub_header_text += f"\n\n… +{len(sub_issues) - 10} more"

        try:
            for r in sub_issues[:10]:
                row_marker = "🔴" if r.get("status") == "PROBLEM" else "🔸"
                cam_icon = {"forward": "🎥", "inward": "🪞"}.get(
                    r.get("camera_type", "forward"), "📷")
                co_label = f" ({r.get('company', '?')})" if show_co else ""
                summary = r.get("summary", "")[:200]
                caption = (
                    f"{row_marker} <b>#{r['vehicle']}{co_label}</b> {cam_icon}\n"
                    f"{summary}"
                )
                if r.get("image_bytes"):
                    try:
                        photo = io.BytesIO(r["image_bytes"])
                        photo.name = f"cam_{r['vehicle']}.jpg"
                        await bot_app.bot.send_photo(
                            chat_id=sub.telegram_id,
                            photo=photo,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        await bot_app.bot.send_message(
                            chat_id=sub.telegram_id,
                            text=caption,
                            parse_mode=ParseMode.HTML,
                        )
                else:
                    await bot_app.bot.send_message(
                        chat_id=sub.telegram_id,
                        text=caption,
                        parse_mode=ParseMode.HTML,
                    )
                await asyncio.sleep(0.15)

            await bot_app.bot.send_message(
                chat_id=sub.telegram_id,
                text=sub_header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception as e:
            logger.error(
                f"Camera alert to {sub.telegram_id}: {e}"
            )
        await asyncio.sleep(0.3)

    logger.info(
        f"Camera alert for account {account_id}: "
        f"{len(new_issues)} new issue(s)"
    )
