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

    # Drop retired trucks.  This one asks the PROVIDER directly, so
    # neither the ingest gate nor any registry filter reaches it —
    # Samsara still returns a truck we archived, and every snapshot it
    # returns is then handed to AI vision.  So an archived truck was
    # costing analysis budget on every 6-hourly run and alerting on the
    # result, displacing trucks the customer actually operates.
    #
    # Filtered by provider id, never by name: a unit number is REUSABLE
    # — a retired truck's door number can already be on a different
    # truck — so a name filter could silence the wrong vehicle.
    try:
        retired = await tenant.archived_refs(account_id)
        if retired:
            kept = [s for s in all_snapshots
                    if str(s.get("vehicle_id") or "") not in retired]
            dropped = len(all_snapshots) - len(kept)
            if dropped:
                logger.info(
                    "camera snapshots acct=%d dropped_archived=%d",
                    account_id, dropped,
                )
            all_snapshots = kept
    except Exception:
        # Fail-open: a stray analysis costs money, a blank camera report
        # costs the customer the thing they pay for.
        logger.exception(
            "archived filter unavailable for camera snapshots acct=%d",
            account_id,
        )

    return all_snapshots, show_co


# ── Analyze helper (concurrent with semaphore) ───────────────────

async def analyze_snapshot(
    snap: dict, account_id: int, sem: asyncio.Semaphore,
) -> dict:
    """Analyze a single snapshot with AI vision, respecting concurrency.

    Carries BOTH company keys onto the result: ``company`` is the label
    the report prints (``"?"`` when unknown), ``_org`` is the wire code
    the storage layer files the photo under (``""`` when unknown).  They
    are not interchangeable, and dropping ``_org`` here is what sent a
    year of camera images into a placeholder folder instead of their
    company's — the save path looked for a key analysis had renamed.
    """
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
                # The model's verdict FIRST, so the snapshot's own
                # identity keys below always win.  Spread last, a model
                # that ever returned an "_org" or "vehicle" key would
                # silently overwrite the truck's real identity with
                # something it inferred from the picture.
                **analysis,
                "vehicle": snap["vehicle_name"],
                "vehicle_id": snap.get("vehicle_id", ""),
                # `or`, not a .get default: a snapshot that carries
                # _org="" is the common case (Samsara answered, the org
                # was blank), and a .get default only fires when the KEY
                # is missing — so the label silently rendered blank
                # instead of the placeholder it documents.
                "_org": snap.get("_org") or "",
                "company": snap.get("_org") or "?",
                "driver": snap.get("driver_name", ""),
                "event_time": snap.get("event_time", ""),
                "camera_type": snap.get("camera_type", "forward"),
                "image_bytes": snap["image_bytes"],
            }
        except Exception as e:
            logger.warning(
                f"Camera AI analysis failed for {snap['vehicle_name']}: {e}"
            )
            return {
                "vehicle": snap["vehicle_name"],
                "vehicle_id": snap.get("vehicle_id", ""),
                "_org": snap.get("_org") or "",
                "company": snap.get("_org") or "?",
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
    check_id: int = 0,
) -> str:
    """Save dashcam screenshot to the account's configured object store.

    Routes through ``get_object_storage_for_account`` so when an account
    has Google Drive connected, dashcam captures land in their Drive
    instead of platform disk.  Returns the URL / object-store path
    (or Drive file ID for GDrive backends) to persist in the DB.

    ``tenant_db`` is passed in (vs resolved internally) because the
    caller — ``save_camera_results`` — already has it; avoiding the
    extra resolve keeps the per-vehicle loop fast.

    ``company_code`` is the Samsara org code (``_org``) for the
    vehicle's owning company, used to route the image into that
    company's folder.  When the caller doesn't have it we ask the
    vehicle registry — it is the SSOT for which company owns a unit, so
    a snapshot that arrived without an org tag still lands in the right
    place instead of a placeholder folder.
    """
    try:
        from adapters.storage.object_storage import get_object_storage_for_account
        from capabilities.object_storage.paths import resolve_company_folder
        safe_name = vehicle_name.replace("/", "_").replace("\\", "_")
        if not company_code:
            try:
                company_code = await tenant_db.company_code_for_unit(
                    account_id, vehicle_name,
                )
            except Exception:
                logger.warning(
                    "registry company lookup failed for unit %r acct=%s",
                    vehicle_name, account_id, exc_info=True,
                )
        # Bucket path mirrors the work-orders layout so a user browsing
        # their Drive sees ``{COMPANY}/camera-images/…`` next to
        # ``{COMPANY}/work-orders/…`` — consistent structure per
        # company, no opaque account-id prefix.
        company_folder = await resolve_company_folder(
            tenant_db, account_id, company_code,
        )
        bucket = f"{company_folder}/camera-images"
        # {company}_{truck}_{camera}_{check_id}.jpg
        #
        # The check_id is what makes it UNIQUE: the old key was
        # {truck}_{camera}.jpg, so every check for a truck overwrote the
        # previous photo — 17,689 checks left 340 files, and history
        # showed the newest image beside every past row.
        #
        # The company is repeated here even though the bucket already
        # names it: a file downloaded from Drive, mailed to a driver, or
        # dropped into a claim folder arrives with no folder context, and
        # a bare "103_forward.jpg" says nothing about whose truck it was.
        # Redundant on disk, self-describing everywhere else.
        key = f"{company_folder}_{safe_name}_{camera_type}_{check_id}.jpg" \
            if check_id else f"{safe_name}_{camera_type}.jpg"
        store = await get_object_storage_for_account(account_id, tenant_db)
        saved = store.put(bucket, key, image_bytes)
        # Cloud sync (hybrid accounts only; no-op elsewhere).  Safe to
        # enqueue ONLY because the key is per-check now — with the shared
        # key the worker would have deleted a local file that up to 366
        # other check rows still pointed at.
        if saved and check_id:
            from capabilities.object_storage.tracking import track_for_sync_if_hybrid
            await track_for_sync_if_hybrid(
                store, bucket, key, saved,
                entity_type="camera_check", entity_id=int(check_id),
                file_size=len(image_bytes),
            )
        return saved
    except Exception as e:
        logger.debug(f"Camera image save failed: {e}")
        return ""


async def save_camera_results(account_id: int, results: list[dict]):
    """Persist camera check results for history tracking."""
    tenant = await get_tenant_db(account_id)
    for r in results:
        try:
            # Row FIRST, photo SECOND, path THIRD.  The photo's filename
            # embeds the check id, which does not exist until the row
            # does — that id is the whole reason a truck's checks stop
            # overwriting each other's images.
            check_id = await tenant.save_camera_check(
                account_id=account_id,
                vehicle_id=r.get("vehicle_id", ""),
                vehicle_name=r.get("vehicle", "?"),
                camera_type=r.get("camera_type", "forward"),
                status=r.get("status", "OK"),
                obstruction=r.get("obstruction", "none"),
                alignment=r.get("alignment", "centered"),
                quality=r.get("quality", "good"),
                summary=r.get("summary", ""),
                image_path="",
            )
            if r.get("image_bytes") and check_id:
                img_path = await save_camera_image(
                    account_id,
                    r.get("vehicle", "?"),
                    r.get("camera_type", "forward"),
                    r["image_bytes"],
                    tenant_db=tenant,
                    company_code=r.get("_org", ""),
                    check_id=check_id,
                )
                if img_path:
                    await tenant.set_camera_check_image(
                        account_id, check_id, img_path,
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
