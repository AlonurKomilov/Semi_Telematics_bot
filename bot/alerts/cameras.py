"""Camera alert checks — AI-powered dashcam analysis."""

from __future__ import annotations

import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from bot.config import db, logger


# Dedup: account_id → set of "vehicle:camera_type" that already alerted
_known_camera_issues: dict[int, set[str]] = {}

_camera_warmup_done = False


async def check_camera_alerts(app: Application):
    """Periodic camera check — alerts on PROBLEM/WARNING dashcams.

    Runs less frequently (daily or every few hours). Downloads fresh
    dashcam snapshots, runs AI vision analysis, and sends alerts for
    cameras that have problems (obstruction, misalignment, etc.).

    Only sends alerts for *new* issues not already flagged.
    """
    global _camera_warmup_done

    try:
        subscribers = await db.get_all_typed_subscribers("camera")
        if not subscribers:
            return

        acct_subs: dict[int, list] = {}
        for sub in subscribers:
            acct_subs.setdefault(sub.account_id, []).append(sub)

        is_warmup = not _camera_warmup_done

        for account_id, subs in acct_subs.items():
            try:
                from bot.cameras import _gather_snapshots, _analyze_snapshot, _save_camera_results
                import ai

                await ai.ensure_account_model(account_id)
                snapshots, show_co = await _gather_snapshots(account_id)
                if not snapshots:
                    continue

                sem = asyncio.Semaphore(3)  # conservative for background task
                tasks = [_analyze_snapshot(s, account_id, sem) for s in snapshots]
                results = await asyncio.gather(*tasks)

                # Save all results to history
                await _save_camera_results(account_id, results)

                # Filter to problems/warnings only
                issues = [
                    r for r in results
                    if r.get("status") in ("PROBLEM", "WARNING")
                ]

                if is_warmup:
                    # First cycle: populate dedup cache only
                    known = set()
                    for r in issues:
                        key = f"{r['vehicle']}:{r.get('camera_type', 'forward')}"
                        known.add(key)
                    _known_camera_issues[account_id] = known
                    continue

                previously_known = _known_camera_issues.get(account_id, set())
                new_issues = []
                current_keys = set()

                for r in issues:
                    key = f"{r['vehicle']}:{r.get('camera_type', 'forward')}"
                    current_keys.add(key)
                    if key not in previously_known:
                        new_issues.append(r)

                # Update dedup cache
                _known_camera_issues[account_id] = current_keys

                if not new_issues:
                    continue

                # Build summary header
                problems = sum(1 for r in new_issues if r.get("status") == "PROBLEM")
                warnings = sum(1 for r in new_issues if r.get("status") == "WARNING")

                header_lines = [
                    "━━━━━━━━━━━━━━━━━━━",
                    "  📷  <b>Camera Alert</b>",
                    "━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                if problems:
                    header_lines.append(f"  🚨 {problems} camera problem(s)")
                if warnings:
                    header_lines.append(f"  ⚠️ {warnings} camera warning(s)")

                header_text = "\n".join(header_lines)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📷 View Full Check", callback_data="cmd_camera_report")],
                    [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                ])

                # Send to each subscriber: photos first, then summary
                for sub in subs:
                    try:
                        # Send individual screenshots (up to 10)
                        import io
                        for r in new_issues[:10]:
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
                                    await app.bot.send_photo(
                                        chat_id=sub.telegram_id,
                                        photo=photo,
                                        caption=caption,
                                        parse_mode=ParseMode.HTML,
                                    )
                                except Exception:
                                    await app.bot.send_message(
                                        chat_id=sub.telegram_id,
                                        text=caption,
                                        parse_mode=ParseMode.HTML,
                                    )
                            else:
                                await app.bot.send_message(
                                    chat_id=sub.telegram_id,
                                    text=caption,
                                    parse_mode=ParseMode.HTML,
                                )
                            await asyncio.sleep(0.15)

                        if len(new_issues) > 10:
                            header_text += f"\n\n  ... +{len(new_issues) - 10} more"

                        # Send summary with keyboard
                        await app.bot.send_message(
                            chat_id=sub.telegram_id,
                            text=header_text,
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

            except Exception as e:
                logger.error(f"Camera alert check for account {account_id}: {e}")

        if is_warmup:
            _camera_warmup_done = True
            logger.info("Camera alerts: warm-up cycle done, "
                        f"cached {sum(len(v) for v in _known_camera_issues.values())} issue(s)")

    except Exception as e:
        logger.error(f"check_camera_alerts failed: {e}")
