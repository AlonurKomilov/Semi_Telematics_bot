"""Dismissing a callout — the one act that leaves a record.

Two different things a reader can do with a callout, and only one of
them comes through here:

  COLLAPSE  shrinks a strip to a single line.  The statement is still
            on screen, so there is nothing to audit and nothing to
            protect: the dashboard writes that straight to the user's
            own preferences and never calls this module.
  DISMISS   removes it from that person's view.  The information is
            gone for them, which is exactly the act an owner may need
            to reconstruct later ("did the system tell them, or did
            they close it?").  So it is written HERE, server-side.

Why the server owns the dismissal preference (the dashboard owns every
other one): if the client wrote the preference and separately asked for
the audit entry, the two could disagree — a dismissal with no record is
precisely the gap the record exists to close.  One writer, one truth.

Ordering, not a transaction: the trail lives in the TENANT database and
the preference in the PLATFORM one, so they cannot share a transaction.
The trail is written FIRST and a failure aborts the whole call — the
callout stays on screen rather than vanishing unrecorded.  The reverse
gap (trail written, preference write fails) leaves the record intact and
the reader mildly annoyed, which is the harmless direction.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from capabilities.activity_trail import record_simple
from interfaces.api.deps import (
    get_current_db_user, get_platform_db, get_current_user, get_tenant_db,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/callouts", tags=["callouts"])

# The preference the dashboard reads to know what this person has
# dismissed.  ONE frozen key holding a map, not a key per callout —
# preference keys are frozen once shipped, and a key per callout would
# mint a permanent one for every fault the platform ever learns to
# detect.
DISMISSED_KEY = "callout.dismissed"

# Backstop only.  Entries are pruned when their callout stops being
# emitted, so this is reached by pathological use, not normal life.
MAX_ENTRIES = 500


class DismissBody(BaseModel):
    # Opaque by contract — the server stores and echoes it, never parses
    # it.  See ``capabilities.callouts.models.callout_id``.
    callout_id: str = Field(..., min_length=1, max_length=400)
    # What the reader actually saw, recorded verbatim so a later
    # argument about the wording is settled by the record rather than by
    # whatever the copy says today.
    rendered: str = Field(default="", max_length=2000)
    entity_type: str = Field(default="", max_length=40)
    entity_id: str = Field(default="", max_length=120)
    # Undo: the same act in reverse, also recorded.  A dismissal that
    # could be silently walked back would be a record you cannot trust.
    undo: bool = False


def _load(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {"v": 1, "entries": {}}
    entries = data.get("entries")
    return {"v": 1, "entries": entries if isinstance(entries, dict) else {}}


@router.post("/dismiss")
async def dismiss_callout(
    body: DismissBody,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Remove a callout from THIS person's view, and record that.

    The actor comes from the session, never the request body — a client
    that could name someone else in the trail would make the trail
    worthless.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    account_id = int(user["account_id"])

    # ── The record first ────────────────────────────────────────
    # Wording is deliberately flat: "dismissed", never "acknowledged"
    # or "confirmed".  This proves the callout was shown and closed —
    # not that it was read, understood, or remembered — and a verb that
    # implies otherwise would invite exactly that over-reading.
    action = "callout.undismiss" if body.undo else "callout.dismiss"
    note = (
        f"{'Restored' if body.undo else 'Dismissed'} callout "
        f"{body.callout_id}"
    )
    try:
        await record_simple(
            tenant_db, account_id, db_user.id, action,
            body.entity_type or "callout", body.entity_id or body.callout_id,
            note=note,
            context={
                "callout_id": body.callout_id,
                # The exact text on screen at the moment it was closed.
                "rendered": body.rendered,
            },
        )
    except Exception as e:
        # Refusing here is the point: a dismissal nobody can reconstruct
        # is worse for the owner than a callout that would not close.
        logger.exception(
            "callout dismiss: trail write failed acct=%d user=%d id=%s",
            account_id, db_user.id, body.callout_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not record this dismissal — please try again.",
        ) from e

    # ── Then the preference ─────────────────────────────────────
    # Already recorded, so a failure past this point costs the reader a
    # re-appearance, never the record.
    stored = _load(await platform_db.get_user_preference(
        db_user.id, DISMISSED_KEY, "",
    ))
    entries: dict = stored["entries"]
    if body.undo:
        entries.pop(body.callout_id, None)
    else:
        entries[body.callout_id] = int(time.time() * 1000)
        if len(entries) > MAX_ENTRIES:
            # Oldest first — a dismissal from two years ago is the one
            # least likely to still be protecting anything.
            for dead in sorted(entries, key=entries.get)[:len(entries) - MAX_ENTRIES]:
                entries.pop(dead, None)
    try:
        await platform_db.set_user_preference(
            db_user.id, DISMISSED_KEY,
            json.dumps({"v": 1, "entries": entries}, ensure_ascii=False),
        )
    except Exception:
        logger.exception(
            "callout dismiss: preference write failed after trail write "
            "acct=%d user=%d — the record stands, the callout will return",
            account_id, db_user.id,
        )

    return {"ok": True, "callout_id": body.callout_id, "dismissed": not body.undo}
