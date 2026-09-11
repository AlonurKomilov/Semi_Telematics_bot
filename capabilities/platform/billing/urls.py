"""Where a payment provider sends the customer back.

Its own module, importing nothing but the environment, so the checkout
and the console's wiring check ask the SAME question: a check that
resolved the address by its own copy of the rule would happily verify a
URL the checkout never uses.  (The billing router may import the
interface layer; a capability that wants this answer must not have to.)
"""

from __future__ import annotations

import os

#: Candidate origins, most specific first.  DASHBOARD_BASE_URL leads
#: because this address has to land on the host that SERVES /billing —
#: not the platform's auth origin.  In production AUTH_BASE_URL holds
#: the apex (https://4truck.us), which answers 404 for /billing: a
#: customer who had just paid was shown an error page (probed
#: 2026-09-11).  AUTH_BASE_URL stays last so a deployment that sets only
#: it still returns somewhere real.
RETURN_ORIGIN_KEYS = ("DASHBOARD_BASE_URL", "APP_BASE_URL", "AUTH_BASE_URL")

#: Used when none of the above is set — the dashboard's own host.
DEFAULT_RETURN_ORIGIN = "https://dash.4truck.us"


def return_origin() -> str:
    """The origin to return to, without a trailing slash."""
    for key in RETURN_ORIGIN_KEYS:
        value = (os.getenv(key) or "").strip().rstrip("/")
        if value:
            return value
    return DEFAULT_RETURN_ORIGIN


def return_origin_source() -> str:
    """Which variable supplied it — '' when the default did.

    The console shows this: "which address" and "who said so" are
    different questions when four of them can be set at once.
    """
    for key in RETURN_ORIGIN_KEYS:
        if (os.getenv(key) or "").strip():
            return key
    return ""
