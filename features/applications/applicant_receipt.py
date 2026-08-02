"""The applicant's own copy of their submission.

Every other sender in this feature talks to a recruiter or a previous
employer.  The person who just handed over ten years of employment
history, an SSN and three signatures got nothing back — and the
reference number, the only key to the self-service status page, existed
on one screen and was then unrecoverable.

Lives in its own module rather than service.py so the receipt is
separable from the recruiter-notification fan-out.
"""

from __future__ import annotations

import asyncio
import logging

from features.applications.service import apply_base_url

logger = logging.getLogger("api.applications")


async def notify_applicant_received(
    platform_db, account_id: int, link: dict, email: str,
    applicant_name: str, reference: str,
) -> None:
    """Email the APPLICANT their reference number and status link.

    Best-effort by contract, like every other notification here — a mail
    failure must never affect a submission that already succeeded.

    The carrier name comes from the link's company when it has one, else
    the account's registered name: the applicant is applying TO this
    employer and is entitled to know who received their file.
    """
    try:
        from capabilities.email.application_emails import (
            send_application_received_email,
        )
        carrier_name = ""
        if link.get("company_id"):
            try:
                co = await platform_db.get_company_in_account(
                    account_id, link["company_id"],
                )
                if co:
                    carrier_name = co.display_name or co.code or ""
            except Exception:
                pass
        if not carrier_name:
            try:
                acct = await platform_db.get_account(account_id)
                carrier_name = (getattr(acct, "name", "") or "").strip()
            except Exception:
                pass
        await asyncio.to_thread(
            send_application_received_email,
            to=email, applicant_name=applicant_name, carrier_name=carrier_name,
            reference=reference,
            status_url=f"{apply_base_url()}/status/{reference}",
        )
    except Exception as e:
        logger.debug("applicant receipt for %s failed: %s", reference, e)
