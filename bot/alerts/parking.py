"""Unsafe parking detection — geofence, keyword, and AI-vision analysis."""

from __future__ import annotations

import re as _re
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from database import Role
from bot.config import db, logger, get_client
from bot.helpers import escape_html
from bot.alerts.pipeline import (
    AlertSeverity, SYSTEM_USER_ID,
    send_alert, is_vehicle_suppressed,
)


# Keywords that indicate a SAFE parking location (case-insensitive match)
_SAFE_KEYWORDS = [
    "truck stop", "truckstop", "rest area", "rest stop",
    "pilot", "flying j", "love's", "loves", "petro",
    "warehouse", "terminal", "depot", "yard", "dock",
    "distribution", "logistics", "parking lot", "parking area",
    "travel center", "travel plaza", "service plaza",
    "weigh station", "scales",
    "walmart", "costco", "home depot",  # common overnight lots
    "industrial", "commerce",
]

# Regex patterns for safe keywords that need word-boundary matching
_SAFE_REGEX = [
    _re.compile(r"\bta\b", _re.IGNORECASE),        # TA travel centers
    _re.compile(r"\bta-", _re.IGNORECASE),          # TA-Petro
]

# Keywords that indicate an UNSAFE parking location
_UNSAFE_KEYWORDS = [
    "highway", "interstate", "freeway", "beltway", "turnpike",
    "expressway", "parkway", "bypass",
    "shoulder", "ramp", "exit ramp", "on-ramp", "off-ramp",
    "overpass", "underpass", "bridge", "tunnel",
    "median", "roadside", "roadway",
    "interchange", "junction",
]

# Regex patterns for unsafe keywords that need word-boundary matching
_UNSAFE_REGEX = [
    _re.compile(r"\bi-\d", _re.IGNORECASE),         # I-95, I-10, etc.
]

# Minimum speed (mph) to consider a vehicle "moving"
_MOVING_SPEED_MPH = 3.0

# Duration thresholds (hours)
_PARKING_WARN_HOURS = 2.0      # WARNING if outside safe zone > 2h
_PARKING_CRITICAL_HOURS = 8.0  # CRITICAL if roadside > 8h
_PARKING_LONG_HOURS = 24.0     # WARNING if anywhere outside geofence > 24h

# Cooldown: don't re-alert the same vehicle within this window
_PARKING_ALERT_COOLDOWN_S = 4 * 3600  # 4 hours
_parking_alert_sent: dict[str, float] = {}  # "acctID:vid" → timestamp

# N7 — Speed-pattern confirmation: how many consecutive 30-min checks must
# show speed == 0 before we treat the vehicle as truly "stopped".
# First check = 1, so we process on the SECOND consecutive stopped reading.
_PARKING_CONFIRM_CHECKS = 2
_vehicle_stopped_checks: dict[str, int] = {}  # "acctID:vid" → consecutive count

# N9 — Breakdown threshold: unknown location parked longer than this without
# AI being able to classify → flag as possible breakdown, not just "unverified".
_PARKING_BREAKDOWN_HOURS = 4.0


def classify_parking_location(address: str) -> str:
    """Classify an address as 'safe', 'unsafe', or 'unknown'.

    Uses keyword scoring on the Samsara reverse-geocoded address.
    Both safe and unsafe keywords are checked, and the side with more
    matches wins.  This avoids false positives like
    "Pilot Travel Center, I-95, Exit 42" being classified as unsafe
    just because "I-95" appears in the address alongside "Pilot".
    """
    if not address:
        return "unknown"
    addr_lower = address.lower()

    safe_score = 0
    unsafe_score = 0

    for keyword in _SAFE_KEYWORDS:
        if _re.search(r"\b" + _re.escape(keyword) + r"\b", addr_lower):
            safe_score += 1
    for pattern in _SAFE_REGEX:
        if pattern.search(address):
            safe_score += 1

    for keyword in _UNSAFE_KEYWORDS:
        if _re.search(r"\b" + _re.escape(keyword) + r"\b", addr_lower):
            unsafe_score += 1
    for pattern in _UNSAFE_REGEX:
        if pattern.search(address):
            unsafe_score += 1

    if safe_score == 0 and unsafe_score == 0:
        return "unknown"
    if safe_score > 0 and unsafe_score == 0:
        return "safe"
    if unsafe_score > 0 and safe_score == 0:
        return "unsafe"
    # Both matched — safe POI names (truck stop, pilot, etc.) outweigh
    # generic road names that often appear in the same address line.
    if safe_score >= unsafe_score:
        return "safe"
    return "unsafe"


def get_parking_classification_reason(
    address: str, loc_class: str, ai_analysis: str = "",
) -> str:
    """Return a short explanation of why a parking event was classified."""
    addr_lower = (address or "").lower()
    if loc_class == "geofence":
        return "Inside a designated geofence"
    if loc_class == "safe":
        for kw in _SAFE_KEYWORDS:
            if kw in addr_lower:
                return f"Safe area — matched \"{kw}\""
        return "Classified as safe parking area"
    if loc_class == "unsafe":
        for kw in _UNSAFE_KEYWORDS:
            if kw in addr_lower:
                return f"Hazard keyword — matched \"{kw}\""
        for pat in _UNSAFE_REGEX:
            m = pat.search(address or "")
            if m:
                return f"Hazard pattern — matched \"{m.group()}\""
        if ai_analysis and "unsafe" in ai_analysis.lower():
            return "AI vision analysis — confirmed unsafe"
        return "Roadside / highway location"
    # unknown
    if ai_analysis:
        return "AI analysis inconclusive — manual review advised"
    return "Location unverified — AI review pending"


def _is_inside_any_geofence(
    lat: float, lng: float, geofences: list[dict],
) -> bool:
    """Check if coordinates fall inside any geofence."""
    from bot.geofences import _is_inside_geofence
    for gf in geofences:
        if _is_inside_geofence(lat, lng, gf):
            return True
    return False


def parse_ai_confidence(ai_text: str) -> str:
    """Extract the CONFIDENCE level from a structured AI response.

    The AI is instructed to reply with:
      CLASSIFICATION: SAFE or UNSAFE
      CONFIDENCE: HIGH, MEDIUM, or LOW
      REASON: ...

    Returns 'HIGH', 'MEDIUM', 'LOW', or '' if not found.
    """
    for line in ai_text.splitlines():
        if line.strip().upper().startswith("CONFIDENCE"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                val = parts[1].strip().upper()
                for level in ("HIGH", "MEDIUM", "LOW"):
                    if level in val:
                        return level
    return ""


def _render_parking_map(lat: float, lng: float) -> bytes | None:
    """Render satellite + road map side-by-side for AI vision analysis.

    Left panel:  Satellite imagery (ESRI World Imagery, zoom 17, ~125 m)
    Right panel:  Labeled road map (OpenStreetMap, zoom 15, ~500 m)
    Red marker shows vehicle position on both panels.

    Returns PNG bytes or None on failure.
    """
    try:
        from staticmap import StaticMap, CircleMarker
        from PIL import Image as PILImage
        import io

        pw, ph = 400, 400

        # Satellite panel — close-up terrain / parking lot detail
        sat = StaticMap(
            pw, ph,
            url_template=(
                "https://server.arcgisonline.com/ArcGIS/rest/services/"
                "World_Imagery/MapServer/tile/{z}/{y}/{x}"
            ),
        )
        sat.add_marker(CircleMarker((lng, lat), "#ff0000", 12))
        sat_img = sat.render(zoom=17, center=[lng, lat])

        # Road map panel — wider area with road names & POI labels
        road = StaticMap(
            pw, ph,
            url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        )
        road.add_marker(CircleMarker((lng, lat), "#ff0000", 12))
        road_img = road.render(zoom=15, center=[lng, lat])

        # Combine side-by-side
        combined = PILImage.new("RGB", (pw * 2, ph))
        combined.paste(sat_img, (0, 0))
        combined.paste(road_img, (pw, 0))

        buf = io.BytesIO()
        combined.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.debug("Failed to render parking map: %s", e)
        return None


async def _get_ai_parking_analysis(
    vehicle_name: str, address: str, lat: float, lng: float,
    duration_hours: float,
) -> str:
    """Analyse a parking location using satellite + road map imagery.

    Renders a side-by-side map screenshot at the vehicle's coordinates
    and sends it to a Gemini vision model so the AI can *see* the actual
    terrain — highway shoulder vs parking lot, ramp vs truck stop, etc.

    Falls back to text-only analysis when maps or vision are unavailable.
    """
    try:
        import ai
        if not ai.is_configured():
            return ""

        # Render map screenshot (runs blocking I/O in a thread)
        import asyncio
        map_bytes = await asyncio.to_thread(_render_parking_map, lat, lng)

        if map_bytes:
            # ── Vision-enhanced analysis (satellite + road map) ──
            prompt = (
                f"A commercial truck (#{vehicle_name}) has been parked/stopped "
                f"for {duration_hours:.1f} hours at this location.\n\n"
                f"Address (GPS reverse-geocode, may be inaccurate): {address}\n"
                f"Coordinates: {lat:.6f}, {lng:.6f}\n\n"
                "The image contains TWO map panels of the vehicle's position:\n"
                "• LEFT — Satellite / aerial imagery (zoom 17, ~125 m)\n"
                "• RIGHT — Road map with labels (zoom 15, ~500 m)\n"
                "The RED DOT marks the truck's exact position.\n\n"
                "Analyze BOTH panels carefully:\n"
                "1. Is the truck in a designated safe area? "
                "(truck stop, rest area, warehouse yard, terminal, parking lot, "
                "distribution center, fuel station)\n"
                "2. Or is it on a highway shoulder, ramp, interchange, median, "
                "roadside, bridge, or other dangerous location?\n"
                "3. Look at: paved surfaces, building proximity, road lane "
                "markings, highway vs local road, commercial facilities.\n\n"
                "Reply in EXACTLY this format:\n"
                "CLASSIFICATION: SAFE or UNSAFE\n"
                "CONFIDENCE: HIGH, MEDIUM, or LOW\n"
                "REASON: 1-2 sentences describing what you see."
            )

            response = await ai.generate_with_vision(
                prompt,
                map_bytes,
                system=(
                    "You are a fleet safety analyst specializing in commercial "
                    "truck parking safety. Analyze map imagery to determine "
                    "whether a parking location is safe for an extended stop."
                ),
            )
        else:
            # ── Fallback: text-only analysis (map render failed) ──
            prompt = (
                f"A commercial truck (#{vehicle_name}) has been parked/stopped "
                f"for {duration_hours:.1f} hours at this location:\n"
                f"Address: {address}\n"
                f"Coordinates: {lat:.6f}, {lng:.6f}\n\n"
                "Is this a safe or unsafe parking location for a commercial "
                "truck?  Consider: Is it a designated truck stop, rest area, "
                "warehouse, or terminal?  Or is it roadside, highway shoulder, "
                "interchange, or residential area?\n"
                "Reply in 2-3 sentences: classify as SAFE or UNSAFE and explain."
            )

            response = await ai.generate(
                prompt,
                system="You are a fleet safety analyst. Be concise and factual.",
            )

        # Track usage
        usage = ai.get_last_usage()
        if usage:
            try:
                from bot.config import db as _db
                await _db.log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai.get_current_model_name(),
                    request_type="parking_analysis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception:
                pass

        if response:
            from bot.alerts.ai_maintenance import _truncate_at_sentence, _is_valid_ai_response
            if _is_valid_ai_response(response):
                return escape_html(_truncate_at_sentence(response, 800))
    except Exception as e:
        logger.debug(f"AI parking analysis failed: {e}")
    return ""


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
    try:
        accounts = await db.list_accounts()
        if not accounts:
            logger.warning("Parking check: no accounts found")
            return
        for account in accounts:
            companies = await db.get_account_companies(account.id)
            if not companies:
                logger.debug("Parking check: no companies for acct %s", account.id)
                continue

            try:
                samsara = await get_client(account.id)
            except Exception as e:
                logger.warning("Parking check: get_client failed for acct %s: %s", account.id, e)
                continue

            try:
                import asyncio as _aio
                vehicles, engine_data = await _aio.gather(
                    samsara.get_fleet_overview(),
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
                geofences = await samsara.get_geofences()
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

            for v in vehicles:
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
                    continue

                if await is_vehicle_suppressed(account.id, vname):
                    continue

                chk_key = f"{account.id}:{vid}"

                # Vehicle is moving → resolve any active parking event
                if speed > _MOVING_SPEED_MPH:
                    # N7 — reset consecutive-stopped counter
                    _vehicle_stopped_checks.pop(chk_key, None)
                    existing = await db.get_active_parking_event(account.id, vid)
                    if existing:
                        await db.resolve_parking_event(account.id, vid)
                        # Send resolved notification if it was alerted
                        if existing.get("alert_level") in ("warning", "critical", "breakdown"):
                            await _send_parking_resolved(
                                app, account.id, vname, co, existing,
                            )
                    continue

                # ── Look up existing DB record before N7 gate ──
                # Must come first so N7 can be bypassed for already-tracked stops
                # (e.g. after a bot restart where the in-memory counter reset).
                existing = await db.get_active_parking_event(account.id, vid)

                # N7 — Speed-pattern confirmation: require _PARKING_CONFIRM_CHECKS
                # consecutive zero-speed polls before treating as a real stop.
                # This prevents a brief traffic-light slowdown from creating events.
                # Bypass if already in DB — stop was confirmed before restart.
                if existing is None:
                    _vehicle_stopped_checks[chk_key] = (
                        _vehicle_stopped_checks.get(chk_key, 0) + 1
                    )
                    if _vehicle_stopped_checks[chk_key] < _PARKING_CONFIRM_CHECKS:
                        continue  # not yet confirmed stopped

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

                # Check geofence
                in_geofence = _is_inside_any_geofence(lat, lng, geofences)

                # Classify address
                loc_class = "geofence" if in_geofence else classify_parking_location(address)

                # M1 — Skip safe/geofence stops entirely: they do not need
                # DB rows or alerts. (Geofences = always safe; safe-keyword
                # address = truck stop / yard / terminal.)
                if loc_class in ("geofence", "safe"):
                    # If there was a stale DB row from a previous session
                    # that is now inside a geofence/safe zone, resolve it.
                    if existing:
                        await db.resolve_parking_event(account.id, vid)
                    continue

                # M2 — AI on first detection (not waiting 2h).
                # Run once per event for any unknown location the moment we
                # first see the vehicle stopped and outside a safe zone.
                ai_analysis = existing.get("ai_analysis", "") if existing else ""
                if loc_class == "unknown" and not ai_analysis:
                    ai_analysis = await _get_ai_parking_analysis(
                        vname, address, lat, lng, duration_h,
                    )
                    if ai_analysis:
                        # Re-classify based on AI response
                        ai_lower = ai_analysis.lower()
                        if "safe" in ai_lower and "unsafe" not in ai_lower:
                            # AI says it's safe — skip entirely (M1)
                            continue
                        elif "unsafe" in ai_lower:
                            loc_class = "unsafe"

                # Upsert the parking event (only unsafe/unknown reach here)
                event = await db.upsert_parking_event(
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
                await db.update_parking_alert_level(
                    event["id"], new_alert, ai_analysis,
                )

                if new_alert == "none":
                    continue

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
                                    continue
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
                            _vehicle_stopped_checks.pop(chk_key, None)
                            await db.resolve_parking_event(account.id, vid)
                            if prev_alert in ("warning", "critical", "breakdown"):
                                await _send_parking_resolved(
                                    app, account.id, vname, co,
                                    existing or event,
                                )
                            continue
                except Exception as e:
                    logger.debug("Parking recheck failed for %s: %s", vname, e)

                # Send parking alert
                is_breakdown = new_alert == "breakdown"
                severity = (AlertSeverity.CRITICAL if new_alert == "critical"
                            else AlertSeverity.WARNING)

                subscribers = await db.get_all_typed_subscribers("parking")
                acct_subs = [s for s in subscribers if s.account_id == account.id]
                if not acct_subs:
                    continue

                # Render map image to attach to alert
                import asyncio as _aio_map
                map_bytes = await _aio_map.to_thread(
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
                )

                # Keep in-memory dict as secondary guard within same process run
                _parking_alert_sent[f"{account.id}:{vid}"] = now.timestamp()

              except Exception as e:
                logger.warning("Parking check vehicle %s error: %s", v.get("name", "?"), e)
                continue

    except Exception as e:
        logger.error(f"Unsafe parking check error: {e}")


def _format_parking_alert(
    vname: str, address: str, lat: float, lng: float,
    duration_h: float, loc_class: str, ai_analysis: str,
    severity: AlertSeverity,
    is_breakdown: bool = False,
) -> str:
    """Format the parking alert message."""
    sep = "━━━━━━━━━━━━━━━━━━━"

    if is_breakdown:
        icon = "🆘"
        level = "POSSIBLE BREAKDOWN"
    elif severity == AlertSeverity.CRITICAL:
        icon = "🚨"
        level = "CRITICAL"
    else:
        icon = "⚠️"
        level = "WARNING"

    # Duration formatting
    if duration_h >= 24:
        dur_str = f"{duration_h / 24:.1f} days"
    else:
        dur_str = f"{duration_h:.1f}h"

    # Location class label
    class_labels = {
        "unsafe": "🔴 Roadside / Highway",
        "unknown": "🟡 Unverified Location",
        "safe": "🟢 Designated Parking",
        "geofence": "🟢 Inside Geofence",
    }
    class_label = class_labels.get(loc_class, "🟡 Unknown")

    # Google Maps link
    maps_url = f"https://maps.google.com/?q={lat},{lng}"

    title = "🆘  POSSIBLE BREAKDOWN" if is_breakdown else f"{icon}  UNSAFE PARKING — {level}"
    text = (
        f"{sep}\n"
        f"  {title}\n"
        f"{sep}\n"
        f"\n  🚛 Truck: <b>#{vname}</b>\n"
        f"\n  📍 <b>{lat:.5f}°, {lng:.5f}°</b>\n"
    )
    if address:
        text += f"  🏷 {address}\n"
    text += (
        f"  {class_label}\n"
        f"\n  🕐 Stopped for: <b>{dur_str}</b>\n"
        f"\n  🗺 <a href='{maps_url}'>View on Map</a>\n"
    )

    if ai_analysis:
        text += f"\n  🤖 <b>AI Analysis:</b>\n  {ai_analysis}\n"

    if is_breakdown:
        text += (
            "\n  🆘 <b>No AI classification possible.</b>\n"
            "  Vehicle may be disabled or have a\n"
            "  mechanical issue. Contact driver.\n"
        )
    elif severity == AlertSeverity.CRITICAL:
        text += (
            "\n  ❗ <b>Immediate attention required</b>\n"
            "  Vehicle has been parked in an unsafe\n"
            "  location for an extended period.\n"
        )

    return text


async def _send_parking_resolved(
    app: Application,
    account_id: int,
    vname: str,
    co: str,
    event: dict,
):
    """Send notification that a parking event has been resolved (vehicle moved)."""
    duration_h = event.get("duration_hours", 0)
    address = event.get("address", "Unknown")
    loc_class = event.get("location_class", "unknown")

    if duration_h >= 24:
        dur_str = f"{duration_h / 24:.1f} days"
    else:
        dur_str = f"{duration_h:.1f}h"

    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ✅  <b>PARKING RESOLVED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"\n  🚛 Truck: <b>#{vname}</b>\n"
        f"  📍 Was at: {address}\n"
        f"  🕐 Parked for: <b>{dur_str}</b>\n"
        f"\n  Vehicle is now moving.\n"
    )

    subscribers = await db.get_all_typed_subscribers("parking")
    acct_subs = [s for s in subscribers if s.account_id == account_id]

    for sub in acct_subs:
        if sub.role == Role.DRIVER and sub.truck_num:
            if vname.lower() != sub.truck_num.lower():
                continue
        # Respect DND / quiet hours for resolved notifications
        if sub.is_in_quiet_hours():
            await db.queue_dnd_alert(
                account_id=account_id,
                telegram_id=sub.telegram_id,
                alert_type="parking",
                vehicle_name=vname,
                alert_text=text,
            )
            continue
        try:
            await app.bot.send_message(
                chat_id=sub.telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"📋 View Truck #{vname}",
                        callback_data=f"cotruck_{co}_{vname}",
                    )],
                    [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
                ]),
            )
        except Exception as e:
            logger.debug(f"Parking resolved notification to {sub.telegram_id}: {e}")
