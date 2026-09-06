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
from interfaces.api.deps import get_current_db_user, get_current_user
from interfaces.api.rate_limit import limiter
from adapters.storage import Role
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
    return {
        "display_name": db_user.display_name or "",
        "role": str(user.get("role") or ""),
        "account_name": account_name,
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
