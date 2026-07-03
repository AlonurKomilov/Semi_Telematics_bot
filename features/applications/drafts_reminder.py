"""Auto-remind abandoned apply-form drafts — per-link, recruiter-opted.

Hourly scheduler job (``application_draft_reminders``).  A link owner turns
the policy on per link (cadence + lifetime cap); this job then nudges each
idle draft on that link with the same resume-link email the manual [Remind]
button sends, until the draft's ``reminders_sent`` reaches the link's
``remind_max`` — the hard no-infinite-sends guarantee.  Eligibility (idle
window, spacing off the previous nudge, active/unexpired link, cap) lives in
``list_drafts_needing_reminder``; this module just sends + stamps.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_BATCH_LIMIT = 200   # emails per run — bounds a pathological backlog


async def job_send_draft_reminders(_app=None) -> None:
    from infra.platform import get_platform_db
    from features.applications.service import apply_base_url
    from capabilities.email.application_emails import send_resume_link_email

    db = get_platform_db()
    try:
        due = await db.list_drafts_needing_reminder(limit=_BATCH_LIMIT)
    except Exception:
        logger.exception("draft reminders: eligibility query failed")
        return
    if not due:
        return

    base = apply_base_url()
    sent = 0
    for d in due:
        try:
            ok = send_resume_link_email(
                to=d["email"],
                carrier_name=d.get("company_name") or d.get("company_code") or "",
                resume_url=f"{base}/resume/{d['resume_token']}",
                reminder=True,
            )
            if ok:
                # Stamp ONLY on success so an SMTP outage retries next run;
                # auto=True bumps the lifetime counter the cap counts.
                await db.mark_draft_emailed(d["id"], reminder=True, auto=True)
                sent += 1
        except Exception:
            logger.exception("draft reminders: send failed draft=%s", d.get("id"))
    logger.info("draft reminders: %d due, %d sent", len(due), sent)
