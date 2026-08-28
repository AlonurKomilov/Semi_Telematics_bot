"""Carrier Directory domain logic that is not HTTP plumbing.

Today that is one thing: deciding what name an EXTERNAL carrier sees as
the sender of a self-fill invite.
"""
from __future__ import annotations


# Longest sender name we will echo onto the public page / into an email.
# Matches the Pydantic cap on both write paths.
MAX_SENDER_NAME = 120

# How many template fields an external carrier is asked to fill: the two
# public row sections plus the four free-text basics on the form.  The
# invite email states this so the ask has an honest shape (it used to
# claim "10-15 minutes", which the reader disproved by scrolling).
#
# The template itself lives in the dashboard's fields.ts — this constant
# mirrors it across the language boundary, and
# features/carrier_directory/tests/test_carrier_directory.py::test_public_field_count_matches_template
# parses that file and fails if the two ever drift.
PUBLIC_BASIC_FIELDS = 4
PUBLIC_FIELD_COUNT = 74


def clean_sender_name(value: object) -> str:
    """Public alias of :func:`_clean` for WRITE paths.

    The resolver cleans on every read, so nothing unsafe can reach a
    header today — but that makes the guarantee a convention ("route
    through the resolver") rather than a property of the stored data.
    Callers that persist a sender name use this so a future consumer
    reading the column directly still gets a header-safe string.
    """
    return _clean(value)[:MAX_SENDER_NAME]


def _clean(value: object) -> str:
    """Strip control characters, then trim.

    The resolved name lands in an SMTP ``Subject`` header, so CR/LF must
    never survive.  ``send_email`` strips them again at the transport,
    but doing it here means every consumer of this function — page,
    email, anything added later — gets a header-safe string without
    having to remember.  Tabs and other C0/C1 controls go too: they have
    no place in a display name and only serve to disguise it.
    """
    text = str(value or "")
    return "".join(ch for ch in text if ch.isprintable()).strip()


def resolve_sender_name(profile_row: dict, account) -> str:
    """The name to show the external carrier, or '' for neutral wording.

    Precedence: the per-link override on the carrier's profile, then the
    account-wide ``public_display_name``, then nothing.

    ``accounts.name`` is deliberately NOT in that chain.  It is the
    tenant's registered legal name, and the reader here is an outside
    business with no relationship to the tenant — leaking it was the bug
    this resolver exists to close.  A blank result is a real answer
    meaning "say nothing about who we are"; callers render neutral copy
    ("the recruiting team") rather than substituting another name.

    Every surface that names the sender — the public page, the invite
    email subject and body — MUST route through here, so the five copies
    of that string cannot drift apart.
    """
    override = _clean((profile_row or {}).get("intake_display_name"))
    if override:
        return override[:MAX_SENDER_NAME]
    return _clean(getattr(account, "public_display_name", ""))[:MAX_SENDER_NAME]
