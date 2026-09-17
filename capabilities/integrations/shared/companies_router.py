"""Per-company API keys, for any provider that issues one per company.

Some vendors issue a key per COMPANY while the platform schedules per
ACCOUNT.  Samsara does; ORIENT ELD does; Datatruck does not.  The card
that closes that gap — a row per company with "Set / Test / Remove" —
was written for Samsara and lived behind ``/integrations/samsara/...``,
even though the dashboard had already been calling it through a
``{providerId}`` template the whole time.  So the frontend was generic
and the backend was not, and the second per-company provider found out
by getting a 404.

This module is that surface with the vendor name taken out.  It is
mounted LAST, after every vendor router, so a literal ``/samsara/...``
path still wins its own routes: Samsara's versions carry a dual-write
to the legacy ``companies.samsara_api_key`` column that only Samsara
has, and quietly inheriting these generic ones instead would drop that
write with no error anywhere.

What is deliberately NOT here: history backfill.  It is per-capability,
not per-company-key, and a provider that never declares
``HISTORY_BACKFILL`` has no use for the route.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from infra.platform import get_platform_db, get_tenant_db
from infra.services import invalidate_client
from interfaces.api.deps import require_permission

from .helpers import (
    audit,
    cross_check_company_identity,
    guard_credential_encryption,
    validate_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

_owner_only = require_permission("can_manage_integrations")


class CompanyCredentialUpsert(BaseModel):
    """Body for setting one company's API key."""

    api_token: str = Field(..., min_length=1, max_length=512)


async def _credentials(account_id: int, provider_id: str) -> dict:
    """The whole stored blob, or empty.  Never leaves this module."""
    integ = await get_platform_db().get_account_integration(
        account_id, provider_id,
    )
    if not integ or not integ.credentials:
        return {}
    return dict(integ.credentials)


async def _creds_map(account_id: int, provider_id: str) -> dict:
    """This provider's per-company key map, or empty.

    Never returns the keys to a caller — only this module reads it, and
    only to answer ``has_key``.  Raw tokens leave the database for the
    upstream API and for nowhere else.
    """
    integ = await get_platform_db().get_account_integration(
        account_id, provider_id,
    )
    if not integ or not integ.credentials:
        return {}
    return integ.credentials.get("companies") or {}


@router.get("/{provider_id}/companies")
async def list_provider_companies_generic(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """One row per company: does it have a key, and did it last work.

    ``health`` is ``None`` for a company nobody has tested, and the
    dashboard renders that as "untested" rather than "down" — an
    untested key and a failing key are different facts and the operator
    is the one who has to tell them apart.

    ``account_level_key`` says the integration is currently running on
    ONE key with no per-company key set.  It is deliberately absent
    from Samsara's own route, and the dashboard's copy for it
    ("only the company that key belongs to reports in") is true here
    and would be FALSE there: Samsara's builder lends a single
    account-level token to every company, while a per-company vendor's
    key only ever opens its own company.  If Samsara's route ever grows
    this field, that sentence has to grow a second branch.
    """
    validate_provider(provider_id)
    account_id = int(user["account_id"])

    from capabilities.integrations.company_health import (
        list_company_health, summarise_health,
    )

    creds = await _credentials(account_id, provider_id)
    creds_map = creds.get("companies") or {}
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    companies = await tenant.get_account_companies(account_id)

    # A removed company leaves its health record alive for the TTL.
    # Counting it would let "7 of 5 healthy" through.
    current = {co.code for co in companies}
    raw = await list_company_health(account_id, provider_id)
    health_map = {c: h for c, h in raw.items() if c in current}

    return {
        "account_id": account_id,
        "provider_id": provider_id,
        "health_summary": summarise_health(health_map, len(companies)),
        # A connect stores ONE key, because the connect form has one
        # field.  Until per-company keys are set, that key is what the
        # integration actually runs on — and every row below would say
        # "no key" while the feed is working.  Saying so is the
        # difference between "set these up" and "something is broken".
        "account_level_key": bool(
            not creds_map
            and (creds.get("api_key") or creds.get("api_token"))
        ),
        "companies": [
            {
                "code": co.code,
                "display_name": co.display_name,
                "has_key": bool(creds_map.get(co.code)),
                "active_days": getattr(co, "active_days", None),
                "health": health_map.get(co.code),
            }
            for co in companies
        ],
    }


@router.put("/{provider_id}/companies/{company_code}/credentials")
async def set_company_credential_generic(
    provider_id: str,
    company_code: str,
    body: CompanyCredentialUpsert,
    user: dict = Depends(_owner_only),
):
    """Set or rotate ONE company's API key.

    The cached client is invalidated afterwards: it was built with the
    old key and would keep using it until the process restarted, which
    reads to an operator as "I rotated the key and nothing changed".
    """
    validate_provider(provider_id)
    guard_credential_encryption()
    account_id = int(user["account_id"])

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    known = {co.code for co in await tenant.get_account_companies(account_id)}
    if company_code not in known:
        raise HTTPException(
            404, f"company {company_code!r} is not on this account",
        )

    updated = await get_platform_db().set_company_credential(
        account_id, provider_id, company_code, body.api_token.strip(),
    )
    if updated is None:
        raise HTTPException(
            409,
            f"{provider_id} is not connected for this account — connect it "
            "before setting a per-company key.",
        )
    invalidate_client(account_id)
    await audit(
        account_id, int(user.get("id") or 0),
        "integration.company_credential.set",
        f"{provider_id}:{company_code}",
    )
    return {"ok": True, "company_code": company_code, "has_key": True}


@router.delete("/{provider_id}/companies/{company_code}/credentials")
async def remove_company_credential_generic(
    provider_id: str,
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Remove ONE company's API key.

    The company row stays — only the key is cleared, so the row comes
    back with a "Set key" affordance rather than disappearing.  No
    encryption guard here: clearing a secret is safe with encryption
    off, and refusing would strand an operator who wants a key GONE.
    """
    validate_provider(provider_id)
    account_id = int(user["account_id"])
    await get_platform_db().set_company_credential(
        account_id, provider_id, company_code, None,
    )
    invalidate_client(account_id)
    await audit(
        account_id, int(user.get("id") or 0),
        "integration.company_credential.remove",
        f"{provider_id}:{company_code}",
    )
    return {"ok": True, "company_code": company_code, "has_key": False}


@router.post("/{provider_id}/companies/{company_code}/actions/test")
async def test_company_connection_generic(
    provider_id: str,
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Probe ONE company's key, so a failure names the company.

    An account-wide test on a five-company integration answers "one of
    these is wrong" and leaves the operator to guess which.  This builds
    a single-use provider over just that company's key — the same
    ``build_for_test`` the connect route uses — and never touches the
    cached client, so a probe cannot disturb a running feed.
    """
    validate_provider(provider_id)
    account_id = int(user["account_id"])

    creds_map = await _creds_map(account_id, provider_id)
    token = (creds_map.get(company_code) or "").strip()
    if not token:
        raise HTTPException(
            404,
            f"no {provider_id} key stored for company {company_code!r}",
        )

    # The company as WE know it, so the probe can check the key landed
    # in the right row.  A failure to read this is not a failure of the
    # probe — the key test still runs, just without the cross-check.
    ours = None
    try:
        tenant = await get_tenant_db(account_id)
        if tenant is not None:
            for co in await tenant.get_account_companies(account_id):
                if co.code == company_code:
                    ours = co
                    break
    except Exception:
        logger.exception(
            "company lookup failed acct=%d company=%s",
            account_id, company_code,
        )

    from adapters.telematics.registry import get_provider

    try:
        provider_cls = get_provider(provider_id)
    except KeyError:
        raise HTTPException(503, f"provider not registered: {provider_id}")

    creds = {"companies": {company_code: token}}
    started = time.monotonic()
    try:
        provider = await provider_cls.build_for_test(account_id, creds)
    except (ValueError, NotImplementedError) as e:
        raise HTTPException(400, str(e))
    except AttributeError:
        raise HTTPException(
            501,
            f"{provider_id} does not support per-company testing",
        )
    try:
        try:
            status = await provider.test_connection(creds)
        finally:
            try:
                await provider.close_if_owned_by_test()
            except AttributeError:
                # A provider without that method opened nothing for the
                # probe, so there is nothing to close.
                pass
    except Exception as e:
        # A probe that raises is a FAILED probe, not a broken endpoint:
        # the operator asked "does this key work", and "no, because the
        # host timed out" is the answer, not a 500.
        logger.warning(
            "per-company probe raised acct=%d provider=%s company=%s: %s",
            account_id, provider_id, company_code, e,
        )
        return {
            "code": company_code, "ok": False, "message": str(e),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "checked_at": None,
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)

    ok, message = cross_check_company_identity(
        status, ours, company_code)

    from capabilities.integrations.company_health import set_company_health

    try:
        await set_company_health(
            account_id, provider_id, company_code,
            ok=ok, message=message, elapsed_ms=elapsed_ms,
        )
    except Exception:
        logger.exception("company health record failed")

    # Same shape Samsara's own per-company test returns, because the
    # dashboard has one component for both.
    return {
        "code": company_code,
        "ok": ok,
        "message": message,
        "elapsed_ms": elapsed_ms,
        "checked_at": None,
    }
