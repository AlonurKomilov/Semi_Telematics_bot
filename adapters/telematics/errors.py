"""Telematics adapter errors that an API layer needs to tell apart.

Lives in the package root, deliberately free of any provider import, so
an HTTP layer can catch these without pulling a provider SDK into its
import graph.
"""

from __future__ import annotations


class NoTelematicsClientError(ValueError):
    """A company was requested that has no telematics client behind it.

    The adapter cannot tell WHY, and saying so is the point.  It holds a
    map of company code -> client, built by skipping every company whose
    API key is unset; a code missing from that map is either not a
    company on this account at all, or one the operator has added but
    not yet given a key.  The old message picked one of those readings
    ("Unknown company: TestCo") and was wrong in the common case: the
    company exists, is listed in the dashboard's own company filter, and
    has no key — which is a normal state during onboarding, answered
    with a 500 and a stack trace.

    Subclasses ``ValueError`` on purpose: it narrows what the adapter
    already raised, so no caller's failure surface widens.  No existing
    ``except ValueError`` is known to sit on this path today — the
    subclassing is there so that if one appears, or an old one moves,
    the change is invisible to it rather than a new escape.
    """

    def __init__(self, company: str, known: list[str] | None = None):
        self.company = company
        # Kept for logs only.  It is NOT in the message: a caller
        # restricted to one company should not learn the others' codes
        # from an error string.
        self.known = list(known or [])
        super().__init__(
            f"No telematics connection for company '{company}' — it is "
            "either not a company on this account, or its API key has "
            "not been set on the Integration card."
        )
