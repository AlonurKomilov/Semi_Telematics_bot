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

from capabilities.object_storage.paths import vehicle_folder

def vehicle_docs_bucket(company_folder: str, unit_number: str) -> str:
    """The bucket for one truck's paperwork — registration, title,
    insurance, annual inspections.

    ``…/vehicles/110/documents/`` — a SUBFOLDER of the truck, because
    the truck's folder now holds more than paperwork: its work orders
    sit beside them under the same unit.  Documents used to be the
    truck folder itself, which left no room for anything else and made
    "everything about unit 110" impossible to express.

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
    return f"{vehicle_folder(company_folder, unit_number)}/documents"


def vehicle_docs_archive_bucket(company_folder: str, unit_number: str) -> str:
    """Where a SUPERSEDED document goes — last year's registration once
    this year's is filed.

    Inside the truck's own documents folder, because the truck has not
    gone anywhere: only this paper stopped being current.  A retiring
    TRUCK is the other archive entirely — ``vehicle_archive_folder``
    moves the whole unit, papers and invoices together.

    Undated: the object key already carries the upload timestamp, so
    two archived registrations cannot collide, and a date folder would
    only add a level to click through.
    """
    return f"{vehicle_folder(company_folder, unit_number)}/documents/_archive"
