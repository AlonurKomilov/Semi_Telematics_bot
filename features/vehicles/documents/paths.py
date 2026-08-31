"""Where a truck's documents live in the tenant tree.

Registration, cab card, title, insurance, annual inspections.  Owned by
this sub-feature rather than by work orders, which is where these two
composers used to sit: a truck's papers and a repair invoice are
different records with different lifecycles, and only a shared module
made them look like one thing.

The shared primitives they build on (company-folder resolution,
sanitising) stay in ``capabilities/object_storage/paths.py``, beside
the LAYOUT.md law.
"""

from __future__ import annotations

from capabilities.object_storage.paths import sanitize_company_folder

def vehicle_docs_bucket(company_folder: str, unit_number: str) -> str:
    """The bucket for one truck's paperwork — registration, title,
    insurance, annual inspections.

    Named by UNIT NUMBER, not registry id, deliberately: this tree is
    mirrored into the customer's own Drive, where a compliance officer
    browses it by hand — ``vehicles/6862/`` reads as a truck,
    ``vehicle-16/`` reads as a database.  The number is sanitized the
    same way company names are (it is user data on a path).  The cost,
    stated: a renamed truck keeps its old folder until its documents
    are next touched — acceptable for paperwork that changes yearly,
    where the driver flow's ``user-{id}`` answer would cost daily
    readability for rename-safety nobody asked for.
    """
    return f"{company_folder}/vehicles/{sanitize_company_folder(unit_number)}"


def vehicle_docs_archive_bucket(
    company_folder: str, unit_number: str, archive_date: str,
) -> str:
    """Where a retired truck's paperwork moves — same pattern as the
    driver archive: the original location frees up for any future truck
    that reuses the number, and the carrier keeps an audit trail dated
    by the retirement.  Restore moves it back."""
    return (
        f"{company_folder}/vehicles/_archive/{archive_date}/"
        f"{sanitize_company_folder(unit_number)}"
    )
