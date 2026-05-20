"""Storage-backend configuration routes — per-account.

Lets each tenant pick where their attachments live (work-order invoices,
maintenance photos, future modules).  Default backend is the platform
``DiskObjectStore``; the user can opt into Google Drive via OAuth so
their bytes live in their own Drive at no platform storage cost.

Three endpoints:

* ``GET  /storage/config`` — current backend + connection info
* ``POST /storage/google/start`` — kick off the OAuth consent flow,
  returns the URL the dashboard redirects the browser to
* ``GET  /storage/google/callback`` — Google redirects here with the
  auth code; we exchange for a refresh token, create the root folder
  on the user's Drive, and flip the account backend to gdrive
* ``POST /storage/google/disconnect`` — remove stored creds and revert
  to disk backend

Refresh tokens are encrypted at rest via ``infra.crypto.encrypt``.
The client secret + redirect URI are configured via environment
variables (the OAuth app itself is registered once per platform
deployment, but each user's refresh token is scoped to their own
Drive).
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from adapters.storage.object_store import (
    invalidate_object_store_for_account,
    STORAGE_BACKEND_KEY, STORAGE_GDRIVE_REFRESH_TOKEN,
    STORAGE_GDRIVE_ROOT_FOLDER_ID, STORAGE_GDRIVE_USER_EMAIL,
)
from infra.crypto import encrypt
from interfaces.api.deps import get_current_user, get_tenant_db, require_permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/storage", tags=["storage"])


# ── OAuth config (env-driven so deployments differ) ─────────────────────────


def _oauth_client_config() -> dict:
    """Build the OAuth client config dict from env.  Raises 503 if not
    configured — saves clearer error messaging than a generic 500."""
    client_id = os.getenv("GDRIVE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GDRIVE_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GDRIVE_OAUTH_REDIRECT_URI", "").strip()
    if not (client_id and client_secret and redirect_uri):
        raise HTTPException(
            status_code=503,
            detail=(
                "Google Drive backend not configured on this server.  "
                "Administrator must set GDRIVE_OAUTH_CLIENT_ID, "
                "GDRIVE_OAUTH_CLIENT_SECRET, and GDRIVE_OAUTH_REDIRECT_URI."
            ),
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    }


# In-memory state→account_id map for the OAuth dance.  State is a
# random opaque token attached to the consent URL; Google echoes it
# back in the callback so we can recover the account context without
# trusting URL parameters.  Survives one request cycle (typically
# seconds); cleared after callback OR after 10 minutes (stale).
_pending_oauth: dict[str, dict] = {}


def _gc_pending_oauth() -> None:
    """Evict OAuth-start entries older than 10 minutes.  Called on each
    /start call; tiny dict, no scheduled GC needed."""
    import time
    cutoff = time.time() - 600
    expired = [s for s, v in _pending_oauth.items() if v.get("ts", 0) < cutoff]
    for s in expired:
        _pending_oauth.pop(s, None)


# ── GET /storage/config ──────────────────────────────────────────────────────


@router.get("/config")
async def get_storage_config(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Return the account's current storage backend + connection info.

    Used by the Settings page to render the current state and the
    Connect/Disconnect actions.  Never returns the encrypted refresh
    token itself — only whether one is present.

    Includes a usage breakdown so the Settings page can render a
    "187 MB used of 15 GB" progress bar:

    * ``used_by_app_bytes`` — what we've uploaded for this account
      (sourced from our ``work_order_attachments.file_size`` rows;
      authoritative for "files this app created")
    * ``drive_quota`` — the user's whole-Drive numbers from Google's
      ``about().storageQuota`` endpoint (so the progress bar can show
      what % of the user's Drive we're using).  Only populated when
      Drive is connected; ``None`` otherwise.  Failures (token
      revoked, network glitch) fall back to ``None`` rather than
      erroring out — the rest of the response stays usable.
    """
    backend = await tenant_db.get_account_setting(
        user["account_id"], STORAGE_BACKEND_KEY, "disk",
    ) or "disk"
    has_token = bool(await tenant_db.get_account_setting(
        user["account_id"], STORAGE_GDRIVE_REFRESH_TOKEN, "",
    ))
    user_email = await tenant_db.get_account_setting(
        user["account_id"], STORAGE_GDRIVE_USER_EMAIL, "",
    )
    root_folder_id = await tenant_db.get_account_setting(
        user["account_id"], STORAGE_GDRIVE_ROOT_FOLDER_ID, "",
    )

    # Our own attachment-byte tally — runs for every account because
    # it's a single SQL SUM and the Settings page should show "used
    # by app" on both disk and gdrive backends.
    used_by_app_bytes = await tenant_db.attachments_total_bytes(
        user["account_id"],
    )

    # Drive quota — only when connected.  Fetched in a thread so the
    # synchronous Google client doesn't block the event loop.  We
    # pull the encrypted refresh token here (in the async route) and
    # hand the raw decrypted string to the sync helper — that keeps
    # the thread side free of any tenant_db / asyncio plumbing.
    drive_quota: Optional[dict] = None
    if backend == "gdrive" and has_token:
        encrypted = await tenant_db.get_account_setting(
            user["account_id"], STORAGE_GDRIVE_REFRESH_TOKEN, "",
        )
        if encrypted:
            from infra.crypto import decrypt
            refresh_token = decrypt(encrypted)
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                drive_quota = await loop.run_in_executor(
                    None, _fetch_drive_quota_sync, refresh_token,
                )
            except Exception as e:
                logger.warning(
                    "Could not fetch Drive quota for account %d: %s",
                    user["account_id"], e,
                )

    return {
        "backend": backend,
        "gdrive": {
            "connected": has_token,
            "user_email": user_email or None,
            "root_folder_id": root_folder_id or None,
        },
        "usage": {
            "used_by_app_bytes": used_by_app_bytes,
            "drive_quota": drive_quota,
        },
    }


def _fetch_drive_quota_sync(refresh_token: str) -> Optional[dict]:
    """Pull the user's whole-Drive quota from Google's ``about`` endpoint.

    Synchronous because google-api-python-client is synchronous; the
    caller runs this in a thread executor.  The decrypted refresh
    token is passed in so this function has no async dependencies —
    keeps the executor side clean.

    Limitations: this is the user's WHOLE Drive quota, not just the
    portion in our 4truck folder.  Drive's API doesn't expose a
    per-folder size cheaply (the only path is recursive listing,
    which is too expensive for a Settings-page render).  The Settings
    page renders both numbers — "used by us" comes from our DB,
    "user's total Drive" from this endpoint.
    """
    from adapters.storage.object_store_gdrive import _build_drive_service

    svc = _build_drive_service(refresh_token)
    about = svc.about().get(fields="storageQuota").execute()
    quota = about.get("storageQuota") or {}
    # Google returns strings — coerce to ints so the JSON shape is
    # consistent (sometimes the trial/unlimited tier returns no
    # ``limit`` at all, which we surface as ``None`` for the UI to
    # render as "unlimited" or omit the right-hand side).
    def _maybe_int(v):
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None
    return {
        "limit_bytes": _maybe_int(quota.get("limit")),
        "usage_bytes": _maybe_int(quota.get("usage")),
        "usage_in_drive_bytes": _maybe_int(quota.get("usageInDrive")),
    }


# ── POST /storage/google/start ──────────────────────────────────────────────


class OAuthStartResponse(BaseModel):
    authorize_url: str = Field(..., description="Send the browser here to begin consent")


@router.post("/google/start", response_model=OAuthStartResponse)
async def start_google_oauth(
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Build the Google OAuth consent URL and return it.

    The dashboard receives the URL, redirects the user there, and
    Google sends them back to ``/storage/google/callback`` after they
    grant consent.  State is opaque; we look it up server-side to
    identify which account is connecting.
    """
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail=(
                "google-auth-oauthlib is not installed on this server.  "
                "Run: pip install 'google-auth-oauthlib>=1.2'"
            ),
        )

    _gc_pending_oauth()
    cfg = _oauth_client_config()
    state = secrets.token_urlsafe(32)

    flow = Flow.from_client_config(
        cfg,
        scopes=["https://www.googleapis.com/auth/drive.file"],
        redirect_uri=cfg["web"]["redirect_uris"][0],
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",        # required to get a refresh token
        include_granted_scopes="true",
        prompt="consent",             # force a fresh refresh token even on re-connect
        state=state,
    )

    # google-auth-oauthlib >= 1.2 enables PKCE by default: at
    # authorization_url() time it generates a ``code_verifier`` and
    # sends the matching code_challenge with the auth request.
    # fetch_token() then has to send the SAME code_verifier back to
    # Google or the token exchange fails with
    # ``(invalid_grant) Missing code verifier``.  /start and /callback
    # run in different request scopes (different Flow instances), so
    # we persist the verifier alongside the state token.
    import time
    _pending_oauth[state] = {
        "account_id": user["account_id"],
        "user_sub": user["sub"],
        "code_verifier": getattr(flow, "code_verifier", None),
        "ts": time.time(),
    }
    return {"authorize_url": auth_url}


# ── GET /storage/google/callback ─────────────────────────────────────────────


@router.get("/google/callback")
async def google_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: Optional[str] = Query(None),
):
    """OAuth redirect target.  Google sends the user here after they
    grant (or deny) consent.

    On success we:
      1. Recover the account_id from the in-memory state map
      2. Exchange the auth code for an access + refresh token
      3. Create or find the ``4truck`` root folder on the user's Drive
      4. Encrypt + store the refresh token, root folder ID, and email
      5. Flip ``account_settings.storage.backend`` to ``gdrive``
      6. Invalidate the cached ObjectStore so the new backend takes
         effect on the next upload
      7. Redirect back to the dashboard Settings page

    On failure we redirect to Settings with a query param the page
    renders as an error banner.
    """
    # Callback intentionally has NO auth dependency. Google redirects
    # the user's browser here from accounts.google.com — that request
    # carries no JWT cookie for our domain, so a require_permission or
    # get_tenant_db dependency (which needs the Authorization header)
    # would 422 every legitimate callback. We identify the account via
    # the opaque state token written into _pending_oauth at /google/start,
    # then resolve tenant_db ourselves from that account_id.
    #
    # All redirects build an ABSOLUTE URL to the dashboard — the API
    # callback lives on ``api.4truck.us`` and the dashboard SPA on
    # ``dash.4truck.us``, so a relative ``/admin/storage`` would 404
    # on the API host.  ``DASHBOARD_BASE_URL`` defaults to the dash
    # subdomain; legacy apex deployments can override.  The path is
    # ``/admin/storage`` (the dedicated Drive-management page) — not
    # ``/admin/settings``, which is the generic account-settings page
    # and has no Drive UI to surface the success / error banner.
    dashboard_base = os.getenv("DASHBOARD_BASE_URL", "https://dash.4truck.us").rstrip("/")

    def _back(qs: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{dashboard_base}/admin/storage?{qs}",
            status_code=303,
        )

    pending = _pending_oauth.pop(state, None)
    if not pending:
        return _back("gdrive_error=expired")
    account_id = pending["account_id"]

    from infra.platform import get_router as _get_router
    tenant_db = await _get_router().get_tenant(account_id)

    if error:
        return _back(f"gdrive_error={error}")

    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        return _back("gdrive_error=oauthlib_missing")

    cfg = _oauth_client_config()
    flow = Flow.from_client_config(
        cfg,
        scopes=["https://www.googleapis.com/auth/drive.file"],
        redirect_uri=cfg["web"]["redirect_uris"][0],
        state=state,
    )
    # Restore the PKCE code_verifier captured at /start (see comment
    # there for why this is necessary).  Without it, fetch_token()
    # fails with ``(invalid_grant) Missing code verifier``.
    stored_verifier = pending.get("code_verifier")
    if stored_verifier:
        flow.code_verifier = stored_verifier

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.error("OAuth token fetch failed for account %d: %s", account_id, e)
        return _back("gdrive_error=token_exchange")

    creds = flow.credentials
    if not creds.refresh_token:
        # ``prompt=consent`` should always issue one, but Google sometimes
        # withholds when the user previously granted with the same scope
        # and account.  Tell the user to revoke and retry.
        return _back("gdrive_error=no_refresh_token")

    # Pull the user's email (for display in Settings) + create the root
    # folder so we have an ID to store.  Both operations use the
    # access token returned by the token exchange.
    try:
        user_email = _get_user_email(creds)
        root_folder_id = _create_or_find_root_folder(creds)
    except Exception as e:
        logger.error("Post-OAuth setup failed for account %d: %s", account_id, e)
        return _back("gdrive_error=drive_setup")

    # Pre-create the company / module folder tree so the user sees the
    # full layout immediately when they open Drive — instead of an
    # empty ``4truck`` folder that fills in lazily on first upload.
    # Failures here are best-effort and don't block the connect flow
    # (the per-upload find-or-create chain remains the source of truth).
    try:
        companies = await tenant_db.get_account_companies(account_id)
        _prepare_company_folders(creds, root_folder_id, companies)
    except Exception as e:
        logger.warning(
            "Drive prep: post-connect folder seeding failed for account %d: %s",
            account_id, e,
        )

    encrypted_token = encrypt(creds.refresh_token)
    await tenant_db.set_account_setting(account_id, STORAGE_GDRIVE_REFRESH_TOKEN, encrypted_token)
    await tenant_db.set_account_setting(account_id, STORAGE_GDRIVE_ROOT_FOLDER_ID, root_folder_id)
    await tenant_db.set_account_setting(account_id, STORAGE_GDRIVE_USER_EMAIL, user_email or "")
    await tenant_db.set_account_setting(account_id, STORAGE_BACKEND_KEY, "gdrive")

    invalidate_object_store_for_account(account_id)
    await tenant_db.add_audit_log(
        account_id, int(pending["user_sub"]),
        "storage_backend_connect",
        target_type="account", target_id=str(account_id),
        details=f"gdrive: {user_email}",
    )
    return _back("gdrive_connected=1")


# ── POST /storage/google/disconnect ──────────────────────────────────────────


@router.post("/google/disconnect")
async def disconnect_google(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Disconnect Drive and revert to the disk backend.

    We delete our stored copy of the refresh token but do NOT revoke
    Google-side — the user can do that from their Google account page.
    Existing attachments stored on their Drive remain accessible to
    them; our app simply stops writing new files there.

    Files already saved to Drive stay in the user's Drive (we don't
    have permission to delete them without ``drive`` scope and even
    then we shouldn't auto-purge).  New uploads go to disk.
    """
    await tenant_db.set_account_setting(user["account_id"], STORAGE_BACKEND_KEY, "disk")
    await tenant_db.set_account_setting(user["account_id"], STORAGE_GDRIVE_REFRESH_TOKEN, "")
    await tenant_db.set_account_setting(user["account_id"], STORAGE_GDRIVE_ROOT_FOLDER_ID, "")
    await tenant_db.set_account_setting(user["account_id"], STORAGE_GDRIVE_USER_EMAIL, "")
    invalidate_object_store_for_account(user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "storage_backend_disconnect",
        target_type="account", target_id=str(user["account_id"]),
    )
    return {"backend": "disk"}


# ── Helpers (Drive operations during callback) ───────────────────────────────


def _get_user_email(creds) -> str:
    """Pull the connected Google account's primary email.

    ``drive.file`` scope alone doesn't include profile info, so we use
    Drive's ``about`` endpoint which returns ``user.emailAddress``.
    """
    from googleapiclient.discovery import build
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    about = svc.about().get(fields="user(emailAddress)").execute()
    return (about.get("user") or {}).get("emailAddress", "")


def _create_or_find_root_folder(creds) -> str:
    """Return the ID of the ``4truck`` folder on the user's Drive,
    creating it if absent.  This is the role-neutral parent of every
    account-{id}/... subfolder (work-orders, camera-images, driver
    documents, parking maps — anything the platform stores)."""
    from googleapiclient.discovery import build
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    name = "4truck"
    q = (
        f"name = '{name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false "
        f"and 'root' in parents"
    )
    res = svc.files().list(
        q=q, spaces="drive", fields="files(id)", pageSize=1,
    ).execute()
    items = res.get("files", [])
    if items:
        return items[0]["id"]

    folder = svc.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            # No parents → goes to "My Drive" root.
        },
        fields="id",
    ).execute()
    return folder["id"]


def _find_or_create_subfolder(svc, parent_id: str, name: str) -> str:
    """Find a subfolder by name under ``parent_id``, creating if absent."""
    safe = name.replace("'", "\\'")
    q = (
        f"name = '{safe}' and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    res = svc.files().list(
        q=q, spaces="drive", fields="files(id)", pageSize=1,
    ).execute()
    items = res.get("files", [])
    if items:
        return items[0]["id"]
    folder = svc.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        fields="id",
    ).execute()
    return folder["id"]


def _prepare_company_folders(creds, root_folder_id: str, companies) -> None:
    """Pre-create the per-company folder tree under ``4truck/``.

    Lazy create-on-first-upload works fine functionally, but users who
    connect Drive and immediately open ``drive.google.com`` see an
    empty ``4truck`` folder — confusing.  Walking the company list at
    OAuth callback time and creating the structure up-front makes the
    layout discoverable from minute one::

        4truck/
          ├─ PREMIER TRUCKING/
          │   ├─ work-orders/
          │   ├─ camera-images/
          │   ├─ parking-maps/
          │   └─ drivers/
          └─ CARGO FREIGHT/
              └─ ...

    Each ``find-or-create`` is idempotent so re-connecting Drive over an
    existing folder tree is a no-op.  Failures are logged but don't
    block the OAuth success path — the lazy fallback in
    ``GDriveObjectStore._resolve_folder_chain`` still works.
    """
    from googleapiclient.discovery import build
    from capabilities.work_orders.storage import sanitize_company_folder

    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    subdirs = ("work-orders", "camera-images", "parking-maps", "drivers")

    for co in companies:
        try:
            name = sanitize_company_folder(co.display_name or co.code)
            co_id = _find_or_create_subfolder(svc, root_folder_id, name)
            for sub in subdirs:
                _find_or_create_subfolder(svc, co_id, sub)
        except Exception as e:
            logger.warning(
                "Drive prep: failed to create folders for company %s: %s",
                getattr(co, "code", "?"), e,
            )
