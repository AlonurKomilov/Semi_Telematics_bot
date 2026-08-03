"""Writes the DQF sidecar into the carrier's own storage.

``dqf.py`` builds the artifacts; this module decides where they go and
when they are rewritten.  Split because the "what" is pure and testable
without a store, and the "where" needs an account, a backend and a folder
convention.

WHEN IT RUNS
------------
On submit, and again on every mutation that changes what the document
says: stage change, pre-hire check ticked or un-ticked, recruiter notes.
An application is not static — your screenshot shows one that is Approved
with 3 of 3 checks complete, none of which existed at submit — and a
compliance document that contradicts the record is worse than no document.

NEVER FATAL
-----------
Every entry point swallows its own failure.  The application is the
product; the sidecar is a safety net, and a safety net that can take down
the thing it protects is worth less than none.  A failed write leaves the
previous sidecar in place (stale, but stamped with its own
``generated_at``) and logs.
"""

from __future__ import annotations

import json
import logging

from features.applications.dqf import (
    DQF_PASSPHRASE_KEY,
    DOC_FILENAMES,
    application_folder,
    build_manifest,
    render_pdf,
    render_protected_ssn,
    render_readme,
)

logger = logging.getLogger(__name__)


async def _passphrase(tenant_db, account_id: int) -> str:
    """The carrier's DQF passphrase, or '' when they have not set one.

    Stored ``encrypt``ed, the same way the Drive refresh token is.  We
    hold a copy ONLY so a regeneration can reuse it — the carrier knows it
    independently, which is what makes the protected file survive us.
    """
    try:
        from infra.crypto import decrypt

        raw = await tenant_db.get_account_setting(account_id, DQF_PASSPHRASE_KEY, "")
        return decrypt(raw) if raw else ""
    except Exception:
        logger.exception("DQF passphrase read failed for account %d", account_id)
        return ""


async def _history(platform_db, account_id: int, app_id: int) -> list[dict]:
    """Stage history from the activity trail, oldest first.

    Empty for applications submitted before the trail existed — reported
    as empty rather than reconstructed.  Inventing a plausible sequence
    for a compliance record would be worse than admitting the gap.
    """
    try:
        rows = await platform_db.list_activity_events(
            account_id, entity_type="driver_application", entity_id=str(app_id),
        )
        return [
            {
                "at": r.get("created_at"),
                "action": r.get("action"),
                "actor": r.get("actor_name") or "",
            }
            for r in reversed(rows or [])
        ]
    except Exception:
        # A missing trail must not cost the carrier their whole sidecar.
        logger.debug("DQF history unavailable for app %s", app_id, exc_info=True)
        return []


async def write_sidecar(
    tenant_db, platform_db, account_id: int, app: dict, *, company_folder: str,
) -> bool:
    """(Re)write ``application.pdf`` + ``application.json`` + the protected
    SSN file for one application.  Returns True when something was written.

    ``app`` must come from ``get_driver_application(..., decrypt_pii=True)``
    — this is the ONLY production caller that decrypts, and it does so to
    write into the carrier's own storage, never to return over the wire.
    """
    try:
        from adapters.storage.object_store import get_object_store_for_account

        reference = str(app.get("reference") or "")
        if not reference:
            return False

        folder = application_folder(
            app.get("first_name") or "", app.get("last_name") or "", reference,
        )
        bucket = f"{company_folder}/applications/{folder}"

        history = await _history(platform_db, account_id, app.get("id"))
        manifest = build_manifest(app, history)
        store = await get_object_store_for_account(account_id, tenant_db)

        wrote = False

        pdf = render_pdf(manifest)
        if pdf:
            store.put(bucket, "application.pdf", pdf)
            wrote = True

        store.put(
            bucket, "application.json",
            json.dumps(manifest, indent=2, default=str).encode("utf-8"),
        )
        wrote = True

        # Legible at a glance in the Drive preview, with no software and
        # no login.  Whoever eventually needs this folder may be an
        # auditor or whoever inherited the account, and a password prompt
        # with no explanation is where a compliance document stops being
        # useful.
        store.put(bucket, "README.txt", render_readme(manifest))

        # The protected file only exists once the carrier has set a
        # passphrase.  Until then the SSN is simply absent from their
        # storage — the safe default has to be the one that happens when
        # nobody has made a choice.
        passphrase = await _passphrase(tenant_db, account_id)
        protected = render_protected_ssn(app, passphrase)
        if protected:
            store.put(f"{bucket}/documents", "ssn-protected.pdf", protected)

        return wrote
    except Exception:
        logger.exception(
            "DQF sidecar write failed for application %s", app.get("reference"),
        )
        return False


async def refresh_sidecar(tenant_db, platform_db, account_id: int, app_id: int) -> None:
    """Re-render after a mutation.  Best-effort, never raises.

    Loads the application fresh rather than taking the caller's copy: the
    caller usually holds the row from BEFORE its own update, and a sidecar
    rendered from that would state the previous stage — precisely the
    stale-document failure this exists to prevent.
    """
    try:
        app = await platform_db.get_driver_application(
            account_id, app_id, decrypt_pii=True,
        )
        if not app:
            return
        from features.work_orders.storage import sanitize_company_folder

        # Account-scoped lookup, not a bare by-id: the folder this resolves
        # to decides which company's Drive tree the file lands in, so a
        # company_id from another tenant must not resolve at all.
        company_folder = ""
        if app.get("company_id"):
            try:
                co = await platform_db.get_company_in_account(
                    account_id, int(app["company_id"]),
                )
                company_folder = sanitize_company_folder(
                    (co.display_name if co else "") or "",
                )
            except Exception:
                company_folder = ""
        await write_sidecar(
            tenant_db, platform_db, account_id, app,
            company_folder=company_folder or "unnamed-company",
        )
    except Exception:
        logger.exception("DQF sidecar refresh failed for application %d", app_id)
