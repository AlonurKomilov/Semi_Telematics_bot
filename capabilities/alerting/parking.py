"""Unsafe parking detection — geofence, keyword, and AI-vision analysis."""

from __future__ import annotations

import logging
import re as _re
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from telegram.constants import ParseMode

from constants import OSM_TILE_URL
from adapters.storage import Role
from capabilities.formatting.helpers import escape_html
from core.services import get_client, get_platform_db, get_tenant_db
from capabilities.alerting.pipeline import (
    AlertSeverity, SYSTEM_USER_ID,
    send_alert, is_vehicle_suppressed,
)
from core.bot_registry import get_app_for_account

logger = logging.getLogger("bot")


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
    _re.compile(r"\bI[\s-]\d", _re.IGNORECASE),     # I-95, I 70 (interstate)
    _re.compile(r"\bUS[\s-]\d", _re.IGNORECASE),     # US-40, US 54
    _re.compile(r"\bSR[\s-]\d", _re.IGNORECASE),     # SR-99, SR 392
    _re.compile(r"\b[A-Z]{2}\s\d{2,3}\b"),           # NM 392, CA 99, TX 45 (state routes)
]

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
_vehicle_stopped_checks: dict[str, int] = {}  # "acctID:vid" → consecutive count

# N9 — Breakdown threshold: unknown location parked longer than this without
# AI being able to classify → flag as possible breakdown, not just "unverified".
_PARKING_BREAKDOWN_HOURS = 4.0

# First-run flag: after a bot restart the in-memory dicts are empty and all
# 2-day-old DB cooldowns will have expired.  Skip alert-sending on the very
# first parking check so we can re-evaluate existing events with fresh data
# (correct speed + address) without flooding subscribers.
_first_run = True


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
    from capabilities.geofencing.geometry import is_inside_geofence
    for gf in geofences:
        if is_inside_geofence(lat, lng, gf):
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
    """Render satellite-hybrid + road map side-by-side for AI vision analysis.

    Left panel:  Satellite imagery with labels overlay (ESRI Hybrid, zoom 17)
    Right panel:  Labeled road map (OpenStreetMap, zoom 15, ~500 m)
    Red marker shows vehicle position on both panels.

    Uses ESRI World_Imagery as base + Reference_Labels overlay for POI names
    (truck stops, weigh stations, parking areas, etc.) that raw satellite
    imagery alone would not show.

    Returns PNG bytes or None on failure.
    """
    try:
        from staticmap import StaticMap, CircleMarker
        from PIL import Image as PILImage
        import io

        pw, ph = 512, 512

        # Satellite base layer — close-up terrain / parking lot detail
        sat = StaticMap(
            pw, ph,
            url_template=(
                "https://server.arcgisonline.com/ArcGIS/rest/services/"
                "World_Imagery/MapServer/tile/{z}/{y}/{x}"
            ),
        )
        sat.add_marker(CircleMarker((lng, lat), "#ff0000", 12))
        sat_img = sat.render(zoom=18, center=[lng, lat])

        # Labels overlay on satellite — shows road names, POIs, facility names
        try:
            import numpy as np
            labels = StaticMap(
                pw, ph,
                url_template=(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/"
                    "Reference/World_Reference_Overlay/MapServer/tile/{z}/{y}/{x}"
                ),
            )
            labels_img = labels.render(zoom=18, center=[lng, lat])
            # staticmap renders to RGB, losing the tile's alpha channel.
            # The label tiles have a transparent background, but after RGB
            # conversion those pixels become white (255,255,255).  Restore
            # transparency by treating near-white pixels as transparent.
            labels_rgba = labels_img.convert("RGBA")
            arr = np.array(labels_rgba)
            white_mask = (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
            arr[white_mask, 3] = 0  # make white pixels transparent
            labels_rgba = PILImage.fromarray(arr, "RGBA")
            sat_img = sat_img.convert("RGBA")
            sat_img = PILImage.alpha_composite(sat_img, labels_rgba).convert("RGB")
        except Exception as e:
            logger.debug("Labels overlay failed, using raw satellite: %s", e)

        # Road map panel — wider area with road names & POI labels
        road = StaticMap(
            pw, ph,
            url_template=OSM_TILE_URL,
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


async def _save_parking_map(
    account_id: int, vehicle_id: str, lat: float, lng: float,
) -> str:
    """Render and save a parking map image to disk for dashboard display.

    Returns the relative path (from project root) on success, or "" on failure.
    """
    import asyncio, os
    map_bytes = await asyncio.to_thread(_render_parking_map, lat, lng)
    if not map_bytes:
        return ""
    try:
        maps_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "parking_maps",
        )
        os.makedirs(maps_dir, exist_ok=True)
        # Sanitize vehicle_id for filename
        safe_vid = vehicle_id.replace("/", "_").replace("\\", "_")
        fname = f"{account_id}_{safe_vid}.png"
        fpath = os.path.join(maps_dir, fname)
        with open(fpath, "wb") as f:
            f.write(map_bytes)
        return f"data/parking_maps/{fname}"
    except Exception as e:
        logger.debug("Failed to save parking map: %s", e)
        return ""


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
        import capabilities.ai as ai
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
                "• LEFT — Satellite / aerial imagery with labels overlay (zoom 18, ~60 m)\n"
                "  Look for: paved parking surfaces, truck bays, fuel pumps, buildings,\n"
                "  label text showing facility names (truck stops, parking areas, weigh stations)\n"
                "• RIGHT — Road map with labels (zoom 15, ~500 m)\n"
                "  Look for: nearby commercial facilities, truck stops, rest areas,\n"
                "  exit ramps, highway shoulders, interchanges\n"
                "The RED DOT marks the truck's exact position.\n\n"
                "IMPORTANT: The GPS address may just show the nearest road name (e.g. 'I-90')\n"
                "even if the truck is at a truck stop, rest area, or parking lot RIGHT NEXT TO\n"
                "the highway. ALWAYS rely on what you SEE in the imagery over the address text.\n\n"
                "Analyze BOTH panels carefully:\n"
                "1. Is the truck in a designated safe area? "
                "(truck stop, rest area, warehouse yard, terminal, parking lot, "
                "distribution center, fuel station, weigh station)\n"
                "2. Or is it on a highway shoulder, ramp, interchange, median, "
                "roadside, bridge, or other dangerous location?\n"
                "3. Look at: paved surfaces, building proximity, road lane "
                "markings, highway vs local road, commercial facilities, "
                "parking lot striping, truck bays, fuel canopies.\n\n"
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
                await get_platform_db().log_ai_usage(
                    account_id=SYSTEM_USER_ID,
                    user_id=SYSTEM_USER_ID,
                    model=ai.get_current_model_name(),
                    request_type="parking_analysis",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    reply_tokens=usage.get("reply_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            except Exception as e:
                logger.debug("Failed to log AI usage for parking analysis: %s", e)
        from capabilities.alerting.ai_maintenance import _truncate_at_sentence, _is_valid_ai_response
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
    global _first_run
    suppress_alerts = _first_run
    if _first_run:
        _first_run = False
        logger.info(
            "Parking check: first run after restart — updating data "
            "without sending alerts to avoid flood"
        )
    try:
        accounts = await get_platform_db().list_accounts()
        if not accounts:
            logger.warning("Parking check: no accounts found")
            return
        for account in accounts:
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
                    existing = await tenant.get_active_parking_event(account.id, vid)
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                        # Send resolved notification if it was alerted
                        if existing.get("alert_level") in ("warning", "critical", "breakdown"):
                            await _send_parking_resolved(
                                bot_app, account.id, vname, co, existing,
                            )
                    continue

                # ── Look up existing DB record before N7 gate ──
                # Must come first so N7 can be bypassed for already-tracked stops
                # (e.g. after a bot restart where the in-memory counter reset).
                existing = await tenant.get_active_parking_event(account.id, vid)

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
                    _vehicle_stopped_checks.pop(chk_key, None)
                    continue

                # Check geofence
                in_geofence = _is_inside_any_geofence(lat, lng, geofences)

                # Classify address via keywords (provisional — AI may override)
                keyword_class = "geofence" if in_geofence else classify_parking_location(address)

                # M1 — Skip geofence stops entirely (always safe, no AI needed)
                if keyword_class == "geofence":
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                    continue

                # M1b — Safe keywords (truck stop, pilot, etc.) are very
                # reliable → skip without AI.
                if keyword_class == "safe":
                    if existing:
                        await tenant.resolve_parking_event(account.id, vid)
                    continue

                # ── AI Vision is the primary authority for unsafe/unknown ──
                # Address keywords like "I 90" often appear even when the
                # truck is at a rest stop or truck parking area right off
                # the highway. The AI sees the actual satellite imagery
                # and can distinguish highway shoulder from a parking lot.
                ai_analysis = existing.get("ai_analysis", "") if existing else ""
                map_image_path = existing.get("map_image_path", "") if existing else ""
                loc_class = keyword_class  # start with keyword, AI may override

                if not ai_analysis:
                    # Run AI vision on first detection for ALL non-safe stops
                    ai_result = await _get_ai_parking_analysis(
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
                                continue
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
                    continue

                # First run after restart: update DB state only — do NOT
                # send alerts.  The next scheduled check (30 min later)
                # will have correct in-memory state and meaningful DB
                # cooldowns, so it can alert normally.
                if suppress_alerts:
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
                            await tenant.resolve_parking_event(account.id, vid)
                            if prev_alert in ("warning", "critical", "breakdown"):
                                await _send_parking_resolved(
                                    bot_app, account.id, vname, co,
                                    existing or event,
                                )
                            continue
                except Exception as e:
                    logger.debug("Parking recheck failed for %s: %s", vname, e)

                # Send parking alert
                is_breakdown = new_alert == "breakdown"
                severity = (AlertSeverity.CRITICAL if new_alert == "critical"
                            else AlertSeverity.WARNING)

                subscribers = await get_platform_db().get_all_typed_subscribers("parking")
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
                    photo_bytes=map_bytes,                    bot_app=bot_app,                )

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
    bot_app: Application,
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

    subscribers = await get_platform_db().get_all_typed_subscribers("parking")
    acct_subs = [s for s in subscribers if s.account_id == account_id]

    tenant = await get_tenant_db(account_id)
    for sub in acct_subs:
        if sub.role == Role.DRIVER and sub.truck_num:
            if vname.lower() != sub.truck_num.lower():
                continue
        # Respect DND / quiet hours for resolved notifications
        if sub.is_in_quiet_hours():
            await tenant.queue_dnd_alert(
                account_id=account_id,
                telegram_id=sub.telegram_id,
                alert_type="parking",
                vehicle_name=vname,
                alert_text=text,
            )
            continue
        try:
            await bot_app.bot.send_message(
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
