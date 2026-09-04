"""The bridge maps in roles.py are DERIVED from the taxonomy contract.

``LEGACY_TO_CANONICAL``, ``PAIRED_UNIT_FEATURES`` and their siblings
are not opinions kept in roles.py — they are what
``capabilities/permissions/taxonomy.py`` implies, rendered by
``scripts/gen_permission_bridge.py``.  This guard fails when the two
drift, so a contract change cannot half-land: the maps are regenerated
or CI says so.

The failure it exists to prevent has already happened once — a blanket
rename over the hand-written block rewrote a map key into its own value
and installed an alias property over a live field.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import importlib.util
import pathlib

from tests._repo import REPO


def _gen():
    path = pathlib.Path(REPO) / "scripts" / "gen_permission_bridge.py"
    spec = importlib.util.spec_from_file_location("_gen_permission_bridge", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_roles_matches_the_contract():
    gen = _gen()
    assert gen.render() == gen.current(), (
        "roles.py's bridge maps no longer match taxonomy.py — run "
        "`python3 -m scripts.gen_permission_bridge --write`"
    )


def test_the_generator_reads_the_repo_it_guards():
    # A generator pointed at the wrong file would pass vacuously: this
    # pins that ROLES is the module the rest of the suite imports.
    from capabilities.permissions import roles
    assert os.path.realpath(_gen().ROLES) == os.path.realpath(roles.__file__)


def test_an_alias_is_never_installed_over_a_live_field():
    from capabilities.permissions.roles import FeatureSet, LEGACY_TO_CANONICAL
    fields = set(FeatureSet.__dataclass_fields__)
    for legacy, canonical in LEGACY_TO_CANONICAL.items():
        assert legacy not in fields, legacy
        assert canonical in fields, (legacy, canonical)
