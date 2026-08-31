"""Applications config — the DQF export passphrase.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py`` is: both are
co-located with their domain and both may import ``interfaces.api.deps``.

DQF LIVES INSIDE THIS CONFIG; it is not a config of its own.  There is no
``/applications/dqf-config`` any more (only the deprecated alias) because
the DQF export is one thing this feature can be CONFIGURED to do, not a
peer feature.  A component of a feature's config never becomes a config
itself — capabilities/config/docs/ARCHITECTURE.md, "Naming".

⚠️  MOUNT THIS ROUTER BEFORE ``features.applications.router``.  That
module carries nine parametric routes; FastAPI matches in registration
order across the whole app, so a ``/{...}`` registered first would
swallow ``/applications/config`` and this module would never be reached.
Route-count and route-parity checks do NOT catch that — both routes still
exist, one merely shadows the other.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from capabilities.activity_trail import record_simple
from features.applications import dqf
from features.applications.dqf_notify import notify_passphrase_changed
from interfaces.api.deps import (
    get_current_db_user, get_platform_db, get_tenant_db, require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["applications"])

# ── DQF export passphrase (config family, ACCOUNT scope) ─────────────
#
# Account scope, not role scope, by the blast-radius rule: this is not
# how a page is arranged, it decides what gets written into shared
# storage, and two roles disagreeing about it would produce some DQFs
# with a protected SSN file and some without.  Follows the account-scope
# recipe exactly — feature-owned account_settings key, gated on
# can_manage_config_all, ZERO new permissions and ZERO new tables.
# SSOT: capabilities/config/docs/ARCHITECTURE.md.


class DqfPassphraseRequest(BaseModel):
    # Long enough to be worth encrypting with.  Not a password field on a
    # login form — it is typed once and stored by the carrier somewhere
    # they will still have it if we are gone.
    passphrase: str = Field(..., min_length=8, max_length=200)


# ── Feature config ──────────────────────────────────────────────────
#
# ``/applications/config`` is this feature's ONE config endpoint, the same
# shape every feature uses.  DQF lives INSIDE it as a key rather than
# owning a path of its own — it is one thing this feature can be
# configured to do, not a feature.
#
# ``/dqf-config`` remains as a DEPRECATED ALIAS so a browser holding the
# previous JS bundle keeps working across a deploy.
@router.get("/config")
@router.get("/dqf-config", deprecated=True)
async def get_config(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Whether a DQF export passphrase is set — NEVER the passphrase.

    The gate was ``require_permission_any("can_manage_applications",
    "can_manage_config_all")``.  The feature flag is gone: this is a
    config read, and Manage does not carry Config.  It was the last
    ``require_permission_any`` mixing a feature action with the config
    action.

    Deliberately unreadable through the API, even by an authorized
    caller.  Two reasons, and the second is the important one:

      * a passphrase that can be fetched over the wire is one API bug
        away from making every protected SSN file readable; and
      * the carrier is supposed to KNOW it.  If they need us to tell them
        what it is, then it is effectively our secret, and the protected
        file becomes unopenable the day our server is gone — which is the
        exact scenario the whole sidecar exists for.

    Forgetting it means setting a new one and regenerating; that is the
    correct outcome, not a gap.
    """
    raw = await tenant_db.get_account_setting(
        user["account_id"], dqf.DQF_PASSPHRASE_KEY, "",
    )
    # ``configured`` is whether an administrator CHOSE one; ssn_included is
    # whether exports actually carry the protected file.  They differ now
    # that an unconfigured account falls back to an identifier-derived
    # default — the export is complete either way, but the card must not
    # claim someone chose a passphrase when nobody did.
    return {
        "configured": bool(raw),
        "ssn_included": True,
        "using_default": not bool(raw),
    }


@router.post("/config/reveal")
@router.post("/dqf-config/reveal", deprecated=True)
async def reveal_dqf_passphrase(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Show the passphrase to an administrator who asks for it.

    I first built this UNREADABLE, reasoning that a passphrase reachable
    over the wire is one API bug from disclosure.  That was wrong, and the
    failure mode it creates is worse than the one it avoids: setting a NEW
    passphrase does not re-protect files already exported, so a lost
    passphrase permanently orphans every DQF written before it.  The SSN
    records in those files become unopenable by anyone, forever.

    An administrator holding can_manage_config_all can already set a new
    passphrase and re-export, so they effectively control this secret
    either way — reading it grants them no power they lacked, and it is
    the only way to open an older export.

    Not on page load, though: a deliberate POST, and RECORDED in the
    activity trail, so a reveal is something the account can see happened.
    """
    from infra.crypto import decrypt

    raw = await tenant_db.get_account_setting(
        user["account_id"], dqf.DQF_PASSPHRASE_KEY, "",
    )
    reviewer = None
    try:
        du = await get_current_db_user(user, platform_db)
        reviewer = du.id if du else None
    except Exception:
        pass
    await record_simple(
        platform_db, user["account_id"], reviewer,
        "dqf_passphrase_revealed", "setting", "dqf.export_passphrase",
    )
    if raw:
        return {"using_default": False, "passphrase": decrypt(raw), "defaults": []}

    # No passphrase chosen, so exports use the per-COMPANY default. This
    # account has one per carrier brand, and files under each open with
    # that brand's own — so showing a single value would be a lie.  List
    # them, which is also what support needs when a carrier calls with
    # their company name and MC.
    companies = await platform_db.get_account_companies(user["account_id"])
    return {
        "using_default": True,
        "passphrase": "",
        "defaults": [
            {
                "company": co.display_name or co.code,
                "passphrase": dqf.default_passphrase(co),
            }
            for co in (companies or [])
            if dqf.default_passphrase(co)
        ],
    }


@router.put("/config")
@router.put("/dqf-config", deprecated=True)
async def set_config(
    body: DqfPassphraseRequest,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Set the passphrase that protects the SSN file in exported DQFs.

    Encrypted at rest the same way the Drive refresh token is.  We keep a
    copy only so a later regeneration can reuse the same passphrase — the
    carrier holds it independently, and that independence is what makes
    the file survive us.

    Existing applications are NOT rewritten here.  A bulk re-render would
    be a long, failure-prone loop inside a settings save; each application
    picks the new passphrase up on its next change, and a deliberate
    re-export can be added if it turns out to be wanted.
    """
    from infra.crypto import encrypt

    await tenant_db.set_account_setting(
        user["account_id"], dqf.DQF_PASSPHRASE_KEY, encrypt(body.passphrase),
    )
    reviewer = None
    actor_name = ""
    try:
        du = await get_current_db_user(user, platform_db)
        reviewer = du.id if du else None
        actor_name = (du.display_name if du else "") or ""
    except Exception:
        pass
    # WHO and WHEN, so the readable files can say which passphrase era a
    # folder belongs to.  After a rotation a carrier's storage holds files
    # from two eras that look identical; without this there is no way to
    # tell which one still wants the old passphrase.
    from datetime import datetime, timezone as _tz
    await tenant_db.set_account_setting(
        user["account_id"], dqf.DQF_PASSPHRASE_SET_AT_KEY,
        datetime.now(_tz.utc).isoformat(),
    )
    await tenant_db.set_account_setting(
        user["account_id"], dqf.DQF_PASSPHRASE_SET_BY_KEY, actor_name,
    )
    # THAT it was set, never the value — the trail must not become the
    # second place the passphrase lives.
    await record_simple(
        platform_db, user["account_id"], reviewer,
        "dqf_passphrase_set", "setting", "dqf.export_passphrase",
    )
    # Mail the new passphrase to the OWNERS — not to whoever changed it.
    # This is the only copy that lives outside 4truck, and it has to
    # arrive BEFORE anything goes wrong: if we are ever unreachable, the
    # system that would send it is the system that is down.  Best-effort;
    # the setting is already saved and a mail failure must not undo it.
    await notify_passphrase_changed(
        platform_db, user["account_id"],
        passphrase=body.passphrase, changed_by=actor_name,
    )
    return {"configured": True, "ssn_included": True}


@router.post("/config/reexport")
@router.post("/dqf-config/reexport", deprecated=True)
async def reexport_dqf(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Rewrite every application's protected SSN file with the CURRENT
    passphrase.

    The half of "set your own passphrase" that was missing: rotating
    protects future exports, but everything already in the carrier's
    storage keeps the password it was written with.  Without this, an
    owner who follows the warning improves nothing except the next hire.

    Rewrites ONE file per application — the other three artifacts are
    unchanged by a rotation, and on a gdrive account every write is an
    API call against a quota.

    Does NOT reach copies that already left.  If a folder was shared with
    a broker or insurer who downloaded the old file, rotation cannot
    recall it; the UI says so rather than letting anyone assume otherwise.
    """
    from features.applications.sidecar import reexport_ssn_only

    result = await reexport_ssn_only(tenant_db, platform_db, user["account_id"])
    reviewer = None
    try:
        du = await get_current_db_user(user, platform_db)
        reviewer = du.id if du else None
    except Exception:
        pass
    await record_simple(
        platform_db, user["account_id"], reviewer,
        "dqf_reexported", "setting", "dqf.export_passphrase",
        note=f"{result['written']} of {result['scanned']} applications rewritten",
    )
    return result
