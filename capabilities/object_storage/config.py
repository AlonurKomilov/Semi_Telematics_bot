"""Object storage config — where this account's files are written.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py`` is.

CONFIG, NOT MANAGE, and the split is the point.  ``router.py`` keeps this
feature's real operational work — connecting and disconnecting Drive,
retrying failed syncs, clearing orphans, reading health.  This module owns
one value, ``object_storage.backend``, which decides where every driver
document, invoice and DQF the account ever stores is written.  The
blast-radius rule puts that account-wide: "anything a computation reads is
account-wide, always."  A holder of Storage alone can still run the
storage; they can no longer silently redirect the whole document estate.

``POST /object-storage/backend`` stays as a DEPRECATED ALIAS — note it
also changed VERB.  It suited "perform a switch"; under the convention
this replaces a config value, so the primary is a PUT.  Both resolve to
the function below.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.storage.object_storage import OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN
from capabilities.activity_trail import record_simple
from interfaces.api.deps import get_tenant_db, require_permission, resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/object-storage", tags=["object-storage"])

class BackendSwitchRequest(BaseModel):
    backend: str = Field(..., description="One of: disk, gdrive, hybrid")


@router.put("/config")
@router.post("/backend", deprecated=True)
async def put_config(
    body: BackendSwitchRequest,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Switch the account's storage backend.

    CONFIG, not Manage — and this endpoint is why the distinction has
    teeth.  It writes ``object_storage.backend``, an account_settings row
    the registry owns (capabilities/config/account.py), and that one
    value decides where every driver document, invoice and DQF the
    account ever stores is written.  The blast-radius rule puts it
    account-wide: "anything a computation reads is account-wide, always."

    ``can_manage_storage`` keeps this feature's real operational work —
    connecting and disconnecting Drive, retrying failed syncs, clearing
    orphans, reading health.  It no longer decides where the bytes live.
    A holder of Storage alone can still run the storage; they cannot
    silently redirect the account's entire document estate.

    ``disk``    — files stay on the platform server.
    ``gdrive``  — every upload streams directly to the user's Drive.
    ``hybrid``  — uploads land on disk first, a background worker pushes
                  them to Drive within ~60 s.  Requires Drive connection.

    ``gdrive`` and ``hybrid`` require an active Drive OAuth grant — the
    endpoint 409s otherwise so the UI can prompt the user to connect.
    """
    backend = (body.backend or "").strip().lower()
    if backend not in ("disk", "gdrive", "hybrid"):
        raise HTTPException(
            status_code=422,
            detail="backend must be disk, gdrive, or hybrid",
        )
    account_id = int(user["account_id"])

    if backend in ("gdrive", "hybrid"):
        has_token = bool(await tenant_db.get_account_setting(
            account_id, OBJECT_STORAGE_GDRIVE_REFRESH_TOKEN, "",
        ))
        if not has_token:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Connect Google Drive before switching to this backend.",
                    "error_code": "drive_not_connected",
                },
            )

    await tenant_db.set_account_storage_backend(account_id, backend)
    await record_simple(
        tenant_db, account_id, await resolve_user_id(user),
        "storage_backend_switch", "account", account_id,
        changes={"storage_backend": {"from": None, "to": backend}},
    )
    return {"backend": backend}
