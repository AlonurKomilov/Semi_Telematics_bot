"""AI vision analysis of parking locations."""

from __future__ import annotations

import logging

from capabilities.alerting.pipeline import SYSTEM_USER_ID
from capabilities.parking.maps import _render_parking_map
from capabilities.formatting.helpers import escape_html
from infra.services import get_platform_db

logger = logging.getLogger("bot")


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
        # NOTE: lookup the renderer through the local module so test code
        # patching ``capabilities.parking.ai_vision._render_parking_map``
        # (and the legacy ``capabilities.parking._render_parking_map``)
        # still works.
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

            # Vision path doesn't yet route through the router-aware
            # ai.generate(); usage will be logged via the legacy path
            # below.  TODO: thread account_id/action into generate_with_vision.
            response, usage = await ai.generate_with_vision(
                prompt,
                map_bytes,
                system=(
                    "You are a fleet safety analyst specializing in commercial "
                    "truck parking safety. Analyze map imagery to determine "
                    "whether a parking location is safe for an extended stop."
                ),
            )
            # Vision tokens aren't currently captured by the router; keep
            # the legacy logging path for them.
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

            # Router telemetry handled inside ai.generate() via action=.
            response, _usage = await ai.generate(
                prompt,
                system="You are a fleet safety analyst. Be concise and factual.",
                account_id=SYSTEM_USER_ID,
                user_id=SYSTEM_USER_ID,
                user_context={"role": "system"},
                action="parking_analysis",
            )
        from capabilities.alerting.ai_maintenance import _truncate_at_sentence, _is_valid_ai_response
        if _is_valid_ai_response(response):
            return escape_html(_truncate_at_sentence(response, 800))
    except Exception as e:
        logger.debug(f"AI parking analysis failed: {e}")
    return ""
