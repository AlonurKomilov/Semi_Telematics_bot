"""Every ``FeatureSet`` field has exactly ONE user-facing home.

The 2026-07-29 matrix census found four flags that surfaced nowhere —
they turned out to be the then-derived service flags, absent by
design, but nothing enforced the difference between "absent by design"
and "someone forgot".  This guard does: a new permission flag must land
in the staff matrix, the Driver panel, or
the explicit exempt list below — anything else fails here with a
message saying where to put it.

Pure file-parsing, no DB — runs in milliseconds.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import fields

from capabilities.permissions.roles import FeatureSet
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted

_MATRIX_FILE = os.path.join(
    str(_REPO),
    "interfaces", "dashboard", "src", "features", "permissions",
    "permRows.ts",
)

# Flags with a live UI that deliberately is NOT the Permissions matrix.
# Add here ONLY with a pointer to the surface that edits/serves the flag.
_EXEMPT: dict[str, str] = {
    # Managed on the Alerts → Group delivery page (per-persona bot rows);
    # manager-tier hard-check, deliberately outside the matrix + the
    # config family (capabilities/config/docs/ARCHITECTURE.md).
    "can_manage_role_bot": "features/alerts/GroupDelivery.tsx",
}


def _matrix_keys() -> set[str]:
    with open(_MATRIX_FILE, encoding="utf-8") as f:
        src = f.read()
    # Row declarations only — key:/allKey:/vehicleKey: '<flag>' — so flags
    # merely MENTIONED in comments don't count as surfaced.  Text-based on
    # purpose (no TS parser here), which PINS a style: single quotes, value
    # on the same line as the property.  If a formatter changes that, this
    # regex under-counts and the guard fails with a confusing "no home"
    # message — update the regex, not the rows.
    return set(re.findall(r"(?:key|allKey|vehicleKey):\s*'(can_[a-z_]+)'", src))


class TestPermissionSurface:
    def test_every_field_has_exactly_one_home(self):
        declared = {f.name for f in fields(FeatureSet)}
        surfaced = _matrix_keys()

        missing = declared - surfaced - set(_EXEMPT)
        assert not missing, (
            f"FeatureSet fields with NO user-facing home: {sorted(missing)}. "
            "Add a matrix/driver-panel row in Permissions.tsx, or list the "
            "in _EXEMPT here with a pointer to its UI."
        )

    def test_no_home_claims_a_nonexistent_or_derived_flag(self):
        declared = {f.name for f in fields(FeatureSet)}
        surfaced = _matrix_keys()

        ghosts = surfaced - declared
        assert not ghosts, (
            f"Permissions.tsx rows reference flags FeatureSet doesn't "
            f"declare: {sorted(ghosts)}"
        )
