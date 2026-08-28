"""The one definition of "may this caller touch this company's vehicle".

THE CONTRACT: company restriction binds every VERB, not just viewing.
Owners and unrestricted users pass everything; a vehicle with no
``company_code`` is unscoped and passes for anyone holding the
permission.

That last clause is not a loophole, it is the fleet — on the live
account 87 of 188 active vehicles carry no company code, so reading
null as "denied" would hide nearly half the registry from every
restricted user.

The rule lived in two places that agreed by luck: ``inventory/router.py``
resolved by unit NAME and walled, while the registry-admin routes
resolved by registry ID and did not wall at all, so a user restricted to
company A could rename, archive or read the VIN of company B's trucks.
The two lookups are genuinely different and stay separate; only the
verdict is shared, because a verdict spelled out twice is a verdict that
will eventually disagree with itself.

Callers pass the CODE rather than the row, because one has it as a dict
key and the other as an attribute — and neither should have to care what
shape the other uses.
"""

from __future__ import annotations


def company_allows(company_code: str | None, allowed: list[str]) -> bool:
    """True when a caller restricted to ``allowed`` may act on a vehicle
    in ``company_code``.

    ``allowed`` empty  → unrestricted caller (owners, and anyone Team
                         Management never narrowed) → everything passes.
    ``company_code``
    empty or None      → unscoped vehicle → passes.
    """
    if not allowed:
        return True
    if not company_code:
        return True
    return company_code in allowed
