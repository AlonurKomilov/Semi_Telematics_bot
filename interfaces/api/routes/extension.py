"""Where a signed-in person gets the browser extension.

The Chrome Web Store is where people install it — the Profile card
links there by the package id.  This ALSO streams the current build as
a zip, on the fly, for loading unpacked a build the store does not have
yet (a preview, a fix still under review): what people download is
always what was last deployed, never a file somebody was emailed.

Login-gated: the extension is for account users, and a public URL would
be an anonymous copy of the package for anyone to poke at.  Any role —
the panel itself enforces what each token may see.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from interfaces.api.auth import (
    EXTENSION_AUDIENCE, EXTENSION_SCOPE, AuthResponse, mint_session_token,
)
from interfaces.api.deps import (
    effective_perms, get_current_db_user, get_current_user, require_permission,
)
from interfaces.api.rate_limit import limiter
from fastapi import Query
from pydantic import BaseModel, Field

from adapters.storage import Role
from features.inventory import service as inventory_service
from capabilities.permissions.roles import get_user_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["extension"])

#: The consent page sends this header.  A foreign origin cannot: the
#: request would need a CORS preflight, and the allow-list refuses it —
#: so a cookie-carrying cross-site POST (CSRF) never reaches the mint.
CONNECT_HEADER = "x-requested-with"


@router.post("/connect", response_model=AuthResponse)
@limiter.limit("5/minute")
async def connect_extension(request: Request, user: dict = Depends(get_current_user)):
    """The ONLY place an ``aud=extension`` token is minted.

    Reached from the dashboard's consent page after the person pressed
    Confirm — never from the panel, which holds no credentials.  The
    caller is the dashboard session (the ``.4truck.us`` cookie); the
    answer is a token scoped to the live map, recorded as its own
    "Browser extension" session, always announced, revocable from Active
    Sessions.  No cookie is set from it.
    """
    if user.get("aud"):
        # A narrowed token must not mint another credential — a lifted
        # panel token could otherwise renew itself forever.
        raise HTTPException(
            status_code=403,
            detail="Sign in to the dashboard to connect the extension.",
        )
    if not request.headers.get(CONNECT_HEADER):
        raise HTTPException(status_code=400, detail="Missing X-Requested-With header")
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user or not getattr(db_user, "is_active", True):
        raise HTTPException(status_code=403, detail="User no longer active")
    if not db_user.id or db_user.id <= 0:
        # No session row means nothing to disconnect later — refuse
        # rather than mint a credential that cannot be revoked.
        raise HTTPException(status_code=403, detail="This sign-in cannot connect the extension.")
    # Permissions are the single source of truth for what a person may
    # see; the token's scope only NARROWS them, it grants nothing.  So a
    # person whose role has no live map would connect and then meet 403
    # on every request — say so here, before a session exists.
    perms = await get_user_permissions(
        Role(db_user.role.value), db_user.account_id,
        is_manager=bool(db_user.is_manager),
        is_primary_owner=bool(db_user.is_primary_owner),
    )
    if not getattr(perms, "can_view_location", False):
        raise HTTPException(
            status_code=403,
            detail="Your role does not include the live map, which is what the extension shows.",
        )
    token = await mint_session_token(
        db, request,
        user_id=db_user.id, telegram_id=db_user.telegram_id,
        account_id=db_user.account_id, role=db_user.role.value,
        is_manager=db_user.is_manager,
        is_primary_owner=db_user.is_primary_owner,
        remember_me=True,   # 30 days + refresh in place; daily re-consent trains people to stop reading it
        aud=EXTENSION_AUDIENCE, scope=EXTENSION_SCOPE,
        device_label="Browser extension",
        always_notify=True,
    )
    return AuthResponse(
        access_token=token,
        user={
            "telegram_id": db_user.telegram_id,
            "name": db_user.display_name or "",
            "role": db_user.role.value,
            "account_id": db_user.account_id,
        },
    )

#: The built package, produced by ``npm run build`` in
#: interfaces/browser_extension on deploy.  Resolved from this file so a
#: moved checkout still finds it.
_DIST = Path(__file__).resolve().parents[3] / "interfaces" / "browser_extension" / "dist"
_VERSION_FILE = _DIST / "manifest.json"


def _build_zip() -> bytes:
    if not (_DIST / "manifest.json").is_file():
        raise HTTPException(
            status_code=503,
            detail="The extension has not been built on this server yet.",
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(_DIST.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(_DIST).as_posix())
    return buf.getvalue()


@router.get("/me")
async def extension_me(user: dict = Depends(get_current_user)):
    """Who is connected — for the panel's avatar, and nothing more.

    The panel's token is a key to the live map, so the panel must not
    read ``/user/me``: that answer carries the whole permission matrix,
    the email, the company list.  Three display strings is all an
    avatar needs, and all a lifted panel token can learn here.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    account_name = None
    try:
        acct = await db.get_account(db_user.account_id)
        account_name = getattr(acct, "name", None) or None
    except Exception:
        logger.debug("account name lookup failed", exc_info=True)
    # Which of the panel's features this person may open.  Feature IDS,
    # not permission flags: the panel has no business learning the
    # permission vocabulary, and the mapping from grant to feature is a
    # decision, so it lives on the server where it can be tested.  The
    # verdict is the SCOPED one — a token that may not reach a feature
    # must not be offered it either.
    perms = await effective_perms(user)
    # Whether this token was minted before the audience's scope last
    # changed.  A panel holding a stale one is told so, and heals itself
    # with a single refresh — /auth/refresh re-reads the audience's
    # scope, so the answer comes back complete on the next ask.
    #
    # Without this the person has to Disconnect and connect again, and
    # nobody thinks to do that: they see a feature the build has and the
    # server will not offer, and read it as broken.  The alternative —
    # waiting for the ordinary refresh — is up to eight hours.
    claim = user.get("scope")
    scope_stale = (
        isinstance(claim, list) and set(claim) != set(EXTENSION_SCOPE)
    )
    features = [
        fid for fid, flag in (("live-map", "can_view_location"),
                              ("inventory", "can_view_inventory"))
        if getattr(perms, flag, False)
    ]
    # What the panel may DO, in the panel's vocabulary.  Kept apart from
    # ``features`` because a feature is a place you go and an ability is
    # a verb you may perform there — and because the panel must be able
    # to hide a control the server would refuse, rather than offering it
    # and answering 403 on the press.
    abilities = ["inventory.write"] if getattr(perms, "can_manage_inventory", False) else []
    return {
        "display_name": db_user.display_name or "",
        "role": str(user.get("role") or ""),
        "account_name": account_name,
        "features": features,
        "abilities": abilities,
        "scope_stale": scope_stale,
    }


@router.get("/vehicle-link")
async def extension_vehicle_link(
    vehicle: int,
    user: dict = Depends(get_current_user),
):
    """Where to open ONE truck at the provider that supplies it.

    Its own endpoint rather than the vehicle page's
    ``/vehicles/registry/{id}/links``: that one is gated on
    ``can_view_vehicles``/``can_view_faults``, which a live-map-scoped
    token narrows to False — the panel would get a 403, and widening
    the token to reach it would hand a truck-list key the vehicle
    surface too.

    The vehicle is taken as a QUERY parameter, not a path segment,
    because ``EXTENSION_ROUTES`` matches paths exactly — a path that
    carries an id could not be listed there without turning the
    allow-list into prefix matching, which is what it exists to avoid.

    The company wall still applies: a member restricted to one company
    cannot reach a foreign truck's link by guessing its id.
    """
    from infra.platform import get_tenant_db
    from interfaces.api.deps import get_user_company_codes
    from features.vehicles.scope import company_allows
    from features.vehicles.provider_links import build_provider_links

    account_id = int(user["account_id"])
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=503, detail="tenant DB unavailable")
    v = await tenant.get_vehicle(account_id, vehicle)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    allowed = await get_user_company_codes(user)
    if not company_allows(getattr(v, "company_code", "") or "", allowed):
        # Same answer as a truck that does not exist: a 403 here would
        # confirm the id belongs to a company the caller may not see.
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return {"links": await build_provider_links(account_id, v)}


@router.get("/inventory")
async def extension_inventory(
    vehicle: int,
    user: dict = Depends(require_permission("can_view_inventory")),
):
    """What is aboard ONE truck — the panel's answer beside the map.

    Its own endpoint for the same reason ``/vehicle-link`` has one: the
    feature's route (``/inventory/vehicle/{name}``) is addressed by NAME
    and would need prefix matching to sit in ``EXTENSION_ROUTES``, which
    is exactly what that allow-list exists to avoid.  The vehicle is a
    QUERY parameter here, and the path stays constant.

    Unlike ``/vehicle-link`` this one IS permission-gated.  Provider
    links are part of the map the panel already shows; inventory is a
    feature of its own that an owner may deliberately withhold, and a
    dispatcher denied it on the dashboard must be denied it here.  The
    gate works because ``can_view_inventory`` is in ``EXTENSION_SCOPE``;
    the scope is an intersection, so listing it there widens nobody --
    it only stops the narrowing from answering False for everyone.

    What it deliberately does NOT return: ``notes`` and ``identifier``.
    The question the panel asks is "what is on this truck, and does any
    of it need attention" -- a category, a name and a status answer it.
    A fuel-card number answers a different question, the dashboard's,
    and a key that lives in a browser extension should not carry it.

    The company wall still applies, and a truck behind it answers 404 --
    the same as one that does not exist, so an id cannot be probed.
    """
    from infra.platform import get_tenant_db
    from interfaces.api.deps import get_user_company_codes
    from features.vehicles.scope import company_allows
    from adapters.storage.vehicle_inventory import ATTENTION_STATUSES

    account_id = int(user["account_id"])
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=503, detail="tenant DB unavailable")
    v = await tenant.get_vehicle(account_id, vehicle)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    allowed = await get_user_company_codes(user)
    if not company_allows(getattr(v, "company_code", "") or "", allowed):
        raise HTTPException(status_code=404, detail="Vehicle not found")

    rows = await tenant.list_vehicle_inventory(account_id, vehicle)
    items = [
        {
            "id": int(r["id"]),
            "category": str(r["category"] or ""),
            "label": str(r["label"] or ""),
            "status": str(r["status"] or ""),
            # WHEN somebody last looked, so the panel's Verify button has
            # a visible result.  Without it the primary write verb closed
            # a strip and changed nothing on screen — and on an item
            # already flagged missing it saved a check that the row went
            # on contradicting.  A timestamp is the least of what this
            # record holds; the label and the serial still stay behind.
            "last_verified_at": str(r["last_verified_at"] or ""),
        }
        for r in rows
    ]
    return {
        "vehicle_id": vehicle,
        "items": items,
        "attention": sum(1 for i in items if i["status"] in ATTENTION_STATUSES),
    }


@router.get("/inventory-fleet")
async def extension_inventory_fleet(
    include_empty: bool = Query(False, alias="all"),
    user: dict = Depends(require_permission("can_view_inventory")),
):
    """Which vehicles the caller may see, and what is aboard each.

    A flat path, like every other route the panel may knock on —
    ``EXTENSION_ROUTES`` matches exactly, so there is nowhere to put an
    id.  Counts and a unit number, nothing else: an item's own label
    arrives when a vehicle is chosen, and its identifier and notes never
    do.

    ``?all=1`` is the panel's question — "every vehicle I may see" — and
    the bare path is the MAP's — "which vehicles have something aboard".
    They are different questions and the flag keeps them apart, which is
    also what makes this deployable: the overlay card renders a count
    line whenever one is present, so a server that began answering with
    ``total: 0`` rows would make every marker on google.com/maps read
    "0 items · all settled" for anyone whose extension had not updated
    yet.  The map asks without the flag and is unaffected; the panel
    asks with it.  (The client refuses a zero as well — belt and
    braces, since the two ship on different clocks.)

    Scope, in the order the Live Map applies it:
    * the grant (``can_view_inventory``, via the dependency);
    * the company wall, per row, with ``company_allows`` — NOT
      ``filter_by_allowed_companies``, which reads a blank company as
      denied and would drop the registry-only trailers and manual units
      that make up nearly half this fleet;
    * Team Management's unit width, via ``filter_by_assigned_trucks`` —
      the same helper and the same answer the Live Map beside it gives,
      so a driver does not see one truck on the map and two hundred
      here.
    """
    from infra.platform import get_tenant_db
    from interfaces.api.deps import filter_by_assigned_trucks, get_user_company_codes
    from features.vehicles.scope import company_allows
    from adapters.storage.vehicle_inventory import ATTENTION_STATUSES

    account_id = int(user["account_id"])
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(status_code=503, detail="tenant DB unavailable")
    allowed = await get_user_company_codes(user)

    if include_empty:
        # REGISTRY-FIRST.  The old shape folded
        # ``list_account_inventory`` — items JOIN vehicles — so a vehicle
        # with nothing recorded could not appear at all, and the panel
        # could not answer "is anything aboard 117?".  ``list_vehicles``
        # is active-only, so a retired truck stops being listed the day
        # it is archived.
        counts = await inventory_service.get_attention_map(account_id)
        rows = []
        for v in await tenant.list_vehicles(account_id):
            if not company_allows(getattr(v, "company_code", "") or "", allowed):
                continue
            c = counts.get(int(v.id)) or {}
            rows.append({
                # ``registry_id`` is for the SCOPE, not the wire — it is
                # projected away below.  Without it the identity ladder
                # falls to its second rung, which reads a row's
                # ``vehicle_id`` as the PROVIDER id; ours is the registry
                # id, so a driver would be matched in the wrong id space
                # — missing their own truck, or worse, matching somebody
                # else's whose provider id happened to collide.
                "registry_id": int(v.id),
                "vehicle_id": int(v.id),
                "name": str(v.unit_number or ""),
                "company": str(getattr(v, "company_code", "") or ""),
                "total": int(c.get("total") or 0),
                "attention": int(c.get("attention") or 0),
            })
    else:
        # The map's question, unchanged — see the docstring.
        fleet: dict[int, dict] = {}
        for r in await tenant.list_account_inventory(account_id):
            if not company_allows(str(r.get("company_code") or ""), allowed):
                continue
            vid = int(r["vehicle_id"])
            seen = fleet.get(vid)
            if seen is None:
                seen = fleet[vid] = {
                    "registry_id": vid,
                    "vehicle_id": vid,
                    "name": str(r.get("unit_number") or ""),
                    "company": str(r.get("company_code") or ""),
                    "total": 0,
                    "attention": 0,
                }
            seen["total"] += 1
            if str(r.get("status") or "") in ATTENTION_STATUSES:
                seen["attention"] += 1
        rows = list(fleet.values())

    rows = await filter_by_assigned_trucks(rows, user)
    # Wanting somebody first, then carrying anything, then by name —
    # without the middle key ~180 empty vehicles bury the interesting
    # ones the moment ?all=1 is asked.
    rows.sort(key=lambda v: (-v["attention"], v["total"] == 0, v["name"]))
    # Scoped on a domain row, PROJECTED to the wire: registry_id did its
    # work above and has no business on a page we do not own.
    wire = ("vehicle_id", "name", "company", "total", "attention")
    return {"vehicles": [{k: r[k] for k in wire} for r in rows]}


class _ItemRef(BaseModel):
    item_id: int


class _StatusBody(BaseModel):
    item_id: int
    status: str
    note: str = Field("", max_length=500)


async def _writable_item(user: dict, item_id: int) -> dict:
    """The item this caller may write to, or a 404 that says nothing."""
    from interfaces.api.deps import get_user_company_codes
    item = await inventory_service.item_if_visible(
        int(user["account_id"]), item_id, await get_user_company_codes(user),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return item


@router.post("/inventory-verify")
async def extension_verify_item(
    body: _ItemRef,
    user: dict = Depends(require_permission("can_manage_inventory")),
):
    """"I looked, it is here."

    The first thing the panel may WRITE, and the narrowest verb there
    is: it appends a check to the accountability trail and destroys
    nothing.  It is also the one somebody actually performs standing
    beside the truck, which is where a phone showing Google Maps is.

    The item id travels in the BODY.  ``EXTENSION_ROUTES`` matches paths
    exactly, so a path carrying an id could not be listed there — the
    same reason /extension/inventory takes its vehicle as a query.
    """
    from interfaces.api.deps import resolve_user_id
    item = await _writable_item(user, body.item_id)
    ok = await inventory_service.verify_item(
        int(user["account_id"]), item, actor_user_id=await resolve_user_id(user),
    )
    return {"ok": ok}


@router.post("/inventory-status")
async def extension_set_item_status(
    body: _StatusBody,
    user: dict = Depends(require_permission("can_manage_inventory")),
):
    """Flag what is not right: missing, damaged, needs a check.

    Deliberately NOT here, and not listed in EXTENSION_ROUTES either:
    add, transfer and remove.  Those are office actions — moving an item
    between trucks or retiring it is done at a desk with the registry in
    front of you, and a key that lives in a browser has no business
    doing them.  The scope opens the flag; the route list decides where
    the flag may be used, which is the whole point of keeping two lists.
    """
    from interfaces.api.deps import resolve_user_id
    item = await _writable_item(user, body.item_id)
    try:
        ok = await inventory_service.set_item_status(
            int(user["account_id"]), item, body.status,
            note=body.note, actor_user_id=await resolve_user_id(user),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": ok}


@router.get("/download")
async def download_extension(user: dict = Depends(get_current_user)):
    """The built extension as a zip — sideload it from chrome://extensions."""
    data = _build_zip()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="4truck-extension.zip"'},
    )


def extension_id_from_key(key_b64: str) -> str:
    """Chrome's id for a package: the first 128 bits of SHA-256 over the
    DER public key, written in the letters a–p instead of hex digits.

    The key in ``manifest.json`` is the one the Chrome Web Store
    generated when the item was created (Package → View public key), so
    a sideloaded build, the store build and this endpoint all agree —
    and nobody keeps a private key anywhere.
    """
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


@router.get("/info")
async def extension_info(user: dict = Depends(get_current_user)):
    """What the Profile page shows: version, the permanent id, and
    whether a build exists to download."""
    built = _VERSION_FILE.is_file()
    version, extension_id = "", ""
    if built:
        try:
            manifest = json.loads(_VERSION_FILE.read_text())
            version = str(manifest.get("version") or "")
            if manifest.get("key"):
                extension_id = extension_id_from_key(manifest["key"])
        except Exception:
            logger.warning("extension manifest unreadable", exc_info=True)
    return {
        "built": built,
        "version": version,
        # The PACKAGE id — one for every install, never per user or tenant.
        "extension_id": extension_id,
    }
