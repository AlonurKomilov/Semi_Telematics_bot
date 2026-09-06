"""Regenerate the bridge maps in roles.py from the taxonomy contract.

The maps between the legacy flag names and the canonical verbs are
DERIVED data: ``capabilities/permissions/taxonomy.py`` is the contract,
and ``capabilities/permissions/roles.py`` carries the maps it implies.
They were generated once by hand-run code that did not survive; this
script is that code, kept, so the next contract change is a
regeneration instead of an edit.  A blanket rename over the hand-
written block once rewrote a map key into its own value and installed
a property over a live field — that is the failure this exists to
prevent.

    python3 -m scripts.gen_permission_bridge --check   # CI / guard
    python3 -m scripts.gen_permission_bridge --write   # apply

The derivation, in full:

* ``LEGACY_TO_CANONICAL`` — every taxonomy entry whose fate carries a
  target (the verb fates and the two scope splits).
* ``PAIRED_UNIT_FEATURES`` — one entry per UNIT scope split: the noun
  is its target minus ``can_view_``; the wide half is the sibling flag
  on the same stem (``_all``, or ``_map`` for the Live Map pair, whose
  name predates the convention).
* ``UNIT_FEATURES`` — noun → (view verb, manage verb or None); the
  manage verb is the wide half's target when that half bundles writes.
* ``PAIRED_PERSON_FEATURES`` / ``PERSON_FEATURES`` — the same two
  shapes for the PERSON scope splits ("my paystubs", "my loads"),
  whose width is the role's, not Team Management's.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROLES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "capabilities", "permissions", "roles.py",
)

#: the block this script owns, between these two markers (exclusive of
#: the opening line, inclusive of nothing after the closing one).
START = "#: legacy flag → canonical field (generated from the contract)\n"
END = "#: 1:1 renames, canonical → legacy (the drift guard and the wire use it)\n"

#: narrow suffix → the wide suffixes that can complete its pair.
_WIDE_SUFFIXES = ("_all", "_map")


def _dict_literal(name: str, kind: str, items: dict, comment: str) -> str:
    lines = [f"#: {comment}\n", f"{name}: {kind} = {{\n"]
    body = []
    for k, v in items.items():
        if isinstance(v, tuple):
            inner = ",\n".join(f'        {"None" if x is None else chr(34) + x + chr(34)}' for x in v)
            body.append(f'    "{k}": (\n{inner}\n    )')
        else:
            body.append(f'    "{k}": "{v}"')
    lines.append(",\n".join(body) + "\n}\n")
    return "".join(lines)


def _taxonomy():
    """The contract, loaded from its file — NOT through the package,
    whose ``__init__`` imports roles.py.  Regeneration is exactly the
    moment roles.py may not import (a new map not written yet), and
    the contract itself depends on nothing but the standard library."""
    import importlib.util
    path = os.path.join(os.path.dirname(ROLES), "taxonomy.py")
    spec = importlib.util.spec_from_file_location("_permission_taxonomy", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses resolve the module by name
    spec.loader.exec_module(mod)
    return mod.TAXONOMY, mod.Fate


def render() -> str:
    TAXONOMY, Fate = _taxonomy()

    # A flag whose target is its own name was already canonical: it
    # needs no alias, and one would install a property over a live field.
    legacy_to_canonical = {
        flag: v.target for flag, v in sorted(TAXONOMY.items())
        if v.target and v.target != flag
    }

    def _pairs(fate) -> dict:
        out = {}
        for flag, v in sorted(TAXONOMY.items()):
            if v.fate is not fate:
                continue
            narrow_suffix = "_" + flag.rsplit("_", 1)[1]
            stem = flag[: -len(narrow_suffix)]
            wide = next(
                (stem + s for s in _WIDE_SUFFIXES if stem + s in TAXONOMY), None,
            )
            assert wide, f"{flag}: no wide half on stem {stem!r}"
            noun = v.target.removeprefix("can_view_")
            assert noun not in out, f"{noun}: two narrow halves"
            out[noun] = (wide, flag)
        return out

    unit_pairs = _pairs(Fate.SCOPE_SPLIT)

    def _unit_features(pairs: dict) -> dict:
        out = {}
        for noun, (wide, _narrow) in pairs.items():
            wide_verdict = TAXONOMY[wide]
            manage = (
                wide_verdict.target
                if wide_verdict.fate is Fate.VERB_MANAGE else None
            )
            out[noun] = ("can_view_" + noun, manage)
        return out

    # A person pair has no wide LEGACY half on its stem (the wide side
    # was a manage flag under another name, or nothing at all): the
    # own flag alone is the pair, and the manage verb — when the
    # feature has one — is whatever the contract names can_manage_<noun>.
    known = set(TAXONOMY) | {v.target for v in TAXONOMY.values() if v.target}
    person_pairs, person_features = {}, {}
    for flag, v in sorted(TAXONOMY.items()):
        if v.fate is not getattr(Fate, "PERSON_SPLIT", None):
            continue
        noun = v.target.removeprefix("can_view_")
        assert noun not in person_pairs, f"{noun}: two own halves"
        person_pairs[noun] = flag
        manage = "can_manage_" + noun
        person_features[noun] = (v.target, manage if manage in known else None)

    parts = [
        _dict_literal(
            "LEGACY_TO_CANONICAL", "dict[str, str]", legacy_to_canonical,
            "legacy flag → canonical field (generated from the contract)",
        ),
        _dict_literal(
            "PAIRED_UNIT_FEATURES", "dict[str, tuple[str, str]]", unit_pairs,
            "the unit pairs, LEGACY names — kept for the stored-row sweep"
            "\n#: (which reads pre-flip JSON) and as the registry of unit features.",
        ),
        _dict_literal(
            "UNIT_FEATURES", "dict[str, tuple[str, str | None]]", _unit_features(unit_pairs),
            "noun → (view field, manage field or None) — the canonical shape",
        ),
    ]
    if person_pairs:
        parts += [
            _dict_literal(
                "PAIRED_PERSON_FEATURES", "dict[str, str]", person_pairs,
                "the person pairs, noun → the LEGACY own flag.  Their width is"
                "\n#: the role's (driver reads their own rows), never stored — see"
                "\n#: capabilities/permissions/scope.person_width.",
            ),
            _dict_literal(
                "PERSON_FEATURES", "dict[str, tuple[str, str | None]]", person_features,
                "noun → (view field, manage field or None) — the canonical shape",
            ),
        ]
    return "\n".join(parts) + "\n"


def current() -> str:
    src = open(ROLES, encoding="utf-8").read()
    i, j = src.index(START), src.index(END)
    return src[i:j]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Exit 1 when roles.py differs from the contract.")
    g.add_argument("--write", action="store_true", help="Rewrite the block in roles.py.")
    args = p.parse_args(argv)

    want, have = render(), current()
    if want == have:
        print("roles.py bridge maps match the taxonomy contract.")
        return 0
    if args.check:
        import difflib
        sys.stdout.writelines(difflib.unified_diff(
            have.splitlines(True), want.splitlines(True),
            "roles.py (checked in)", "taxonomy.py (implied)",
        ))
        print("\nroles.py does NOT match the contract — run with --write.")
        return 1
    src = open(ROLES, encoding="utf-8").read()
    open(ROLES, "w", encoding="utf-8").write(src.replace(have, want, 1))
    print("roles.py bridge maps regenerated.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
