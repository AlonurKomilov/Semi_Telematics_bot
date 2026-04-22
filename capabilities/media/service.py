"""Media service — camera snapshot gathering, analysis, and persistence."""

from __future__ import annotations

import asyncio
import logging
import os

import capabilities.ai as ai
from adapters.samsara.client import populate_company_display
from core.services import get_client, get_platform_db, get_tenant_db
from capabilities.vehicle_catalog.service import prepare_companies

logger = logging.getLogger("bot")

# Camera image storage directory (relative to project root)
_CAMERA_IMG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "camera_images"
)


async def get_fleet_for_cameras(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview for camera truck selection."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_fleet_overview(company=company)


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

    all_snapshots: list[dict] = []
    for co in companies:
        from adapters.samsara.client import SamsaraClient
        client = SamsaraClient(
            api_key=co.samsara_api_key,
            active_days=co.active_days,
        )
        try:
            snaps = await client.get_dashcam_snapshots(days=days)
            for s in snaps:
                s["_org"] = co.code
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

def save_camera_image(
    account_id: int, vehicle_name: str,
    camera_type: str, image_bytes: bytes,
) -> str:
    """Save dashcam screenshot to disk. Returns relative path from project root."""
    try:
        os.makedirs(_CAMERA_IMG_DIR, exist_ok=True)
        safe_name = vehicle_name.replace("/", "_").replace("\\", "_")
        fname = f"{account_id}_{safe_name}_{camera_type}.jpg"
        full = os.path.join(_CAMERA_IMG_DIR, fname)
        with open(full, "wb") as f:
            f.write(image_bytes)
        return os.path.join("data", "camera_images", fname)
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
                img_path = save_camera_image(
                    account_id,
                    r.get("vehicle", "?"),
                    r.get("camera_type", "forward"),
                    r["image_bytes"],
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
