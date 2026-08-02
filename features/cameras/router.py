"""Camera-checks API — the Vehicle cameras component's router.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Paths keep the historical ``/safety`` prefix so URLs are unchanged.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response

from adapters.storage.object_store import get_object_store_for_account
from interfaces.api.deps import require_permission_any, get_tenant_db

router = APIRouter(prefix="/safety", tags=["safety"])

# No ``_PROJECT_ROOT`` here any more.  This router used to resolve stored
# paths itself and chained FOUR dirname calls where three were needed,
# landing on the project's PARENT — which 404'd every dashcam image and
# simultaneously widened the traversal guard to sibling projects.  The
# image route now reads through the object store, which owns the only
# project root and enforces containment centrally, so the constant is
# not merely fixed but gone: there is nothing left to get wrong.


# ── Camera Checks ────────────────────────────────────────────

@router.get("/cameras")
async def camera_checks(
    vehicle: str | None = Query(None, description="Filter by vehicle name"),
    latest_only: bool = Query(True, description="Only latest check per vehicle"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission_any("can_cameras", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Camera check history — obstruction, alignment, quality per vehicle."""
    checks = await tenant_db.get_camera_check_history(
        user["account_id"],
        limit=limit,
        vehicle_name=vehicle if vehicle else None,
        latest_only=latest_only,
    )
    return {"checks": checks, "count": len(checks)}


@router.get("/cameras/{check_id}/image")
async def camera_check_image(
    check_id: int,
    user: dict = Depends(require_permission_any("can_cameras", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Serve the dashcam screenshot for a camera check."""
    checks = await tenant_db.get_camera_check_history(
        user["account_id"], limit=500,
    )
    check = next((c for c in checks if c.get("id") == check_id), None)
    if not check:
        raise HTTPException(status_code=404, detail="Camera check not found")
    img_path = check.get("image_path", "")
    if not img_path:
        raise HTTPException(status_code=404, detail="No image available")
    # Read THROUGH the object store, not off the filesystem.  This route
    # used to resolve the stored path to a local file and FileResponse
    # it, which works only while the account's backend is disk: on
    # ``gdrive`` there is no local copy at all, and on ``hybrid`` the
    # sync worker deletes the local copy once the file reaches Drive, so
    # every dashcam image would 404 the moment either was enabled.
    # ``get_by_id`` accepts both a stored path and a Drive id, so one
    # call is correct on all three backends.  Containment against path
    # traversal now lives inside the store (_disk_path_candidates), which
    # covers this route and every other get_by_id caller at once.
    store = await get_object_store_for_account(user["account_id"], tenant_db)
    data = store.get_by_id(img_path)
    if data is None:
        raise HTTPException(status_code=404, detail="Image file not found")
    return Response(content=data, media_type="image/jpeg")
