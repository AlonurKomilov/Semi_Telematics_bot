"""Camera-checks API — the Vehicle cameras component's router.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Paths keep the historical ``/safety`` prefix so URLs are unchanged.
"""

import os

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse

from interfaces.api.deps import require_permission_any, get_tenant_db

router = APIRouter(prefix="/safety", tags=["safety"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


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
    from adapters.storage.object_store import resolve_disk_path
    full_path = resolve_disk_path(img_path, project_root=_PROJECT_ROOT)
    if not full_path:
        raise HTTPException(status_code=404, detail="Image file not found")
    real = os.path.realpath(full_path)
    if not real.startswith(os.path.realpath(_PROJECT_ROOT)):
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(real, media_type="image/jpeg")
