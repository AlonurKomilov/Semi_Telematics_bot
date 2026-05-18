"""Daily expiration-alert scheduler for driver documents.

Fires once per day per (document, threshold-bucket).  Buckets are
T-30, T-14, T-7, T-1 (advance warnings) and T-0 (expired today).
Each bucket is recorded in ``driver_document_notifications`` so the
same alert never goes out twice for the same document.

Past-due documents also get their status flipped to ``expired`` so
the UI and any future "must-have-valid-CDL" gate can see the change.

The job is registered in ``interfaces/bot/scheduler.py`` at 09:00 UTC
(early enough for North-American drivers' morning, late enough that
overnight migration / data fixes have settled).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Bucket thresholds — order matters: we pick the SMALLEST applicable
# bucket each tick (so a doc at days_until=5 fires T-7, not T-30).
# T-0 is the "expired today" bucket — separate from the others so the
# UI can render a distinct "expired" alert.
BUCKETS: tuple[int, ...] = (0, 1, 7, 14, 30)


# Doc-type labels used in alert text — keep aligned with the dashboard
# DOC_LABEL map.  Falls back to the raw key for unknown types.
DOC_LABELS: dict[str, str] = {
    "cdl":              "CDL",
    "medical_card":     "Medical card",
    "mvr":              "MVR",
    "drug_screen":      "Drug screen",
    "background_check": "Background check",
    "training_cert":    "Training certificate",
    "other":            "Document",
}


@dataclass(frozen=True)
class ExpiringDoc:
    doc_id:        int
    account_id:    int
    user_id:       int
    doc_type:      str
    file_name:     str
    expires_at:    str          # ISO date
    days_until:    int          # negative when past-due
    bucket:        int          # 0, 1, 7, 14, or 30


def _bucket_for(days_until: int) -> Optional[int]:
    """Return the alert bucket for a doc with ``days_until`` remaining,
    or ``None`` if the doc isn't in any alert window."""
    if days_until < 0:
        return None        # past-due — handled by the status flip path
    for b in BUCKETS:
        if days_until <= b:
            return b
    return None            # > 30 days out — no alert yet


def _days_until(today: datetime, expires_at: str) -> Optional[int]:
    """Days between ``today`` (UTC) and ``expires_at`` (ISO date).
    Returns None if ``expires_at`` is empty or malformed."""
    if not expires_at:
        return None
    try:
        target = datetime.fromisoformat(expires_at[:10]).date()
    except ValueError:
        return None
    return (target - today.date()).days


def classify_documents(
    docs: list, today: Optional[datetime] = None,
) -> tuple[list[ExpiringDoc], list]:
    """Split a list of ``DriverDocument`` objects into:

    * ``alerts`` — docs in an active alert bucket (0/1/7/14/30)
    * ``expired`` — docs that crossed past-due and should have their
      ``status`` flipped to ``'expired'``

    Pure function — no DB calls — so unit tests don't need any setup.
    """
    today = today or datetime.now(timezone.utc)
    alerts: list[ExpiringDoc] = []
    expired = []
    for d in docs:
        days = _days_until(today, d.expires_at or "")
        if days is None:
            continue
        if days < 0:
            if d.status == "active":
                expired.append(d)
            continue
        bucket = _bucket_for(days)
        if bucket is None:
            continue
        alerts.append(ExpiringDoc(
            doc_id=d.id, account_id=d.account_id, user_id=d.user_id,
            doc_type=d.doc_type, file_name=d.file_name,
            expires_at=d.expires_at, days_until=days, bucket=bucket,
        ))
    return alerts, expired


def format_alert(doc: ExpiringDoc, *, for_driver: bool = True) -> str:
    """Build the Telegram message body for one expiring document.

    Drivers see a personal "please upload renewal" framing; admins see
    the same data with the driver-identity preserved (the bot appends
    the driver's name before sending — see ``check_document_expirations``)."""
    label = DOC_LABELS.get(doc.doc_type, doc.doc_type.replace("_", " ").title())

    if doc.bucket == 0:
        header = f"🟥 <b>{label} expired today</b>"
        action = (
            "Your document is no longer valid — please upload a renewal as soon as possible."
            if for_driver
            else "This document is no longer valid."
        )
    elif doc.bucket == 1:
        header = f"🟥 <b>{label} expires tomorrow</b>"
        action = (
            "Please upload the renewal today so you stay compliant."
            if for_driver
            else "Document expires tomorrow."
        )
    else:
        header = f"🟧 <b>{label} expires in {doc.bucket} days</b>"
        action = (
            "Please upload the renewal before the expiration date."
            if for_driver
            else f"Document expires in {doc.bucket} days."
        )

    body = (
        f"{header}\n\n"
        f"  📄 {doc.file_name}\n"
        f"  📅 Expires {doc.expires_at}\n\n"
        f"{action}"
    )
    return body
