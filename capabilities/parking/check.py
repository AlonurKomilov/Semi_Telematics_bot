"""Scheduled parking-check job.

Holds module-level state for the consecutive-stopped counter and the
post-restart suppression flag.  All classification, formatting, map
rendering, and AI logic lives in sibling modules.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telegram.ext import Application

from capabilities.parking.ai_vision import _get_ai_parking_analysis
from capabilities.parking.classifier import (
    _is_inside_any_geofence,
    classify_parking_location,
    parse_ai_confidence,
)
from capabilities.parking.formatting import (
    _format_parking_alert,
    _send_parking_resolved,
)
from capabilities.parking.maps import (
    _render_parking_map,
    _save_parking_map,
)
from capabilities.alerting.pipeline import (
    AlertSeverity,
    send_alert,
)
from infra import cache as _redis
from infra.bot_registry import get_app_for_account
from infra.services import get_client, get_platform_db, get_tenant_db

logger = logging.getLogger("bot")

# Minimum speed (mph) to consider a vehicle "moving"
_MOVING_SPEED_MPH = 3.0

# Duration thresholds (hours)
_PARKING_WARN_HOURS = 2.0      # WARNING if outside safe zone > 2h
_PARKING_CRITICAL_HOURS = 8.0  # CRITICAL if roadside > 8h
_PARKING_STALE_HOURS = 72.0    # Auto-resolve after 3 days — no longer actionable

# Cooldown: don't re-alert the same vehicle within this window
_PARKING_ALERT_COOLDOWN_S = 4 * 3600  # 4 hours

# N7 — Speed-pattern confirmation: how many consecutive 30-min checks must
# show speed == 0 before we treat the vehicle as truly "stopped".
# First check = 1, so we process on the SECOND consecutive stopped reading.
_PARKING_CONFIRM_CHECKS = 2

# Concurrency caps for the per-account vehicle pass. A typical fleet-cycle
# spends most of its wall-clock time waiting on AI vision (~2–5 s/call) and
# Samsara location rechecks (~300 ms each); these gates parallelize that
# I/O without melting either the LLM endpoint or the Samsara API.
import os as _os
_PARKING_VEHICLE_CONCURRENCY = int(_os.getenv("PARKING_VEHICLE_CONCURRENCY", "10"))
_PARKING_AI_CONCURRENCY = int(_os.getenv("PARKING_AI_CONCURRENCY", "5"))
del _os

# Counter TTL: long enough that a vehicle stopped across 2+ check cycles
# (60+ minutes) is still tracked, but short enough that a vehicle that
# finally moves doesn't keep a stale counter alive forever. Each fresh
# stopped reading bumps the TTL via INCR+EXPIRE.
_STOP_COUNTER_TTL_S = 6 * 3600

# Redis-backed consecutive-stopped counter. Across multiple Gunicorn
# workers this would otherwise be a per-worker in-memory dict, causing
# the _PARKING_CONFIRM_CHECKS gate to fire inconsistently depending on
# which worker handled the cycle. We fall back to a process-local dict
# when Redis is unavailable so single-worker dev still works.
_vehicle_stopped_checks_local: dict[str, int] = {}


async def _stop_counter_incr(chk_key: str) -> int:
    """Atomically increment the consecutive-stopped counter and return
    its new value. Uses Redis INCR+EXPIRE when available so the count is
    coherent across workers; falls back to a process-local dict
    otherwise."""
    if _redis.is_available() and _redis._pool is not None:
        try:
            full_key = f"parking:stopcnt:{chk_key}"
            count = await _redis._pool.incr(full_key)  # type: ignore[misc]
            await _redis._pool.expire(full_key, _STOP_COUNTER_TTL_S)  # type: ignore[misc]
            return int(count)
        except Exception as e:
            logger.debug("parking stop-counter Redis INCR failed (%s): %s", chk_key, e)
    _vehicle_stopped_checks_local[chk_key] = (
        _vehicle_stopped_checks_local.get(chk_key, 0) + 1
    )
    return _vehicle_stopped_checks_local[chk_key]


async def _stop_counter_reset(chk_key: str) -> None:
    """Drop the consecutive-stopped counter for ``chk_key`` (vehicle moved
    or alert was sent). Best-effort; ignores Redis errors."""
    if _redis.is_available() and _redis._pool is not None:
        try:
            await _redis._pool.delete(f"parking:stopcnt:{chk_key}")  # type: ignore[misc]
        except Exception as e:
            logger.debug("parking stop-counter Redis DEL failed (%s): %s", chk_key, e)
    _vehicle_stopped_checks_local.pop(chk_key, None)

# N9 — Breakdown threshold: unknown location parked longer than this without
# AI being able to classify → flag as possible breakdown, not just "unverified".
_PARKING_BREAKDOWN_HOURS = 4.0

# First-run flag: after a bot restart the in-memory dicts are empty and all
# 2-day-old DB cooldowns will have expired.  Skip alert-sending on the very
# first parking check so we can re-evaluate existing events with fresh data
# (correct speed + address) without flooding subscribers.
_first_run = True


async def check_unsafe_parking(app: Application):
    """Scheduled job: detect vehicles parked in unsafe locations.

    Runs every 30 minutes. For each stopped vehicle (speed ≈ 0):
    1. Get actual stopped duration from Samsara engineStates
    2. Check if inside a geofence (always safe)
    3. Classify address via keyword heuristic
    4. For ambiguous ('unknown') locations, ask AI with satellite map on first detection
    5. Send WARNING/CRITICAL alerts based on duration + classification

    Alert thresholds:
    - Inside geofence → always safe, no alert
    - Safe address (truck stop, etc.) → no alert
    - Unknown address > 2h → WARNING
    - Unsafe address (highway/shoulder) > 2h → WARNING
    - Unsafe address > 8h → CRITICAL
    - Unknown > 4h without AI classification → BREAKDOWN alert
    """
    global _first_run
    suppress_alerts = _first_run
    if _first_run:
        _first_run = False
        logger.info(
            "Parking check: first run after restart — updating data "
            "without sending alerts to avoid flood"
        )
    import time as _time
    from infra import observability as _obs
    job_t0 = _time.perf_counter()
    try:
        accounts = await get_platform_db().list_accounts()
        if not accounts:
            logger.warning("Parking check: no accounts found")
            return
        for account in accounts:
            acct_timings: dict[str, float] = {}
            acct_t0 = _time.perf_counter()
            bot_app = get_app_for_account(account.id)
            if not bot_app:
                logger.debug("No bot for account %d — skipping parking check", account.id)
                continue
            try:
                tenant = await get_tenant_db(account.id)
                companies = await tenant.get_account_companies(account.id)
            except Exception:
                logger.error("Parking check: setup failed for acct %s",
                             account.id, exc_info=True)
                continue
            if not companies:
                logger.debug("Parking check: no companies for acct %s", account.id)
                continue

            try:
                samsara = await get_client(account.id)
            except Exception as e:
                logger.warning("Parking check: get_client failed for acct %s: %s", account.id, e)
                continue

            try:
                from capabilities.vehicles.service import get_fleet_overview as _svc_fleet
                with _obs.time_block(acct_timings, "samsara_fetch"):
                    vehicles, engine_data = await asyncio.gather(
                        _svc_fleet(account.id),
                        samsara.get_engine_states(),
                    )
            except Exception as e:
                logger.warning("Parking check API error for acct %s: %s", account.id, e)
                continue

            if not vehicles:
                logger.debug("Parking check: no vehicles for acct %s", account.id)
                continue

            # Geofences are optional — many Samsara plans don't include them.
            # Fetch separately so a 404 doesn't crash the entire check.
            try:
                from capabilities.geofencing.service import get_geofences as _svc_geofences
                geofences = await _svc_geofences(account.id)
            except Exception:
                geofences = []

            logger.info("Parking check: acct %s — %d vehicles, %d geofences",
                        account.id, len(vehicles), len(geofences or []))

            geofences = geofences or []
            now = datetime.now(timezone.utc)
            now_str = now.isoformat()

            # Build engine-state lookup: vehicle_id → {value, time}
            # engineStates shows when the vehicle entered its current state
            # (Off/Idle/On). If state is "Off" and time is old → stopped since then.
            engine_by_id: dict[str, dict] = {}
            for ev in (engine_data or []):
                vid = ev.get("id", "")
                states = ev.get("engineStates", [])
                if states:
                    # Latest state entry (sorted by time, last = most recent)
                    latest = states[-1] if isinstance(states, list) else states
                    if isinstance(latest, dict):
                        engine_by_id[vid] = latest

            # ── Pre-fetch per-account state ONCE ───────────────────────
            # Replaces V × per-vehicle queries inside the loop:
            #   * active parking events  (was: 1 query per vehicle)
            #   * maintenance suppression (was: 1 query per vehicle)
            #   * parking subscribers     (was: 1 query per ALERTING vehicle)
            vehicle_ids = [v.get("id", "") for v in vehicles if v.get("id")]
            with _obs.time_block(acct_timings, "prefetch"):
                try:
                    active_events = await tenant.get_active_parking_events_for_vehicles(
                        account.id, vehicle_ids,
                    )
                except Exception as e:
                    logger.warning("Parking pre-fetch (active events) failed acct=%s: %s",
                                   account.id, e)
                    active_events = {}
                try:
                    suppressed_names = await tenant.get_vehicles_in_maintenance(account.id)
                except Exception as e:
                    logger.debug("Parking pre-fetch (suppression) failed acct=%s: %s",
                                 account.id, e)
                    suppressed_names = set()
                try:
                    all_park_subs = await get_platform_db().get_all_typed_subscribers("parking")
                    acct_subs = [s for s in all_park_subs if s.account_id == account.id]
                except Exception as e:
                    logger.debug("Parking pre-fetch (subscribers) failed acct=%s: %s",
                                 account.id, e)
                    acct_subs = []

            ai_sem = asyncio.Semaphore(_PARKING_AI_CONCURRENCY)
            veh_sem = asyncio.Semaphore(_PARKING_VEHICLE_CONCURRENCY)

            async def _ai_call(vname, address, lat, lng, duration_h):
                async with ai_sem:
                    return await _get_ai_parking_analysis(
                        vname, address, lat, lng, duration_h,
                    )

            async def _process_vehicle(v):
              async with veh_sem:
               try:
                vid = v.get("id", "")
                vname = v.get("name", "?")
                co = v.get("_org", v.get("company_code", "?"))
                loc = v.get("location", {})
                lat = loc.get("latitude")
                lng = loc.get("longitude")
                speed = loc.get("speedMilesPerHour", 0) or 0
                address = loc.get("address", "") or ""

                if lat is None or lng is None:
                    return

                # Maintenance suppression — pre-fetched dict lookup
                # replaces the per-vehicle is_vehicle_in_maintenance query.
                if vname in suppressed_names:
                    return

                chk_key = f"{account.id}:{vid}"

                # Vehicle is moving → resolve any active parking event
                if speed > _MOVING_SPEED_MPH:
                    # N7 — reset consecutive-stopped counter
                    await _stop_counter_reset(chk_key)
                    existing = active_events.get(vid)
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                        # Send resolved notification if it was alerted
                        if existing.get("alert_level") in ("warning", "critical", "breakdown"):
                            await _send_parking_resolved(
                                bot_app, account.id, vname, co, existing,
                            )
                    return

                # ── Look up existing DB record before N7 gate ──
                # Must come first so N7 can be bypassed for already-tracked stops
                # (e.g. after a bot restart where the in-memory counter reset).
                existing = active_events.get(vid)

                # N7 — Speed-pattern confirmation: require _PARKING_CONFIRM_CHECKS
                # consecutive zero-speed polls before treating as a real stop.
                # This prevents a brief traffic-light slowdown from creating events.
                # Bypass if already in DB — stop was confirmed before restart.
                if existing is None:
                    confirm_count = await _stop_counter_incr(chk_key)
                    if confirm_count < _PARKING_CONFIRM_CHECKS:
                        return  # not yet confirmed stopped

                # ── Vehicle is confirmed stopped — calculate real duration ──
                # Determine when the vehicle actually stopped:
                # Priority 1: Samsara engineState timestamp (most accurate)
                # Priority 2: Existing DB record's first_stopped
                # Priority 3: Current time (first detection)
                engine_info = engine_by_id.get(vid, {})
                engine_state = engine_info.get("value", "")
                engine_time = engine_info.get("time", "")

                first_stopped_str = now_str
                if engine_state == "Off" and engine_time:
                    # Samsara reports exactly when the engine turned Off.
                    first_stopped_str = engine_time
                elif engine_state == "Idle" and engine_time and speed <= _MOVING_SPEED_MPH:
                    # Vehicle is idling but not moving (e.g. climate control
                    # on a highway shoulder). Use the Idle timestamp ONLY if
                    # we don't already have an earlier DB record — the Idle
                    # state may have started after a long engine-Off period.
                    if not existing:
                        first_stopped_str = engine_time
                    else:
                        first_stopped_str = existing["first_stopped"]
                elif existing:
                    first_stopped_str = existing["first_stopped"]

                # If we already have a DB record with an earlier stop time, keep it
                if existing and existing.get("first_stopped"):
                    try:
                        db_first = datetime.fromisoformat(
                            existing["first_stopped"],
                        )
                        eng_first = datetime.fromisoformat(
                            first_stopped_str.replace("Z", "+00:00"),
                        )
                        # Use the earlier of the two (most accurate start)
                        if db_first < eng_first:
                            first_stopped_str = existing["first_stopped"]
                    except (ValueError, TypeError):
                        pass

                # Calculate duration from first stop
                try:
                    first_dt = datetime.fromisoformat(
                        first_stopped_str.replace("Z", "+00:00"),
                    )
                    duration_h = max(
                        (now - first_dt).total_seconds() / 3600, 0,
                    )
                except (ValueError, TypeError):
                    duration_h = existing.get("duration_hours", 0) + 0.5 if existing else 0

                # Staleness guard: auto-resolve events older than _PARKING_STALE_HOURS.
                # After 3 days a "parked in unsafe location" alert is no longer
                # actionable — it's either a yard truck (false positive) or an
                # abandoned / impounded vehicle (different workflow entirely).
                if duration_h >= _PARKING_STALE_HOURS:
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                        logger.info(
                            "Parking auto-resolved (stale): %s — %.0fh",
                            vname, duration_h,
                        )
                    await _stop_counter_reset(chk_key)
                    return

                # Check geofence
                in_geofence = _is_inside_any_geofence(lat, lng, geofences)

                # Classify address via keywords (provisional — AI may override)
                keyword_class = "geofence" if in_geofence else classify_parking_location(address)

                # M1 — Skip geofence stops entirely (always safe, no AI needed)
                if keyword_class == "geofence":
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                    return

                # M1b — Safe keywords (truck stop, pilot, etc.) are very
                # reliable → skip without AI.
                if keyword_class == "safe":
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                    return

                # ── AI Vision is the primary authority for unsafe/unknown ──
                # Address keywords like "I 90" often appear even when the
                # truck is at a rest stop or truck parking area right off
                # the highway. The AI sees the actual satellite imagery
                # and can distinguish highway shoulder from a parking lot.
                ai_analysis = existing.get("ai_analysis", "") if existing else ""
                map_image_path = existing.get("map_image_path", "") if existing else ""
                loc_class = keyword_class  # start with keyword, AI may override

                if not ai_analysis:
                    # Run AI vision on first detection for ALL non-safe stops.
                    # Throttled by ai_sem so a 50-vehicle burst doesn't fan out
                    # into 50 simultaneous LLM calls.
                    ai_result = await _ai_call(
                        vname, address, lat, lng, duration_h,
                    )
                    if ai_result:
                        ai_analysis = ai_result
                        ai_lower = ai_analysis.lower()
                        confidence = parse_ai_confidence(ai_analysis)

                        if "unsafe" not in ai_lower and "safe" in ai_lower:
                            # AI says SAFE — trust it over keyword regex
                            if confidence in ("HIGH", "MEDIUM"):
                                # Resolve and skip — it's a truck stop/yard
                                if existing:
                                    await tenant.resolve_parking_event(account.id, vid)
                                return
                            # LOW confidence safe → keep as unknown for monitoring
                            loc_class = "unknown"
                        elif "unsafe" in ai_lower:
                            loc_class = "unsafe"
                        else:
                            loc_class = "unknown"

                    # Save map image to disk for dashboard display
                    if not map_image_path:
                        saved_path = await _save_parking_map(
                            account.id, vid, lat, lng,
                        )
                        if saved_path:
                            map_image_path = saved_path

                # Upsert the parking event (only unsafe/unknown reach here)
                event = await tenant.upsert_parking_event(
                    account_id=account.id,
                    vehicle_id=vid,
                    vehicle_name=vname,
                    company_code=co,
                    latitude=lat,
                    longitude=lng,
                    address=address,
                    first_stopped=first_stopped_str,
                    duration_hours=duration_h,
                    location_class=loc_class,
                )

                # Determine alert level
                prev_alert = existing.get("alert_level", "none") if existing else "none"
                new_alert = "none"

                if loc_class == "unsafe":
                    if duration_h >= _PARKING_CRITICAL_HOURS:
                        new_alert = "critical"
                    elif duration_h >= _PARKING_WARN_HOURS:
                        new_alert = "warning"
                else:
                    # N9 — Unknown location: detect possible breakdown.
                    # If the AI couldn't produce a definitive SAFE/UNSAFE
                    # classification and the vehicle has been stopped longer
                    # than the breakdown threshold, escalate.
                    ai_has_classification = (
                        ai_analysis
                        and ("safe" in ai_analysis.lower()
                             or "unsafe" in ai_analysis.lower())
                    )
                    if duration_h >= _PARKING_BREAKDOWN_HOURS and not ai_has_classification:
                        new_alert = "breakdown"
                    elif duration_h >= _PARKING_CRITICAL_HOURS:
                        new_alert = "critical"
                    elif duration_h >= _PARKING_WARN_HOURS:
                        new_alert = "warning"

                # Update DB with alert level and AI analysis
                await tenant.update_parking_alert_level(
                    event["id"], new_alert, ai_analysis,
                    map_image_path=map_image_path,
                )

                if new_alert == "none":
                    return

                # First run after restart: update DB state only — do NOT
                # send alerts.  The next scheduled check (30 min later)
                # will have correct in-memory state and meaningful DB
                # cooldowns, so it can alert normally.
                if suppress_alerts:
                    return

                # M4 — Cooldown from DB, not from in-memory dict.
                # In-memory dict resets on every bot restart causing alert
                # spam for all 57 vehicles. Instead check the DB alert_level:
                # if the stored level equals new_alert, we already sent it.
                # Use `existing` (the DB snapshot from before THIS upsert),
                # not `event` (which has last_checked = now).
                if existing and prev_alert == new_alert:
                    try:
                        last_checked_str = existing.get("last_checked", "")
                        if last_checked_str:
                            last_checked_dt = datetime.fromisoformat(
                                last_checked_str.replace("Z", "+00:00"),
                            )
                            seconds_since = (now - last_checked_dt).total_seconds()
                            if seconds_since < _PARKING_ALERT_COOLDOWN_S:
                                # Same level already alerted recently — only
                                # allow escalation from warning/breakdown → critical
                                if not (new_alert == "critical" and prev_alert != "critical"):
                                    return
                    except (ValueError, TypeError):
                        pass

                # ── Real-time speed recheck before sending alert ──
                # The fleet_overview data may be up to 2 min stale (cached).
                # Fetch fresh GPS for this specific vehicle to confirm it is
                # still stopped. If the truck has moved, resolve instead of
                # sending a false alert.
                try:
                    fresh_loc = await samsara.get_vehicle_location(vid, company=co)
                    if fresh_loc:
                        fresh_speed = fresh_loc.get("speedMilesPerHour", 0) or 0
                        if fresh_speed > _MOVING_SPEED_MPH:
                            logger.info(
                                "Parking recheck: %s now at %.1f MPH — "
                                "skipping alert, resolving event",
                                vname, fresh_speed,
                            )
                            await _stop_counter_reset(chk_key)
                            await tenant.resolve_parking_event(account.id, vid)
                            if prev_alert in ("warning", "critical", "breakdown"):
                                await _send_parking_resolved(
                                    bot_app, account.id, vname, co,
                                    existing or event,
                                )
                            return
                except Exception as e:
                    logger.debug("Parking recheck failed for %s: %s", vname, e)

                # Send parking alert
                is_breakdown = new_alert == "breakdown"
                severity = (AlertSeverity.CRITICAL if new_alert == "critical"
                            else AlertSeverity.WARNING)

                # Subscribers were pre-fetched for the whole account — skip
                # if no one is subscribed to parking alerts here.
                if not acct_subs:
                    return

                # Render map image to attach to alert
                map_bytes = await asyncio.to_thread(
                    _render_parking_map, lat, lng,
                )

                alert_text = _format_parking_alert(
                    vname, address, lat, lng, duration_h,
                    loc_class, ai_analysis, severity,
                    is_breakdown=is_breakdown,
                )

                vehicle_dict = {"id": vid, "name": vname, "_org": co}
                await send_alert(
                    app,
                    account_id=account.id,
                    alert_type="parking",
                    severity=severity,
                    vehicle=vehicle_dict,
                    alert_text=alert_text,
                    subscribers=acct_subs,
                    co=co,
                    alert_key_detail=f"parking:{loc_class}:{duration_h:.0f}h",
                    photo_bytes=map_bytes,
                    bot_app=bot_app,
                )

               except Exception as e:
                logger.warning(
                    "Parking check vehicle %s error: %s",
                    v.get("name", "?"), e,
                )

            # Run the per-vehicle work in parallel within the account.
            # The Semaphore inside _process_vehicle bounds concurrency;
            # gather captures any unexpected exceptions so a single bad
            # vehicle doesn't sink the rest of the cycle.
            with _obs.time_block(acct_timings, "vehicle_pass"):
                await asyncio.gather(
                    *(_process_vehicle(v) for v in vehicles),
                    return_exceptions=True,
                )
            acct_timings["total"] = round(
                (_time.perf_counter() - acct_t0) * 1000, 1,
            )
            logger.info(
                "Parking acct=%s vehicles=%d timings_ms=%s",
                account.id, len(vehicles), acct_timings,
            )

        logger.info(
            "Parking job total_ms=%s",
            round((_time.perf_counter() - job_t0) * 1000, 1),
        )

    except Exception as e:
        logger.error(f"Unsafe parking check error: {e}")
