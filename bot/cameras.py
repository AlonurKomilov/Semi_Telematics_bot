"""Camera check — dashcam position & obstruction analysis.

Features:
  • Full fleet camera check (all vehicles)
  • Single-vehicle camera check (from truck detail view)
  • Camera check history stored in DB for trend tracking
  • PDF / CSV export for camera status reports
  • Multi-camera support (forward + inward)
  • Concurrent AI analysis with rate-limit-aware Telegram output
"""

import asyncio
import io
import time
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from permissions import can
from samsara_client import COMPANY_DISPLAY, populate_company_display

import ai_client
from bot.config import db, logger
from bot.keyboards import back_kb
from bot.helpers import _show, _show_loading
from bot.auth import _require_registered

# ── Constants ────────────────────────────────────────────────────

_MAX_CAPTION = 1024          # Telegram photo caption limit
_MAX_TEXT_MSG = 4096         # Telegram text message limit
_SEND_DELAY = 0.35           # seconds between sends (rate-limit safety)
_AI_CONCURRENCY = 5          # max parallel AI vision calls
_CAM_ICONS = {"forward": "🎥", "inward": "🪞"}

# ── Result cache (per account, avoids duplicate API + AI work) ───
# Stores {account_id: (timestamp, results_without_image_bytes)}
# TTL: 1 hour.  Samsara media images are periodic snapshots so
# camera positions don't change within an hour.
_CACHE_TTL = 3600            # 1 hour in seconds
_result_cache: dict[int, tuple[float, list[dict]]] = {}
_running_locks: dict[int, asyncio.Event] = {}  # account_id → Event


def _cache_get(account_id: int) -> tuple[list[dict], float] | None:
    """Return (results, age_seconds) if fresh cache exists, else None."""
    entry = _result_cache.get(account_id)
    if entry is None:
        return None
    ts, results = entry
    age = time.time() - ts
    if age > _CACHE_TTL:
        _result_cache.pop(account_id, None)
        return None
    return results, age


def _cache_set(account_id: int, results: list[dict]):
    """Store results in cache (strip image_bytes to save memory)."""
    stripped = []
    for r in results:
        r2 = {k: v for k, v in r.items() if k != "image_bytes"}
        stripped.append(r2)
    _result_cache[account_id] = (time.time(), stripped)


def _status_icon(status: str) -> str:
    """Map analysis status to emoji."""
    return {"OK": "✅", "WARNING": "⚠️", "PROBLEM": "🚨", "NO_DATA": "📵"}.get(status, "❓")


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit, adding ellipsis if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _camera_check_kb(show_export: bool = False) -> InlineKeyboardMarkup:
    """Keyboard shown after camera check results."""
    rows = []
    rows.append([InlineKeyboardButton("📷 History", callback_data="cam_check_history")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


# ── Analyze helper (concurrent with semaphore) ───────────────────

async def _analyze_snapshot(snap: dict, account_id: int,
                            sem: asyncio.Semaphore) -> dict:
    """Analyze a single snapshot with AI vision, respecting concurrency."""
    # Vehicle without dashcam footage — skip AI analysis
    if snap.get("_no_footage"):
        return {
            "vehicle": snap["vehicle_name"],
            "vehicle_id": snap.get("vehicle_id", ""),
            "company": snap.get("_org", "?"),
            "driver": snap.get("driver_name", ""),
            "event_time": "",
            "camera_type": snap.get("camera_type", "forward"),
            "image_bytes": b"",
            "status": "NO_DATA",
            "obstruction": "unknown",
            "alignment": "unknown",
            "quality": "unknown",
            "summary": "No camera images available (vehicle may be parked/inactive)",
        }

    async with sem:
        try:
            analysis = await ai_client.analyze_camera_image(
                snap["image_bytes"],
                vehicle_name=snap["vehicle_name"],
                account_id=account_id,
            )
            return {
                "vehicle": snap["vehicle_name"],
                "vehicle_id": snap.get("vehicle_id", ""),
                "company": snap.get("_org", "?"),
                "driver": snap.get("driver_name", ""),
                "event_time": snap.get("event_time", ""),
                "camera_type": snap.get("camera_type", "forward"),
                "image_bytes": snap["image_bytes"],
                **analysis,
            }
        except Exception as e:
            logger.warning(
                f"Camera AI analysis failed for {snap['vehicle_name']}: {e}"
            )
            return {
                "vehicle": snap["vehicle_name"],
                "vehicle_id": snap.get("vehicle_id", ""),
                "company": snap.get("_org", "?"),
                "driver": snap.get("driver_name", ""),
                "event_time": snap.get("event_time", ""),
                "camera_type": snap.get("camera_type", "forward"),
                "image_bytes": snap["image_bytes"],
                "status": "ERROR",
                "obstruction": "unknown",
                "alignment": "unknown",
                "quality": "unknown",
                "summary": f"Analysis error: {e}",
            }


# ── Build caption for a result ───────────────────────────────────

def _build_caption(r: dict, show_co: bool) -> str:
    """Build Telegram caption for a camera check result."""
    icon = _status_icon(r.get("status", "OK"))
    cam_icon = _CAM_ICONS.get(r.get("camera_type", "forward"), "📷")
    co_label = f" ({r['company']})" if show_co else ""
    caption_lines = [
        f"{icon} <b>#{r['vehicle']}{co_label}</b> {cam_icon}",
    ]
    if r.get("driver"):
        caption_lines.append(f"  👤 {r['driver']}")
    if r.get("event_time"):
        try:
            et = datetime.fromisoformat(
                r["event_time"].replace("Z", "+00:00")
            )
            caption_lines.append(f"  🕐 {et.strftime('%b %d, %H:%M')}")
        except (ValueError, AttributeError):
            pass

    caption_lines.append(f"\n  {r.get('summary', 'No analysis')}")

    if r.get("status") in ("PROBLEM", "WARNING", "ERROR"):
        caption_lines.append(
            f"\n  📐 Alignment: {r.get('alignment', '?')}"
        )
        caption_lines.append(
            f"  🔍 Obstruction: {r.get('obstruction', '?')}"
        )
        caption_lines.append(
            f"  💡 Quality: {r.get('quality', '?')}"
        )

    return "\n".join(caption_lines)


# ── Gather snapshots from all companies ──────────────────────────

async def _gather_snapshots(account_id: int,
                            vehicle_name: str | None = None,
                            days: int = 10) -> tuple[list[dict], bool]:
    """Fetch dashcam snapshots, optionally filtered to one vehicle.

    Returns (snapshots, show_company_label).
    Primary: camera media API (24h periodic stills, ~51/62 trucks).
    Fallback: safety events (10d) for remaining trucks.
    Only includes active vehicles (matching fault report filtering).
    """
    companies = await db.get_account_companies(account_id)
    populate_company_display(companies)
    show_co = len(companies) > 1

    all_snapshots: list[dict] = []
    for co in companies:
        from samsara_client import SamsaraClient
        client = SamsaraClient(
            api_key=co.samsara_api_key,
            active_days=co.active_days,
        )
        try:
            snaps = await client.get_dashcam_snapshots(days=days)
            for s in snaps:
                s["_org"] = co.code

            # Add active vehicles without footage as NO_DATA entries.
            # Uses get_vehicles() + get_locations() (2 API calls, parallel)
            # and applies _is_active() filter so only active trucks appear
            # (matching the fault report's ~62 active vehicles).
            if not vehicle_name:
                try:
                    vehicles_raw, locations_raw = await asyncio.gather(
                        client.get_vehicles(),
                        client.get_locations(),
                    )
                    loc_map = {v["id"]: v for v in locations_raw}
                    snapped_ids = {s["vehicle_id"] for s in snaps if s.get("vehicle_id")}
                    for v in vehicles_raw:
                        vid = v.get("id", "")
                        vname = v.get("name", "?")
                        loc_data = loc_map.get(vid, {})
                        loc = loc_data.get("location", {})
                        if not client._is_active(v, loc):
                            continue
                        if vid and vid not in snapped_ids:
                            snaps.append({
                                "vehicle_name": vname,
                                "vehicle_id": vid,
                                "driver_name": "",
                                "event_time": "",
                                "image_bytes": b"",
                                "camera_type": "forward",
                                "_org": co.code,
                                "_no_footage": True,
                            })
                except Exception as e:
                    logger.debug(f"Fleet list fetch failed for {co.code}: {e}")

            if vehicle_name:
                snaps = [
                    s for s in snaps
                    if s["vehicle_name"].lower() == vehicle_name.lower()
                ]
            all_snapshots.extend(snaps)
        except Exception as e:
            logger.warning(f"Camera snapshots failed for {co.code}: {e}")
        finally:
            await client.close()

    return all_snapshots, show_co


# ── Store results in DB ─────────────────────────────────────────

async def _save_camera_results(account_id: int, results: list[dict]):
    """Persist camera check results for history tracking."""
    for r in results:
        try:
            await db.save_camera_check(
                account_id=account_id,
                vehicle_id=r.get("vehicle_id", ""),
                vehicle_name=r.get("vehicle", "?"),
                camera_type=r.get("camera_type", "forward"),
                status=r.get("status", "OK"),
                obstruction=r.get("obstruction", "none"),
                alignment=r.get("alignment", "centered"),
                quality=r.get("quality", "good"),
                summary=r.get("summary", ""),
            )
        except Exception as e:
            logger.debug(f"Camera history save failed: {e}")


# ── Send results to chat (rate-limited) ─────────────────────────

async def _send_results(update, context, results: list[dict],
                        show_co: bool):
    """Send individual vehicle results with rate-limit delays."""
    chat_id = (
        update.callback_query.message.chat.id
        if update.callback_query
        else update.effective_chat.id
    )

    for r in results:
        caption = _build_caption(r, show_co)

        # Send photo for problems/warnings, text-only for OK
        if r.get("status") in ("PROBLEM", "WARNING") and r.get("image_bytes"):
            caption = _truncate(caption, _MAX_CAPTION)
            try:
                photo = io.BytesIO(r["image_bytes"])
                photo.name = f"cam_{r['vehicle']}.jpg"
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=_truncate(caption, _MAX_TEXT_MSG),
                    parse_mode=ParseMode.HTML,
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_truncate(caption, _MAX_TEXT_MSG),
                parse_mode=ParseMode.HTML,
            )
        await asyncio.sleep(_SEND_DELAY)


# ── Helpers for progress + cache display ──────────────────────────

def _age_text(age_seconds: float) -> str:
    """Human-readable age string."""
    m = int(age_seconds) // 60
    if m < 1:
        return "less than a minute ago"
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = m // 60
    return f"{h} hour{'s' if h != 1 else ''} ago"


def _build_summary(results: list[dict], *, cached_age: float | None = None) -> str:
    """Build the summary header text from results."""
    problems = sum(1 for r in results if r.get("status") == "PROBLEM")
    warnings = sum(1 for r in results if r.get("status") == "WARNING")
    ok_count = sum(1 for r in results if r.get("status") == "OK")
    errors = sum(1 for r in results if r.get("status") == "ERROR")
    no_data_cnt = sum(1 for r in results if r.get("status") == "NO_DATA")
    checked = len(results) - no_data_cnt

    header = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Camera Check Results\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {len(results)} vehicle(s) total, "
        f"{checked} with footage\n"
    )
    if cached_age is not None:
        header += f"  🔄 Cached — checked {_age_text(cached_age)}\n"
    if problems:
        header += f"  🚨 {problems} problem(s)\n"
    if warnings:
        header += f"  ⚠️ {warnings} warning(s)\n"
    if ok_count:
        header += f"  ✅ {ok_count} OK\n"
    if errors:
        header += f"  ❓ {errors} error(s)\n"
    if no_data_cnt:
        header += f"  📵 {no_data_cnt} no footage\n"
    return header


async def _update_progress(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           done: int, total: int, title: str = "Camera Check"):
    """Edit the loading message to show analysis progress."""
    chat_id = update.effective_chat.id
    query = update.callback_query
    msg_id = None
    if query:
        msg_id = query.message.message_id

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  📷 {title}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Analyzing cameras… {done}/{total}\n"
        f"  {'█' * (done * 20 // max(total, 1))}{'░' * (20 - done * 20 // max(total, 1))}\n"
        "\n  ⏳ Please wait..."
    )
    try:
        if msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text,
            )
        else:
            # Fallback: send new message (shouldn't normally happen)
            pass
    except BadRequest:
        pass  # message unchanged or deleted


async def _analyze_with_progress(
    snapshots: list[dict],
    account_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    title: str = "Camera Check",
) -> list[dict]:
    """Run AI analysis on all snapshots with a live progress counter.

    Updates the loading message every 5 completions so the user sees
    'Analyzing cameras… 15/51' instead of a static spinner.
    """
    total = len(snapshots)
    done = 0
    sem = asyncio.Semaphore(_AI_CONCURRENCY)
    results: list[dict] = [None] * total  # type: ignore[list-item]

    # Show initial progress
    await _update_progress(update, context, 0, total, title)

    async def _run_one(idx: int, snap: dict):
        nonlocal done
        r = await _analyze_snapshot(snap, account_id, sem)
        results[idx] = r
        done += 1
        # Update progress every 5 items (avoid Telegram flood)
        if done % 5 == 0 or done == total:
            await _update_progress(update, context, done, total, title)

    await asyncio.gather(*[_run_one(i, s) for i, s in enumerate(snapshots)])
    return results


# ══════════════════════════════════════════════════════════════════
# Commands
# ══════════════════════════════════════════════════════════════════

# ── Camera Check tool (Tools → Company → Truck → single result) ──

@_require_registered
async def cmd_cam_tool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for Camera Check in Tools — shows company picker."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    companies = await db.get_account_companies(user.account_id)
    populate_company_display(companies)
    codes = [o.code for o in companies]

    if len(codes) == 1:
        # Single company — skip picker, show truck list directly
        await _show_cam_truck_list(update, context, user, codes[0])
        return

    from bot.keyboards import cam_company_picker_kb
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Camera Check\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Select a company:"
    )
    await _show(update, context, [text], keyboard=cam_company_picker_kb(codes))


async def _show_cam_truck_list(update, context, user, company_filter, page=0):
    """Show paginated truck list for camera check."""
    from bot.keyboards import cam_vehicle_list_kb
    from bot.config import get_client

    orgs_db = await db.get_account_companies(user.account_id)
    populate_company_display(orgs_db)

    await _show_loading(update, context,
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Camera Check\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Loading trucks..."
    )
    try:
        samsara = await get_client(user.account_id)
        vehicles = await samsara.get_fleet_overview(company=company_filter)
        if not vehicles:
            await _show(update, context, [
                "━━━━━━━━━━━━━━━━━━━\n"
                "  📷 Camera Check\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"\n  No trucks found for {company_filter}."
            ], keyboard=back_kb())
            return

        co_display = COMPANY_DISPLAY.get(company_filter, company_filter)
        header = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Camera Check\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  🚛 {len(vehicles)} truck(s) — {co_display}\n"
            "\n  Tap a truck to check its camera:"
        )
        kb = cam_vehicle_list_kb(vehicles, page=page, company_filter=company_filter)
        await _show(update, context, [header], keyboard=kb)
    except Exception as e:
        logger.error(f"Cam truck list failed: {e}")
        await _show(update, context, [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌ Error loading trucks\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {type(e).__name__}: {e}"
        ], keyboard=back_kb())


# ── Fleet-wide camera check ──────────────────────────────────────

@_require_registered
async def cmd_camera_check(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           vehicle_name: str | None = None):
    """Run a dashcam position & obstruction check.

    If vehicle_name is provided, checks only that vehicle.
    Otherwise checks all vehicles across all companies.

    Results are cached per account for 1 hour.  If a fresh cache
    exists, the cached summary is shown instantly.  If another user
    triggers a check while one is already running for the same account,
    they wait for the running check to finish.
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    account_id = user.account_id

    # ── Serve cached results (fleet-wide only) ──────────────────
    if not vehicle_name:
        cached = _cache_get(account_id)
        if cached is not None:
            results, age = cached
            header = _build_summary(results, cached_age=age)
            kb = _camera_check_kb(show_export=len(results) > 0)
            context.user_data["_camera_results"] = results
            await _show(update, context, [header], keyboard=kb)
            return

    # ── Wait if another check is already running ────────────────
    if not vehicle_name and account_id in _running_locks:
        wait_text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Camera Check\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  ⏳ Another camera check is already\n"
            "  running for your fleet.\n"
            "  Waiting for results..."
        )
        await _show_loading(update, context, wait_text)
        try:
            await asyncio.wait_for(_running_locks[account_id].wait(), timeout=300)
        except asyncio.TimeoutError:
            pass
        # Now serve the cached results from the other run
        cached = _cache_get(account_id)
        if cached is not None:
            results, age = cached
            header = _build_summary(results, cached_age=age)
            kb = _camera_check_kb(show_export=len(results) > 0)
            context.user_data["_camera_results"] = results
            await _show(update, context, [header], keyboard=kb)
            return

    # ── Run fresh check ─────────────────────────────────────────
    if not vehicle_name:
        lock = asyncio.Event()
        _running_locks[account_id] = lock

    scope = f" for #{vehicle_name}" if vehicle_name else ""
    loading_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Camera Check\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  Downloading dashcam images{scope}...\n"
        "\n  ⏳ This may take a minute."
    )
    await _show_loading(update, context, loading_text)

    try:
        all_snapshots, _show_co = await _gather_snapshots(
            account_id, vehicle_name=vehicle_name,
        )

        if not all_snapshots:
            no_data = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "  📷 Camera Check\n"
                "━━━━━━━━━━━━━━━━━━━\n"
            )
            if vehicle_name:
                no_data += (
                    f"\n  No dashcam footage found for\n"
                    f"  #{vehicle_name}."
                )
            else:
                no_data += (
                    "\n  No dashcam footage found\n"
                    "  for any vehicle."
                )
            await _show(update, context, [no_data], keyboard=back_kb())
            return

        # Analyze with live progress counter
        results = await _analyze_with_progress(
            all_snapshots, account_id, update, context,
        )

        # Sort: problems first, then warnings, then OK, then no-data
        priority = {"PROBLEM": 0, "WARNING": 1, "ERROR": 2, "OK": 3, "NO_DATA": 4}
        results = sorted(
            results,
            key=lambda r: (priority.get(r.get("status", "OK"), 9), r["vehicle"]),
        )

        # Save to DB for history
        await _save_camera_results(account_id, results)

        # Cache results for other users (fleet-wide only)
        if not vehicle_name:
            _cache_set(account_id, results)

        # Store in context for PDF/CSV export
        context.user_data["_camera_results"] = results

        if vehicle_name:
            # Single truck — send screenshot + AI text directly
            show_co = len(await db.get_account_companies(account_id)) > 1
            await _send_results(update, context, results, show_co)
        else:
            # Fleet-wide — show summary only
            header = _build_summary(results)
            kb = _camera_check_kb(show_export=len(results) > 0)
            await _show(update, context, [header], keyboard=kb)

    except Exception as e:
        logger.error(f"Camera check failed: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌ Camera Check Error\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {type(e).__name__}: {e}"
        )
        await _show(update, context, [text], keyboard=back_kb())
    finally:
        if not vehicle_name and account_id in _running_locks:
            _running_locks[account_id].set()
            _running_locks.pop(account_id, None)


# ── Single-vehicle camera check (from truck detail) ─────────────

@_require_registered
async def cmd_camera_check_truck(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 truck_name: str):
    """Run camera check for a single truck."""
    await cmd_camera_check(update, context, vehicle_name=truck_name)


# ── Camera check history ────────────────────────────────────────

@_require_registered
async def cmd_camera_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent camera check history from DB."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    try:
        history = await db.get_camera_check_history(
            user.account_id, limit=30,
        )
    except Exception as e:
        logger.warning(f"Camera history fetch failed: {e}")
        history = []

    if not history:
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Camera History\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  No camera checks recorded yet.\n"
            "  Run a camera check first."
        )
        await _show(update, context, [text], keyboard=back_kb())
        return

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  📷 Camera History",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # Group by vehicle — show latest status and trend
    by_vehicle: dict[str, list[dict]] = {}
    for h in history:
        key = h["vehicle_name"]
        by_vehicle.setdefault(key, []).append(h)

    for vname, checks in sorted(by_vehicle.items()):
        latest = checks[0]
        icon = _status_icon(latest["status"])
        cam_icon = _CAM_ICONS.get(latest.get("camera_type", "forward"), "📷")
        try:
            dt = datetime.fromisoformat(latest["checked_at"].replace("Z", "+00:00"))
            time_str = dt.strftime("%b %d, %H:%M")
        except (ValueError, AttributeError, KeyError):
            time_str = "?"

        # Show trend: count of problems in last N checks
        problem_count = sum(
            1 for c in checks if c["status"] in ("PROBLEM", "WARNING")
        )
        trend = ""
        if problem_count > 1:
            trend = f" ⚠️ {problem_count}× issues"
        elif problem_count == 1 and latest["status"] in ("PROBLEM", "WARNING"):
            trend = " (new)"

        lines.append(
            f"  {icon} <b>#{vname}</b> {cam_icon} — {time_str}{trend}"
        )
        if latest["status"] != "OK":
            lines.append(f"     {latest.get('summary', '')[:60]}")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Run Check Now", callback_data="cmd_camera_check")],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
    ])
    await _show(update, context, [text], keyboard=kb)


# ── PDF Export ───────────────────────────────────────────────────

@_require_registered
async def cmd_camera_check_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export the latest camera check results as PDF."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    results = context.user_data.get("_camera_results")
    if not results:
        if update.callback_query:
            await update.callback_query.answer(
                "No results — run a camera check first", show_alert=True
            )
        return

    await _show_loading(update, context, "📄  Generating PDF...")

    try:
        from pdf_generator import generate_camera_check_pdf
        pdf_buf = await asyncio.get_event_loop().run_in_executor(
            None, generate_camera_check_pdf, results,
        )
        from bot.fleet_reports import _send_report_doc
        await _send_report_doc(
            update, context, pdf_buf, "Camera_Check", "pdf", None,
            f"📷 Camera Check — {len(results)} vehicle(s)",
            back_kb(),
        )
    except Exception as e:
        logger.error(f"Camera PDF export failed: {e}")
        text = f"❌ Camera PDF failed: {type(e).__name__}"
        await _show(update, context, [text], keyboard=back_kb())


# ── CSV Export ───────────────────────────────────────────────────

@_require_registered
async def cmd_camera_check_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export the latest camera check results as CSV."""
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    results = context.user_data.get("_camera_results")
    if not results:
        if update.callback_query:
            await update.callback_query.answer(
                "No results — run a camera check first", show_alert=True
            )
        return

    await _show_loading(update, context, "📊  Generating CSV...")

    try:
        from csv_generator import generate_camera_check_csv
        csv_buf = generate_camera_check_csv(results)
        from bot.fleet_reports import _send_report_doc
        await _send_report_doc(
            update, context, csv_buf, "Camera_Check", "csv", None,
            f"📷 Camera Check — {len(results)} vehicle(s)",
            back_kb(),
        )
    except Exception as e:
        logger.error(f"Camera CSV export failed: {e}")
        text = f"❌ Camera CSV failed: {type(e).__name__}"
        await _show(update, context, [text], keyboard=back_kb())


# ── Reports-menu camera check (runs check → directly generates PDF) ──

@_require_registered
async def cmd_camera_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Camera Check from Reports menu — gather snapshots, analyse, return PDF.

    Uses the same per-account cache as cmd_camera_check.  If fresh
    cached results exist, the PDF is regenerated from cache (fast).
    """
    user = context.user_data["_db_user"]
    if not can(user.role, "can_faults"):
        if update.callback_query:
            await update.callback_query.answer("No access", show_alert=True)
        return

    account_id = user.account_id

    # ── Try serving cached results as PDF ───────────────────────
    cached = _cache_get(account_id)
    if cached is not None:
        results, age = cached
        if results:
            loading_text = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "  📷 Camera Check Report\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"\n  🔄 Using cached results ({_age_text(age)})\n"
                "\n  ⏳ Generating PDF..."
            )
            await _show_loading(update, context, loading_text)
            try:
                context.user_data["_camera_results"] = results
                from pdf_generator import generate_camera_check_pdf
                pdf_buf = await asyncio.get_event_loop().run_in_executor(
                    None, generate_camera_check_pdf, results,
                )
                from bot.fleet_reports import _send_report_doc
                await _send_report_doc(
                    update, context, pdf_buf, "Camera_Check", "pdf", None,
                    f"📷 Camera Check — {len(results)} vehicle(s) "
                    f"(cached {_age_text(age)})",
                    back_kb(),
                )
                return
            except Exception:
                pass  # Fall through to fresh check

    # ── Wait if another check is running ────────────────────────
    if account_id in _running_locks:
        wait_text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📷 Camera Check Report\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n  ⏳ Another camera check is running.\n"
            "  Waiting for results..."
        )
        await _show_loading(update, context, wait_text)
        try:
            await asyncio.wait_for(_running_locks[account_id].wait(), timeout=300)
        except asyncio.TimeoutError:
            pass
        cached = _cache_get(account_id)
        if cached is not None:
            results, age = cached
            if results:
                context.user_data["_camera_results"] = results
                from pdf_generator import generate_camera_check_pdf
                pdf_buf = await asyncio.get_event_loop().run_in_executor(
                    None, generate_camera_check_pdf, results,
                )
                from bot.fleet_reports import _send_report_doc
                await _send_report_doc(
                    update, context, pdf_buf, "Camera_Check", "pdf", None,
                    f"📷 Camera Check — {len(results)} vehicle(s) "
                    f"(cached {_age_text(age)})",
                    back_kb(),
                )
                return

    # ── Run fresh check ─────────────────────────────────────────
    lock = asyncio.Event()
    _running_locks[account_id] = lock

    loading_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  📷 Camera Check Report\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n  Downloading dashcam images and\n"
        "  analyzing camera positions...\n"
        "\n  ⏳ Generating PDF — this may take a minute."
    )
    await _show_loading(update, context, loading_text)

    try:
        all_snapshots, show_co = await _gather_snapshots(account_id)

        if not all_snapshots:
            no_data = (
                "━━━━━━━━━━━━━━━━━━━\n"
                "  📷 Camera Check Report\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "\n  No dashcam footage found\n"
                "  for any vehicle."
            )
            await _show(update, context, [no_data], keyboard=back_kb())
            return

        results = await _analyze_with_progress(
            all_snapshots, account_id, update, context,
            title="Camera Check Report",
        )

        priority = {"PROBLEM": 0, "WARNING": 1, "ERROR": 2, "OK": 3, "NO_DATA": 4}
        results = sorted(
            results,
            key=lambda r: (priority.get(r.get("status", "OK"), 9), r["vehicle"]),
        )

        await _save_camera_results(account_id, results)
        _cache_set(account_id, results)
        context.user_data["_camera_results"] = results

        # Generate and send PDF directly
        from pdf_generator import generate_camera_check_pdf
        pdf_buf = await asyncio.get_event_loop().run_in_executor(
            None, generate_camera_check_pdf, results,
        )
        from bot.fleet_reports import _send_report_doc
        await _send_report_doc(
            update, context, pdf_buf, "Camera_Check", "pdf", None,
            f"📷 Camera Check — {len(results)} vehicle(s)",
            back_kb(),
        )
    except Exception as e:
        logger.error(f"Camera report failed: {e}")
        text = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ❌ Camera Check Report Error\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {type(e).__name__}: {e}"
        )
        await _show(update, context, [text], keyboard=back_kb())
    finally:
        if account_id in _running_locks:
            _running_locks[account_id].set()
            _running_locks.pop(account_id, None)
