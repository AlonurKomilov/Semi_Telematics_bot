"""Media service — camera snapshot gathering, analysis, and persistence."""

from __future__ import annotations

import asyncio
import logging

import capabilities.ai as ai
from adapters.samsara.client import populate_company_display
# Per-account object store imported lazily inside save_camera_image
# so this module stays import-cheap (the GDrive backend pulls in
# google-api-python-client only when a tenant has opted into it).
from infra.services import get_platform_db, get_tenant_db

logger = logging.getLogger("bot")


async def get_fleet_for_cameras(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview for camera truck selection.

    Routes through ``vehicles.service.get_fleet_overview`` so the read
    is warehouse-first with live-Samsara fallback (same SSOT pattern as
    every other fleet read).
    """
    from capabilities.vehicles.service import (
        get_fleet_overview as _svc_fleet_overview,
    )
    return await _svc_fleet_overview(account_id, company=company)


# ── Gather snapshots from all companies ──────────────────────────

async def gather_snapshots(
    account_id: int,
    vehicle_name: str | None = None,
    days: int = 7,
) -> tuple[list[dict], bool]:
    """Fetch dashcam snapshots, optionally filtered to one vehicle.

    Returns (snapshots, show_company_label).
    """
    tenant = await get_tenant_db(account_id)
    companies = await tenant.get_account_companies(account_id)
    populate_company_display(companies)
    show_co = len(companies) > 1

    # Each company has its own Samsara API key → one round-trip per
    # company (~3-5 s). Run them in parallel; the Samsara API rate
    # limits per *key*, so concurrent calls across distinct keys don't
    # contend with each other.
    from adapters.samsara.client import SamsaraClient

    async def _fetch_one(co):
        client = SamsaraClient(
            api_key=co.samsara_api_key,
            active_days=co.active_days,
        )
        try:
            snaps = await client.get_dashcam_snapshots(days=days)
        except Exception as e:
            logger.warning(f"Camera snapshots failed for {co.code}: {e}")
            return []
        finally:
            await client.close()
        for s in snaps:
            s["_org"] = co.code
        if vehicle_name:
            snaps = [
                s for s in snaps
                if s["vehicle_name"].lower() == vehicle_name.lower()
            ]
        return snaps

    results = await asyncio.gather(
        *(_fetch_one(co) for co in companies),
        return_exceptions=False,
    )
    all_snapshots: list[dict] = []
    for r in results:
        all_snapshots.extend(r)

    return all_snapshots, show_co


# ── Analyze helper (concurrent with semaphore) ───────────────────

async def analyze_snapshot(
    snap: dict, account_id: int, sem: asyncio.Semaphore,
) -> dict:
    """Analyze a single snapshot with AI vision, respecting concurrency."""
    async with sem:
        try:
            analysis = await ai.analyze_camera_image(
                snap["image_bytes"],
                vehicle_name=snap["vehicle_name"],
                account_id=account_id,
            )
            # Log vision AI usage
            usage = ai.get_last_usage()
            if usage:
                try:
                    model_name = (
                        ai.get_account_vision_model_name(account_id)
                        or ai.DEFAULT_VISION_MODEL
                    )
                    await get_platform_db().log_ai_usage(
                        account_id, 0,
                        model_name, "vision",
                        usage.get("prompt_tokens", 0),
                        usage.get("reply_tokens", 0),
                        usage.get("total_tokens", 0),
                        usage.get("thinking_tokens", 0),
                    )
                except Exception as e:
                    logger.debug("Failed to log AI usage for media analysis: %s", e)
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


# ── Store results in DB ─────────────────────────────────────────

async def save_camera_image(
    account_id: int, vehicle_name: str,
    camera_type: str, image_bytes: bytes,
    tenant_db,
    company_code: str = "",
) -> str:
    """Save dashcam screenshot to the account's configured object store.

    Routes through ``get_object_store_for_account`` so when an account
    has Google Drive connected, dashcam captures land in their Drive
    instead of platform disk.  Returns the URL / object-store path
    (or Drive file ID for GDrive backends) to persist in the DB.

    ``tenant_db`` is passed in (vs resolved internally) because the
    caller — ``save_camera_results`` — already has it; avoiding the
    extra resolve keeps the per-vehicle loop fast.

    ``company_code`` is the Samsara org code (``_org``) for the
    vehicle's owning company.  Used to route the image into the
    per-company Drive folder; empty falls back to ``unnamed-company``.
    """
    try:
        from adapters.storage.object_store import get_object_store_for_account
        from capabilities.work_orders.storage import resolve_company_folder
        safe_name = vehicle_name.replace("/", "_").replace("\\", "_")
        # Bucket path mirrors the work-orders layout so a user browsing
        # their Drive sees ``{COMPANY}/camera-images/…`` next to
        # ``{COMPANY}/work-orders/…`` — consistent structure per
        # company, no opaque account-id prefix.
        company_folder = await resolve_company_folder(
            tenant_db, account_id, company_code,
        )
        bucket = f"{company_folder}/camera-images"
        key = f"{safe_name}_{camera_type}.jpg"
        store = await get_object_store_for_account(account_id, tenant_db)
        return store.put(bucket, key, image_bytes)
    except Exception as e:
        logger.debug(f"Camera image save failed: {e}")
        return ""


async def save_camera_results(account_id: int, results: list[dict]):
    """Persist camera check results for history tracking."""
    tenant = await get_tenant_db(account_id)
    for r in results:
        try:
            img_path = ""
            if r.get("image_bytes"):
                img_path = await save_camera_image(
                    account_id,
                    r.get("vehicle", "?"),
                    r.get("camera_type", "forward"),
                    r["image_bytes"],
                    tenant_db=tenant,
                    company_code=r.get("_org", ""),
                )
            await tenant.save_camera_check(
                account_id=account_id,
                vehicle_id=r.get("vehicle_id", ""),
                vehicle_name=r.get("vehicle", "?"),
                camera_type=r.get("camera_type", "forward"),
                status=r.get("status", "OK"),
                obstruction=r.get("obstruction", "none"),
                alignment=r.get("alignment", "centered"),
                quality=r.get("quality", "good"),
                summary=r.get("summary", ""),
                image_path=img_path,
            )
        except Exception as e:
            logger.debug(f"Camera history save failed: {e}")
