"""Where a signed-in person gets the browser extension.

Until it is on the Chrome Web Store, the extension is a zip of the
built package — and the place to get that is the product itself, not a
file somebody was emailed.  This streams the current build from the
server, zipped on the fly, so what people download is always what was
last deployed.

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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from interfaces.api.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["extension"])

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
