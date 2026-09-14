"""The live map's flag was renamed (``can_view_location`` →
``can_view_live_map``, cae597b8) and nothing bridged the rows already
on disk under the old name.

What that cost: a stored ``{"can_view_location": false}`` — an owner's
revocation — fell through the resolver's unknown-key filter, and the
role resolved to its SEED, which grants the map.  The revocation was
silently undone for every affected role, and the next save of the
matrix would have written the wrong answer back permanently.

The alias in ``LEGACY_TO_CANONICAL`` is the bridge: the stored key folds
onto the canonical field BEFORE the filter, so the stored value wins
over the seed exactly as it did before the rename.  These pin that,
through the real resolver and not a helper — the assertion that would
have caught this, and catches the next rename.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "x" * 64)

import pytest

from adapters.storage import Role
from capabilities.permissions import roles

OLD, NEW = "can_view_location", "can_view_live_map"


def _a_role_whose_seed_grants_the_map() -> Role:
    for r in Role:
        if r != Role.OWNER and getattr(roles.get_permissions(r), NEW, False):
            return r
    raise AssertionError("no non-owner role seeds the live map")


def test_the_old_key_folds_onto_the_new_one_and_a_stored_no_stays_no():
    assert roles.LEGACY_TO_CANONICAL[OLD] == NEW
    assert roles.normalize_stored_perm_keys({OLD: False})[NEW] is False
    assert roles.normalize_stored_perm_keys({OLD: True})[NEW] is True
    # both spellings on one row: a grant from either side grants (the
    # bridge-safe rule normalize_stored_perm_keys documents)
    assert roles.normalize_stored_perm_keys({OLD: False, NEW: True})[NEW] is True
    # and a stale reader of the old attribute still gets the truth
    assert roles.FeatureSet(**{NEW: False}).can_view_location is False


@pytest.mark.asyncio
async def test_a_revocation_stored_under_the_old_key_still_revokes(pg_db, monkeypatch):
    """Through the resolver, from a row written before the rename."""
    db = pg_db
    monkeypatch.setattr("infra.platform._db", db)
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    role = _a_role_whose_seed_grants_the_map()
    acct = await db.create_account("Old Key Co")
    # the row as the pre-rename matrix wrote it: the setter stores the
    # dict as given, so the old key lands on disk exactly as it did then
    await db.set_role_permissions(acct.id, role.value, {OLD: False})
    roles.invalidate_permissions_cache()

    fs = await roles.get_account_permissions(role, acct.id, None)
    assert getattr(fs, NEW) is False, f"{role.value}: the owner's revocation was undone by the rename"
    # nothing else on the row, so nothing else moved off the seed
    seed = roles.get_permissions(role)
    assert fs.can_view_vehicles == seed.can_view_vehicles
