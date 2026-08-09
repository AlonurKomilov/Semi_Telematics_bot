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
    DQF_PASSPHRASE_SET_AT_KEY,
    DQF_PASSPHRASE_SET_BY_KEY,
    DOC_FILENAMES,
    application_folder,
    build_manifest,
    default_passphrase,
    render_pdf,
    render_protected_ssn,
    render_readme,
)

logger = logging.getLogger(__name__)


async def _protection(tenant_db, account_id: int, company) -> tuple[str, dict]:
    """``(passphrase, protection-stamp)`` for this account.

    One resolver so the full write and the SSN-only rewrite cannot
    disagree about which passphrase is current or what the files claim
    about it.
    """
    stored = await _passphrase(tenant_db, account_id)
    if stored:
        set_at = await tenant_db.get_account_setting(
            account_id, DQF_PASSPHRASE_SET_AT_KEY, "",
        )
        set_by = await tenant_db.get_account_setting(
            account_id, DQF_PASSPHRASE_SET_BY_KEY, "",
        )
        return stored, {"set_at": set_at, "set_by": set_by, "is_default": False}
    return default_passphrase(company), {"is_default": True}


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
    tenant_db, platform_db, account_id: int, app: dict, *,
    company_folder: str, company=None,
) -> bool:
    """(Re)write ``application.pdf`` + ``application.json`` + the protected
    SSN file for one application.  Returns True when something was written.

    ``app`` must come from ``get_driver_application(..., decrypt_pii=True)``
    — this is the ONLY production caller that decrypts, and it does so to
    write into the carrier's own storage, never to return over the wire.
    """
    try:
        from adapters.storage.object_storage import get_object_storage_for_account

        reference = str(app.get("reference") or "")
        if not reference:
            return False

        folder = application_folder(
            app.get("first_name") or "", app.get("last_name") or "", reference,
        )
        bucket = f"{company_folder}/applications/{folder}"

        history = await _history(platform_db, account_id, app.get("id"))
        passphrase, protection = await _protection(tenant_db, account_id, company)
        manifest = build_manifest(app, history, protection)
        store = await get_object_storage_for_account(account_id, tenant_db)

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

        protected = render_protected_ssn(app, passphrase, protection)
        if protected:
            store.put(f"{bucket}/documents", "ssn-protected.pdf", protected)
            # First time regulated PII lands in this account's own cloud
            # storage, tell the owners.  Nobody opted in — the derived
            # default means it just starts happening — and the dialog's
            # warning only reaches someone who opens the dialog.  Fires
            # once, guarded by an account setting.
            from features.applications.dqf_notify import notify_export_started
            await notify_export_started(platform_db, tenant_db, account_id)

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
        from features.work_orders.storage import (
            GENERIC_COMPANY_FOLDER, sanitize_company_folder,
        )

        # Account-scoped lookup, not a bare by-id: the folder this resolves
        # to decides which company's Drive tree the file lands in, so a
        # company_id from another tenant must not resolve at all.
        company_folder = ""
        co = None
        if app.get("company_id"):
            try:
                co = await platform_db.get_company_in_account(
                    account_id, int(app["company_id"]),
                )
                company_folder = sanitize_company_folder(
                    (co.display_name if co else "") or "",
                )
            except Exception:
                co, company_folder = None, ""
        await write_sidecar(
            tenant_db, platform_db, account_id, app,
            company_folder=company_folder or GENERIC_COMPANY_FOLDER,
            company=co,
        )
    except Exception:
        logger.exception("DQF sidecar refresh failed for application %d", app_id)


async def reexport_ssn_only(
    tenant_db, platform_db, account_id: int, *, limit: int = 5000,
) -> dict:
    """Rewrite ONLY ``ssn-protected.pdf`` for every application.

    The upgrade path for a passphrase change.  Rotating protects future
    exports; everything already in the carrier's storage keeps the old
    password, so "set your own to protect these files properly" is a
    half-truth until this runs.

    ONE FILE PER APPLICATION, deliberately.  When the passphrase is the
    only thing that changed, the other three artifacts are byte-identical
    to what is already there — application.pdf and application.json carry
    the masked last-four, README.txt is generic.  Rewriting all four would
    be four times the writes for three files whose content did not move,
    and on a gdrive account every write is an API call against a quota.
    A real content change still goes through ``write_sidecar``; this is
    the narrow path for a rotation.

    The stamp line DOES change, so the readable files go stale on the era
    they name.  Accepted: they are rewritten on that application's next
    real change, and a folder whose README says an older date is exactly
    the signal a carrier needs while both eras coexist.  Claiming the new
    date on a file we did not rewrite would be worse.

    Never reads the old files — every byte is rebuilt from the database,
    which is why a hybrid account having deleted its local copies after
    sync does not matter here.
    """
    from adapters.storage.object_storage import get_object_storage_for_account
    from features.work_orders.storage import (
        GENERIC_COMPANY_FOLDER, sanitize_company_folder,
    )

    result = {"scanned": 0, "written": 0, "skipped": 0, "failed": 0}
    try:
        apps = await platform_db.list_driver_applications(account_id, limit=limit)
    except Exception:
        logger.exception("DQF re-export: could not list applications acct=%s", account_id)
        return result

    store = await get_object_storage_for_account(account_id, tenant_db)
    companies: dict[int, object] = {}

    for row in apps or []:
        result["scanned"] += 1
        try:
            app = await platform_db.get_driver_application(
                account_id, int(row["id"]), decrypt_pii=True,
            )
            if not app or not app.get("ssn"):
                result["skipped"] += 1
                continue

            cid = app.get("company_id")
            if cid and cid not in companies:
                companies[cid] = await platform_db.get_company_in_account(
                    account_id, int(cid),
                )
            company = companies.get(cid)

            passphrase, protection = await _protection(tenant_db, account_id, company)
            pdf = render_protected_ssn(app, passphrase, protection)
            if not pdf:
                result["skipped"] += 1
                continue

            folder = application_folder(
                app.get("first_name") or "", app.get("last_name") or "",
                str(app.get("reference") or ""),
            )
            company_folder = sanitize_company_folder(
                (getattr(company, "display_name", "") or "") if company else "",
            ) or GENERIC_COMPANY_FOLDER
            # Same path as the original write, so this OVERWRITES in place.
            # The Drive backend looks the name up and updates rather than
            # creating "ssn-protected (1).pdf" — without that, a rotation
            # would leave the old file beside the new one, still openable
            # with the old passphrase, defeating the rotation.
            store.put(
                f"{company_folder}/applications/{folder}/documents",
                "ssn-protected.pdf", pdf,
            )
            result["written"] += 1
        except Exception:
            result["failed"] += 1
            logger.exception(
                "DQF re-export failed for application %s", row.get("id"),
            )
    return result
