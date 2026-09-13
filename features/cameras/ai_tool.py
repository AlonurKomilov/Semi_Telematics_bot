"""Camera tools: dashcam check with AI vision analysis."""

from __future__ import annotations

import logging

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.resolve import (
    company_for, resolve_for_tool, row_company, rows_for,
)

logger = logging.getLogger("bot.ai.tools")


@register_tool({
        # Analyses a dashcam frame pulled from the provider right now.
    # `vehicle_scope: "live"` makes the DISPATCHER refuse this
    # for a truck that has left the fleet, rather than
    # answering with stale readings as though they were
    # current — see capabilities/ai/tools/registry.py.
    "vehicle_scope": "live",
    "vehicle_arg": "vehicle_name",
"name": "check_vehicle_camera",
    "description": (
        "Check the dashcam status for a specific vehicle: captures the "
        "latest camera image and analyzes it for obstruction, alignment, "
        "and image quality. This is per-vehicle only — for a full account "
        "camera check, direct the user to the Camera Check feature "
        "in the main menu."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number",
            },
            "company": {
                "type": "string",
                "description": (
                    "Optional company code (e.g. 'OSY', 'G1') — needed only "
                    "when more than one truck shares the number."
                ),
            },
        },
        "required": ["vehicle_name"],
    },
})
async def check_vehicle_camera(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    try:
        from capabilities.ai.vision import analyze_camera_image
        if account_id is None:
            return {"vehicle": vehicle, "error": "Camera check requires account context."}
        resolved, err = await resolve_for_tool(db, account_id, tool_args)
        if err:
            return err
        co = company_for(resolved, tool_args)
        # Route through the cached MultiCompanyClient pool so this
        # request shares the connection pool, circuit breaker, and
        # rate-limit retries with the rest of the app.  Keys come from
        # the Integration card (dual-write keeps the legacy column in
        # sync for any not-yet-migrated reader).
        # Snapshot frames come through the media service (the SSOT
        # accessor) — it merges companies and rides the same cached
        # MultiCompanyClient pool (breaker + rate-limit retries).
        from .service import get_dashcam_snapshots as _svc_snaps
        # Ask for THIS truck, from THIS company.
        #
        # This asked every company's client for a frame from every one of
        # its vehicles and then used exactly one: a roster fetch per
        # company, a JPEG per truck (up to ~512 KB, eight at a time), and
        # safety VIDEOS through ffmpeg for whatever the media API missed.
        # On a 200-truck account that is ~200 image downloads and up to
        # ~400 video downloads to answer "is the camera on 231 blocked?"
        # — real provider quota, and a chat turn nobody waits out.
        _ref = (getattr(resolved, "telematics_ref", "") or "").strip()
        snaps = await _svc_snaps(
            account_id, days=3,
            vehicle_ids=[_ref] if _ref else None,
            company=co or None,
        )
        # Fail closed on the caller's Vehicle-Access scope FIRST: these
        # rows carry vehicle_id, so the ladder's rung 2 splits twins
        # even with no registry id on them.
        snaps = filter_to_scope(snaps, tool_args, key="vehicle_name")
        # Then pin to the truck the resolver named.
        #
        # This matched by NAME and then by company — and the company half
        # was DEAD: get_dashcam_snapshots merges the raw client rows
        # without stamping one, so row_company() returned "" for every
        # snapshot and `"" == co` was False for any vehicle whose
        # registry row HAS a company code. The tool answered "no recent
        # camera image" for those trucks whether or not a frame existed.
        # With the company filter gone the name alone would have been a
        # coin toss between twins, which is what the provider id settles.
        match = rows_for(resolved, snaps)
        if resolved is None or not (getattr(resolved, "telematics_ref", "") or ""):
            # Unregistered or not yet linked to the provider — the name
            # is all there is, and `co` is honoured where a row carries
            # one.
            match = [
                s for s in match
                if s.get("vehicle_name", "").lower() == vehicle.lower()
                and (not co or not row_company(s) or row_company(s) == co)
            ]
        snap = match[0] if match else None
        if not snap or not snap.get("image_bytes"):
            return {"vehicle": vehicle, "result": "No recent camera image found for this vehicle."}
        analysis = await analyze_camera_image(
            snap["image_bytes"],
            vehicle_name=vehicle,
            account_id=account_id,
        )
        return {
            "vehicle": vehicle,
            "camera_type": snap.get("camera_type", "unknown"),
            "event_time": snap.get("event_time", ""),
            "analysis": analysis,
        }
    except Exception as e:
        logger.error(f"Camera check tool failed for {vehicle}: {e}")
        return {"vehicle": vehicle, "error": f"Camera check failed: {e}"}
