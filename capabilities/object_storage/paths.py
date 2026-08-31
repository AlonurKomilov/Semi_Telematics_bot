"""Composing a path in the tenant file tree.

The primitives every feature needs to place a file: the company folder
a record belongs to, and the sanitising that keeps user data safe on a
path.  They live here because ``LAYOUT.md`` — the law they implement —
lives here, two files away.

They were in ``features/work_orders/storage.py``, a module whose own
docstring said "helpers for Work Order attachments", and eight other
features reached into work orders to borrow them: 14 of the 17
importers wanted ``resolve_company_folder`` alone.  A shared primitive
wearing one feature's name is a shared primitive nobody can find.

What is NOT here: the per-record composers.  Where a truck's paperwork
goes is the vehicles feature's business, where a repair invoice goes is
work orders' — each owns its own ``paths.py``, so the two cannot drift
into one file again.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Where a write lands when no company can be established for it.
#
# Every file SHOULD sit under the company that owns it — that is the
# whole point of the layout above, and a customer browsing their Drive
# reads a top-level folder as "one of my businesses".  A name like
# "unnamed-company" (the old value) read as exactly that and quietly
# accumulated real trucks' photos beside the real businesses.  The
# leading underscore says "not a company, a holding pen", sorts it away
# from the real names, and every write that reaches it is logged so the
# pen stays a bug report rather than a second home for data.
GENERIC_COMPANY_FOLDER = "_generic"

# Where data that belongs to the ACCOUNT AS A WHOLE goes.
#
# Not everything is company-scoped: a knowledge-base article and an
# inspection template serve the whole account, and forcing them under
# one company would be a lie about who owns them.  They still may not
# sit at the account root — a bare ``knowledge/`` beside five business
# names reads as a sixth business, which is the mistake this layout
# exists to prevent.
#
# Distinct from GENERIC_COMPANY_FOLDER on purpose, and the difference is
# intent: ``_generic`` means "we could not work out the company, go fix
# the writer" and logs when it is used; ``_account`` means "there is no
# company by design, this is correct".  One is a bug report, the other
# is a home.  Collapsing them would make the bug report unreadable.
ACCOUNT_LEVEL_FOLDER = "_account"

def slugify(value: str, max_len: int = 40) -> str:
    """Filesystem-safe slug.  Keeps letters/digits/dash/underscore,
    collapses runs of other chars to a single dash, trims length so
    folder names don't blow past the 255-char Drive/POSIX limit.
    """
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (value or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return s[:max_len] or "unknown"


def sanitize_company_folder(name: str) -> str:
    """Turn a company display name into a Drive-safe folder name.

    Drive accepts most printable characters in folder names but a few
    (``/`` ``\\`` ``?`` ``<`` ``>`` ``|`` ``*`` NUL) break path
    resolution or are forbidden by the API.  We replace those with an
    underscore, collapse internal whitespace runs to a single space,
    and cap length at 100 characters (Drive's hard limit is 255 but
    keeping it short makes the URLs in browse-Drive screenshots
    readable).

    Empty / whitespace-only input falls back to
    :data:`GENERIC_COMPANY_FOLDER` so a missing display_name never
    produces a path-less write.
    """
    if not name:
        return GENERIC_COMPANY_FOLDER
    safe = name.strip()
    for ch in "/\\?<>|*\x00":
        safe = safe.replace(ch, "_")
    safe = " ".join(safe.split())
    safe = safe[:100].strip()
    # "." / ".." are path components, not names — a company literally
    # named that would write outside its own folder level.
    if safe in (".", ".."):
        return GENERIC_COMPANY_FOLDER
    return safe or GENERIC_COMPANY_FOLDER


async def resolve_company_folder(
    tenant_db, account_id: int, company_code: str,
) -> str:
    """Resolve ``company_code`` → Drive-safe folder name for the
    company's display_name.

    Falls back to the sanitised code if the company row is missing or
    has no display_name (legacy / partial data).  An empty code means
    the CALLER could not establish a company, which is a defect in the
    caller and not a normal state — it yields the
    :data:`GENERIC_COMPANY_FOLDER` holding pen and says so in the log,
    because the alternative (a silent placeholder folder) is how a
    year of camera images ended up outside their companies.

    Done in the route handler (already async) rather than inside
    ``ObjectStorage.put`` so we don't trade a per-byte sync write for an
    async DB round-trip; routes typically resolve once and reuse.
    """
    if not company_code:
        logger.warning(
            "object write has no company for account=%s — filing under %s",
            account_id, GENERIC_COMPANY_FOLDER,
        )
        return GENERIC_COMPANY_FOLDER
    try:
        company = await tenant_db.get_company_by_code(account_id, company_code)
    except Exception:
        company = None
    if company is None:
        logger.warning(
            "company code %r not in the account=%s directory — "
            "filing under the code itself", company_code, account_id,
        )
    name = (company.display_name if company else "") or company_code
    return sanitize_company_folder(name)


def vehicle_folder(company_folder: str, unit_number: str) -> str:
    """One truck's folder — the parent of everything about it.

    Its documents and its work orders are children here, so a carrier
    browsing Drive opens ONE folder to answer "what about unit 110?".
    Lives in the capability rather than in either feature: both build
    on it, and a shape owned by one of them would make the other import
    across a feature boundary to place a file.
    """
    return f"{company_folder}/vehicles/{sanitize_company_folder(unit_number)}"


def vehicle_archive_folder(
    company_folder: str, unit_number: str, archive_date: str,
) -> str:
    """Where a RETIRED TRUCK's whole folder goes — papers, invoices and
    all, because the thing being archived is the vehicle.

    Not to be confused with archiving a DOCUMENT, which moves one paper
    into ``…/vehicles/{unit}/documents/_archive/`` and leaves the truck
    where it is.  Two different acts on two different objects; the first
    version of this collapsed them by moving only the documents folder
    into the truck archive, which archived a vehicle by touching its
    paperwork and left its work orders behind.

    Dated, so a unit number that comes back on a different truck does
    not collide with the record of the one that left.
    """
    return (f"{company_folder}/vehicles/_archive/{archive_date}/"
            f"{sanitize_company_folder(unit_number)}")
