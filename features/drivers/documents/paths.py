"""Where a driver's documents live in the tenant tree.

CDL, medical card, DQF entries — under the company that employs them,
never a top-level ``_drivers`` folder, so each company's compliance
officer browsing Drive sees a self-contained set.

Moved out of ``features/work_orders/storage.py``: a driver's paperwork
is not a work-order attachment, and the two sharing a module is how
they came to share a maintainer's attention.
"""

from __future__ import annotations

def driver_docs_bucket(company_folder: str, user_id: int) -> str:
    """Compose the bucket path for a driver's personal documents.

    Driver documents (CDL, medical card, DQF entries) live under the
    company that currently employs them, never under a top-level
    ``_drivers`` folder, so each company's compliance officer browsing
    Drive sees a self-contained set of records for their drivers.
    """
    return f"{company_folder}/drivers/user-{user_id}"


def driver_docs_archive_bucket(
    company_folder: str, user_id: int, archive_date: str,
) -> str:
    """Compose the archive bucket path used when a driver leaves a
    company.  The driver's old folder is moved here so the original
    location is free for any future driver who later occupies the same
    seat — and the carrier retains an audit trail dated by the
    transition.

    ``archive_date`` is the YYYY-MM-DD the company-change took effect;
    multiple transitions on the same day collapse into the same date
    folder (rare in practice).
    """
    return f"{company_folder}/drivers/_archive/{archive_date}/user-{user_id}"
