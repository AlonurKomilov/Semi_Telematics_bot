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


def plan_row_sweep(perm_dict: dict) -> tuple[dict, list[str], list[str]]:
    """What the stored-row sweep would do to one role_permissions row.

    Returns ``(canonical_row, removed_legacy_keys, collisions)``:

      * ``canonical_row`` — the row as ``normalize_stored_perm_keys``
        reads it today, i.e. exactly the effective grant set, now
        written under canonical keys only;
      * ``removed_legacy_keys`` — the legacy keys that vanish;
      * ``collisions`` — targets where a legacy key and its canonical
        key were BOTH present with different values, so the OR rule
        decided.  Reported so the owner can see where a hand-written
        row's intent was ambiguous; the effective value does not
        change, because reads already applied the same rule.

    A row with no legacy key plans nothing (idempotent).
    """
    from capabilities.permissions.roles import (
        LEGACY_TO_CANONICAL, normalize_stored_perm_keys,
    )
    legacy = [k for k in perm_dict if k in LEGACY_TO_CANONICAL]
    if not legacy:
        return dict(perm_dict), [], []
    canonical = normalize_stored_perm_keys(perm_dict)
    # A collision is a CANONICAL key disagreeing with what its legacy
    # keys say — not two halves of a legacy pair differing, which is
    # the pair's own (narrow-only) meaning and folds by definition.
    collisions = []
    legacy_or: dict[str, bool] = {}
    for k in legacy:
        t = LEGACY_TO_CANONICAL[k]
        legacy_or[t] = legacy_or.get(t, False) or bool(perm_dict[k])
    for target, lv in legacy_or.items():
        if target in perm_dict and bool(perm_dict[target]) != lv:
            collisions.append(target)
    return canonical, sorted(legacy), sorted(collisions)


#: `*_own` flag → the verb that is WIDE today for the same feature.  A
#: staff row holding only the own half would be widened by the fold
#: (own folds into view, and staff width is 'all'); a driver row already
#: holding the wide verb reads account-wide today on the loads router's
#: "holds view = account-wide" proxy and narrows when the proxy becomes
#: person_width.  Both are intent the fold must not guess at.
OWN_TO_WIDE_VERB: dict[str, str] = {
    "can_loads_own": "can_view_loads",
    "can_risk_report_own": "can_view_risk_reports",
    "can_driver_pay_view_own": "can_manage_driver_pay",
    "can_coaching_view_own": "can_manage_coaching",
    "can_driver_docs_own": "can_manage_driver_docs",
}


def plan_own_preflight(key: str, perm_dict: dict) -> list[tuple[str, str]]:
    """Findings for one stored grant row before the own→view fold.

    Returns ``[(flag, kind)]`` with kind ``staff_own_only`` (a non-driver
    key granted the own half and NOT the wide verb — the fold would
    WIDEN them) or ``driver_holds_wide`` (a driver key granted the wide
    verb — today wide on the loads proxy, narrow after).  Owner keys are
    never scoped and never reported.  Absent keys read as False.
    """
    role = key.split("__", 1)[0]
    if role == Role.OWNER.value:
        return []
    out: list[tuple[str, str]] = []
    for own, wide in OWN_TO_WIDE_VERB.items():
        has_own = bool(perm_dict.get(own, False))
        has_wide = bool(perm_dict.get(wide, False))
        if role == Role.DRIVER.value:
            if has_wide:
                out.append((own, "driver_holds_wide"))
        elif has_own and not has_wide:
            out.append((own, "staff_own_only"))
    return out
