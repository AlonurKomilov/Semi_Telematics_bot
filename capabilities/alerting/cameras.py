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
from infra.services import get_platform_db

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
    fleet-wide view they had before; account / company scoping for
    those roles is a separate concern handled by their permissions.
    """
    if sub.role == Role.DRIVER and sub.truck_num:
        my_truck = sub.truck_num.strip().lower()
        return [
            r for r in all_issues
            if my_truck in str(r.get("vehicle", "")).strip().lower()
        ]
    return list(all_issues)


# Dedup: account_id → set of "vehicle:camera_type" that already alerted
_known_camera_issues: dict[int, set[str]] = {}

_camera_warmup_done: set[int] = set()


async def check_camera_alerts(app: Application):
    """Periodic camera check — alerts on PROBLEM/WARNING dashcams.

    Runs less frequently (daily or every few hours). Downloads fresh
    dashcam snapshots, runs AI vision analysis, and sends alerts for
    cameras that have problems (obstruction, misalignment, etc.).

    Only sends alerts for *new* issues not already flagged.
    """

    try:
        subscribers = await get_platform_db().get_all_typed_subscribers("camera")
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
            await run_account_job(
                _check_cameras_account(bot_app, account_id, subs, is_warmup),
                account_id=account_id,
                job_name="camera_check",
                timeout=CAMERA_JOB_TIMEOUT,
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
    from capabilities.media.service import gather_snapshots, analyze_snapshot, save_camera_results
    import capabilities.ai as ai

    await ai.ensure_account_model(account_id)
    snapshots, show_co = await gather_snapshots(account_id)
    if not snapshots:
        return

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

    # Forum routing → Cameras topic.  Post a fleet-wide summary
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

        topic_lines = [
            "🎥 <b>Camera Alert (fleet-wide)</b>",
            "",
            f"  🚨 {problems} problem(s)" if problems else "",
            f"  ⚠️ {warnings} warning(s)" if warnings else "",
            "",
        ]
        for r in new_issues[:10]:
            icon = "🚨" if r.get("status") == "PROBLEM" else "⚠️"
            cam_icon = {"forward": "🎥", "inward": "🪞"}.get(
                r.get("camera_type", "forward"), "📷")
            co_label = f" ({r.get('company', '?')})" if show_co else ""
            if _include_ai:
                summary = r.get("summary", "")[:120]
                topic_lines.append(f"{icon} #{r['vehicle']}{co_label} {cam_icon} — {summary}")
            else:
                # AI off: just the indicator + identity, no description.
                topic_lines.append(f"{icon} #{r['vehicle']}{co_label} {cam_icon}")
        if len(new_issues) > 10:
            topic_lines.append(f"\n… +{len(new_issues) - 10} more")
        if not _include_ai:
            topic_lines.append(
                "\n<i>AI Analysis disabled for this topic — review images below.</i>"
            )
        topic_text = "\n".join(filter(None, topic_lines))
        # No reply_markup for the group post: ``View Full Check`` and
        # ``Main Menu`` both rewrite the message in place via callback,
        # which would erase the alert for every other shift looking
        # at the topic.  The DM fanout below keeps those buttons since
        # each recipient is the sole viewer of their own DM.
        posted_to_topic = await post_alert_to_topic(
            bot_app, account_id=account_id,
            alert_type="camera", text=topic_text,
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
                        icon = "🚨" if r.get("status") == "PROBLEM" else "⚠️"
                        cam_icon = {"forward": "🎥", "inward": "🪞"}.get(
                            r.get("camera_type", "forward"), "📷")
                        co_label = f" ({r.get('company', '?')})" if show_co else ""
                        summary = r.get("summary", "") if _include_ai else ""
                        caption = f"{icon} <b>#{r['vehicle']}{co_label}</b> {cam_icon}"
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
    for sub in subs:
        # Driver-role isolation: drivers only see camera alerts for
        # their own assigned truck.  Account-wide subscribers (owner /
        # admin / fleet manager) keep the full-fleet view.  Without
        # this, drivers received every camera issue across every
        # company in the account — a privacy regression.
        sub_issues = _issues_for_subscriber(sub, new_issues)
        if not sub_issues:
            continue  # nothing relevant for this subscriber; skip silently

        sub_problems = sum(1 for r in sub_issues if r.get("status") == "PROBLEM")
        sub_warnings = sum(1 for r in sub_issues if r.get("status") == "WARNING")
        sub_header_lines = [
            "━━━━━━━━━━━━━━━━━━━",
            "  📷  <b>Camera Alert</b>",
            "━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        if sub_problems:
            sub_header_lines.append(f"  🚨 {sub_problems} camera problem(s)")
        if sub_warnings:
            sub_header_lines.append(f"  ⚠️ {sub_warnings} camera warning(s)")
        sub_header_text = "\n".join(sub_header_lines)
        if len(sub_issues) > 10:
            sub_header_text += f"\n\n  ... +{len(sub_issues) - 10} more"

        try:
            for r in sub_issues[:10]:
                icon = "🚨" if r.get("status") == "PROBLEM" else "⚠️"
                cam_icon = {"forward": "🎥", "inward": "🪞"}.get(
                    r.get("camera_type", "forward"), "📷")
                co_label = f" ({r.get('company', '?')})" if show_co else ""
                summary = r.get("summary", "")[:200]
                caption = (
                    f"{icon} <b>#{r['vehicle']}{co_label}</b> {cam_icon}\n"
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
