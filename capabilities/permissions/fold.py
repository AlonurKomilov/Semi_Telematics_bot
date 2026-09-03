"""What remains of the pair-death pre-flight — three helpers other
code still needs.

The pre-flight itself (classify_pairs / fold / merge_keys /
stale_narrow_crumbs and the two scripts over them) did its job on
2026-09-02 — the owner's dry-run found one shape of residue, the
hygiene sweep removed it, the fold reported zero rows — and the
physical flip then made the *_vehicle pairs it reasoned about
non-existent.  The tooling is archived under scripts/archive/ and in
git history; keeping it importable against fields that no longer
exist would be a lie with tests.
"""

from __future__ import annotations

from capabilities.permissions.roles import Role


def builtin_width(role: str) -> str:
    """The role's built-in default — what an ABSENT role row means."""
    return "assigned" if role == Role.DRIVER.value else "all"


def seed_for_key(key: str):
    """The seed FeatureSet the resolver starts from for one storage
    key — a base role, a senior tier (``fleet__manager``), or the
    co-owner row.  None for owners (never scoped) and unknown keys.
    Shared by both pre-flight scripts so they cannot disagree."""
    from capabilities.permissions.roles import (
        ROLE_PERMISSIONS, senior_default_featureset,
    )
    if key == "owner__co":
        return None
    base, _, tier = key.partition("__")
    try:
        role = Role(base)
    except ValueError:
        return None
    if role is Role.OWNER:
        return None
    return senior_default_featureset(role) if tier else ROLE_PERMISSIONS.get(role)


def system_trail_context(why: str, **extra) -> dict:
    """Trail context for an event with NO human actor.

    The trail records people: ``append_activity_events`` refuses an
    actor-less event unless its context declares ``system`` — the
    first crumb sweep wrote eleven grant changes and every trail write
    raised on exactly this, so the change landed and the record did
    not.  Both pre-flight scripts build their context here.
    """
    return {"system": why, **extra}
