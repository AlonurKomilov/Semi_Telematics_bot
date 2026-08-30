"""When a truck's paper is about to lapse — pure date logic.

Buckets, not a daily nag: a document announces itself at T-30, T-14,
T-7, T-1 and on the day it lapses, and the caller's ledger claims each
bucket once.  Without that, a registration expiring in a month would
send thirty identical messages and teach everyone to ignore the
thirty-first.

Deliberately NOT imported from ``features/drivers/expiration.py``.
The math is the same and the buckets must stay in step — a test pins
that — but a vehicle feature reaching into the drivers feature for it
would couple two features to save twenty lines of date arithmetic.
The test is the cheaper coupling: it lives where cross-feature imports
cost nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: Days-before-expiry at which a document speaks.  0 = the day it
#: lapses.  Must match ``features.drivers.expiration.BUCKETS`` — pinned
#: by ``test_vehicle_doc_expiry.py``.
BUCKETS: tuple[int, ...] = (0, 1, 7, 14, 30)


@dataclass(frozen=True)
class ExpiringDoc:
    doc_id: int
    vehicle_id: int
    unit_number: str
    company_code: str
    doc_type: str
    expires_at: str
    days_left: int
    bucket: int


def _days_until(today: datetime, iso_date: str) -> Optional[int]:
    """Whole days from *today* to a ``YYYY-MM-DD`` date, or None when
    the value is not a date we can read.  A document with an
    unparseable expiry is not an emergency — it is a typo, and
    inventing an alert from it would be worse than silence."""
    if not iso_date:
        return None
    try:
        when = datetime.fromisoformat(str(iso_date)[:10]).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    return (when.date() - today.date()).days


def classify(docs: list[dict], today: Optional[datetime] = None
             ) -> list[ExpiringDoc]:
    """The documents that should speak today, one entry per document.

    A past-due document lands in bucket 0 — it keeps its place in the
    ledger, so the day-of alert fires once and never again.  A document
    further out than the widest bucket says nothing at all.

    Pure: no DB, no clock beyond the argument, so the bucket edges are
    testable without fixtures.
    """
    today = today or datetime.now(timezone.utc)
    out: list[ExpiringDoc] = []
    for d in docs:
        days = _days_until(today, d.get("expires_at") or "")
        if days is None:
            continue
        # Past due collapses onto the day-of bucket rather than opening
        # a new one per day overdue.
        bucket = next((b for b in sorted(BUCKETS) if days <= b), None)
        if bucket is None:
            continue
        out.append(ExpiringDoc(
            doc_id=int(d["id"]),
            vehicle_id=int(d["vehicle_id"]),
            unit_number=str(d.get("unit_number") or ""),
            company_code=str(d.get("company_code") or ""),
            doc_type=str(d.get("doc_type") or ""),
            expires_at=str(d.get("expires_at") or ""),
            days_left=days,
            bucket=bucket,
        ))
    return out


def describe(e: ExpiringDoc) -> str:
    """One line, in the words an operator would use."""
    label = e.doc_type.replace("_", " ").title()
    if e.days_left < 0:
        return f"{label} EXPIRED {abs(e.days_left)}d ago ({e.expires_at})"
    if e.days_left == 0:
        return f"{label} expires TODAY ({e.expires_at})"
    if e.days_left == 1:
        return f"{label} expires tomorrow ({e.expires_at})"
    return f"{label} expires in {e.days_left}d ({e.expires_at})"
