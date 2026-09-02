"""The pair-death fold — pure decisions, no I/O.

When the ``*_all`` / ``*_vehicle`` permission pairs die (stage E of
the verb/scope migration), the WIDTH they carried per role must
already live in ``role_vehicle_scope`` — or every role an owner
narrowed through the matrix silently widens to the whole account.
This module decides what each stored grant set folds to; the script
in ``scripts/fold_pair_width.py`` reads real accounts, prints the
decisions, and writes them only under ``--apply``.

Vocabulary, per role and per paired feature:

  WIDE    — the wide flag is on (width 'all' for that feature)
  NARROW  — wide off, narrow on (width 'assigned' for that feature)
  NONE    — neither: the role cannot open the feature; width is
            undefined there and it does not vote

Fold rule — NARROWEST WINS, because the alternative is a disclosure:
a role that is NARROW on any feature folds to 'assigned'.  A role that
is WIDE on some features and NARROW on others is MIXED: the new model
holds ONE width per role, so the WIDE features lose their width.  That
loss is the safe direction, but it is a real functional change, so
the fold names those features and the owner decides — never silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capabilities.permissions.roles import (
    PAIRED_UNIT_FEATURES,
    Role,
)

WIDE, NARROW, NONE = "wide", "narrow", "none"


def builtin_width(role: str) -> str:
    """The role's built-in default — what an ABSENT role row means."""
    return "assigned" if role == Role.DRIVER.value else "all"


def classify_pairs(fs) -> dict[str, str]:
    """{feature: WIDE|NARROW|NONE} for one EFFECTIVE permission set."""
    out = {}
    for feature, (wide_flag, narrow_flag) in PAIRED_UNIT_FEATURES.items():
        wide = bool(getattr(fs, wide_flag, False))
        narrow = bool(getattr(fs, narrow_flag, False))
        out[feature] = WIDE if wide else (NARROW if narrow else NONE)
    return out


@dataclass(frozen=True)
class FoldDecision:
    role: str
    #: 'all' | 'assigned' — the width the role's features imply, or
    #: None when the role opens no paired feature at all
    implied: str | None
    #: the row to WRITE: 'assigned' when implied differs from the
    #: built-in; None means "no row" (absence = built-in default)
    write: str | None
    #: 'consistent' | 'mixed' | 'default' | 'no-access'
    shape: str
    #: WIDE features that lose their width under narrowest-wins
    lost: tuple[str, ...] = field(default_factory=tuple)
    #: every WIDE feature this key holds — merge_keys needs it to name
    #: what a disagreeing sibling key would lose
    wide: tuple[str, ...] = field(default_factory=tuple)
    #: every NARROW feature — the EVIDENCE column: a "consistent"
    #: verdict with no narrow list would hide which pair produced it
    narrow: tuple[str, ...] = field(default_factory=tuple)


def fold(role: str, classes: dict[str, str]) -> FoldDecision:
    """One role's decision from its pair classes."""
    voting = {f: c for f, c in classes.items() if c != NONE}
    if not voting:
        return FoldDecision(role, None, None, "no-access")
    narrow = sorted(f for f, c in voting.items() if c == NARROW)
    wide = sorted(f for f, c in voting.items() if c == WIDE)
    implied = "assigned" if narrow else "all"
    shape = "mixed" if (narrow and wide) else "consistent"
    lost = tuple(wide) if shape == "mixed" else ()
    if implied == builtin_width(role):
        # Same as what absence already means — no row; but a mixed
        # shape here means an owner WIDENED a narrow-by-default role
        # (a driver) on some features, and those still fold away.
        return FoldDecision(role, implied, None,
                            shape if shape == "mixed" else "default",
                            lost, tuple(wide), tuple(narrow))
    return FoldDecision(role, implied, implied, shape, lost, tuple(wide),
                        tuple(narrow))


def merge_keys(decisions: list[FoldDecision]) -> FoldDecision:
    """Several storage keys for ONE role (base + senior tier +
    company-specific rows) collapse to one row — narrowest wins across
    them too, and a disagreement is reported as mixed with the union
    of lost features.  ``role_vehicle_scope`` has neither a tier nor a
    company dimension; this is where that limit becomes visible."""
    assert decisions and len({d.role for d in decisions}) == 1
    role = decisions[0].role
    live = [d for d in decisions if d.implied is not None]
    if not live:
        return FoldDecision(role, None, None, "no-access")
    implied = "assigned" if any(d.implied == "assigned" for d in live) else "all"
    # A key that implied 'all' while the merge lands on 'assigned'
    # loses EVERY wide feature it held, not just its own mixed ones.
    lost = sorted({f for d in live for f in d.lost}
                  | {f for d in live if d.implied == "all" and implied == "assigned"
                     for f in d.wide})
    disagree = len({d.implied for d in live}) > 1
    shape = "mixed" if (disagree or any(d.shape == "mixed" for d in live)) else (
        "default" if implied == builtin_width(role) else "consistent")
    write = None if implied == builtin_width(role) else implied
    return FoldDecision(role, implied, write, shape, tuple(lost),
                        tuple(sorted({f for d in live for f in d.wide})),
                        tuple(sorted({f for d in live for f in d.narrow})))


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


def stale_narrow_crumbs(seed_fs, stored: dict) -> list[str]:
    """Narrow-half keys a stored grant row still carries as True for a
    pair the CURRENT seed grants NEITHER half of — and the row does not
    grant the wide half either.

    This is the residue shape: a seed once carried ``*_vehicle=True``
    baseline crumbs, a later cleanup removed them from the seed, and
    no migration swept the rows materialised from the old seed.  The
    row then keeps granting what the seed no longer does, invisibly.
    A wide grant on the same pair is NOT residue (someone opened the
    feature deliberately) and is left alone.
    """
    from capabilities.permissions.roles import normalize_stored_perm_keys
    stored = normalize_stored_perm_keys(stored)
    out = []
    for _feature, (wide, narrow) in PAIRED_UNIT_FEATURES.items():
        seed_none = not getattr(seed_fs, wide, False) and not getattr(seed_fs, narrow, False)
        if seed_none and stored.get(narrow) is True and not stored.get(wide):
            out.append(narrow)
    return sorted(out)


def system_trail_context(why: str, **extra) -> dict:
    """Trail context for an event with NO human actor.

    The trail records people: ``append_activity_events`` refuses an
    actor-less event unless its context declares ``system`` — the
    first crumb sweep wrote eleven grant changes and every trail write
    raised on exactly this, so the change landed and the record did
    not.  Both pre-flight scripts build their context here.
    """
    return {"system": why, **extra}
