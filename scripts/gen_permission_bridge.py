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


def render() -> str:
    from capabilities.permissions.taxonomy import TAXONOMY, Fate

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
    person_pairs = _pairs(Fate.PERSON_SPLIT) if hasattr(Fate, "PERSON_SPLIT") else {}

    def _features(pairs: dict) -> dict:
        out = {}
        for noun, (wide, _narrow) in pairs.items():
            wide_verdict = TAXONOMY[wide]
            manage = (
                wide_verdict.target
                if wide_verdict.fate is Fate.VERB_MANAGE else None
            )
            out[noun] = ("can_view_" + noun, manage)
        return out

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
            "UNIT_FEATURES", "dict[str, tuple[str, str | None]]", _features(unit_pairs),
            "noun → (view field, manage field or None) — the canonical shape",
        ),
    ]
    if person_pairs:
        parts += [
            _dict_literal(
                "PAIRED_PERSON_FEATURES", "dict[str, str]",
                {n: own for n, (_w, own) in person_pairs.items()},
                "the person pairs, noun → the LEGACY own flag.  Their width is"
                "\n#: the role's (driver reads their own rows), never stored — see"
                "\n#: capabilities/permissions/scope.person_width.",
            ),
            _dict_literal(
                "PERSON_FEATURES", "dict[str, tuple[str, str | None]]",
                _features(person_pairs),
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
