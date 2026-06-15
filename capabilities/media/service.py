"""Media service — camera snapshot gathering, analysis, and persistence."""

from __future__ import annotations

import asyncio
import logging

import capabilities.ai as ai
from adapters.samsara.client import populate_company_display
# Per-account object store imported lazily inside save_camera_image
# so this module stays import-cheap (the GDrive backend pulls in
# google-api-python-client only when a tenant has opted into it).
from infra.services import get_tenant_db

logger = logging.getLogger("bot")


async def get_fleet_for_cameras(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview for camera truck selection.

    Routes through ``vehicles.service.get_vehicles_overview`` so the read
    is warehouse-first with live-Samsara fallback (same SSOT pattern as
    every other fleet read).
    """
    from features.vehicles.service import (
        get_vehicles_overview as _svc_vehicles_overview,
    )
    return await _svc_vehicles_overview(account_id, company=company)


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
    # company (~3-5 s).  Run them in parallel through the cached
    # MultiCompanyClient pool — Samsara rate-limits per *key*, so
    # concurrent calls across distinct keys don't contend, and the
    # shared pool gives us the circuit breaker + retry chain for
    # free.  Per-company iteration is over ``multi.clients`` so this
    # path can't drift from the canonical key map.
    from infra.services import get_client
    multi = await get_client(account_id)

    async def _fetch_one(code: str, client) -> list[dict]:
        try:
            snaps = await client.get_dashcam_snapshots(days=days)
        except Exception as e:
            logger.warning(f"Camera snapshots failed for {code}: {e}")
            return []
        for s in snaps:
            s["_org"] = code
        if vehicle_name:
            snaps = [
                s for s in snaps
                if s["vehicle_name"].lower() == vehicle_name.lower()
            ]
        return snaps

    results = await asyncio.gather(
        *(_fetch_one(code, c) for code, c in multi.clients.items()),
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
            # ``action="vision"`` routes per-attempt rows into ai_usage
            # via the vision module's internal telemetry — no external
            # log call needed (would duplicate the row).
            analysis = await ai.analyze_camera_image(
                snap["image_bytes"],
                vehicle_name=snap["vehicle_name"],
                account_id=account_id,
                role="system",
                action="vision",
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
        from features.work_orders.storage import resolve_company_folder
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


async def get_dashcam_snapshots(account_id: int, days: int = 3) -> list[dict]:
    """One recent dashcam frame per vehicle, merged across companies.

    SSOT accessor for raw snapshot frames (the camera tool's data path);
    ``gather_snapshots`` above remains the richer analysis-pipeline
    entrypoint.  Routed through the cached MultiCompanyClient pool so
    callers share the breaker + rate-limit retries.
    """
    from infra.services import get_client
    multi = await get_client(account_id)
    out: list[dict] = []
    for _code, client in multi.clients.items():
        out.extend(await client.get_dashcam_snapshots(days=days))
    return out
