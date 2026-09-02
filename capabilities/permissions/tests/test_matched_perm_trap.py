"""``_matched_perm`` is a WIDTH signal smuggled through a gate.

``require_permission_any`` records which flag it matched, and routes
read that to decide narrowing: ``_matched_perm == "can_X_vehicle"``
means "the wide flag was absent".  So swapping such a gate for the
canonical single-flag ``require_permission`` silently deletes the
signal — the gate still admits exactly the same callers, and every
narrow one then sees the WHOLE account.

That is what happened here: the events/location/routes/parking sweep
turned four pair gates into view verbs and a driver's heatmap went
from 2 rows to 3.  A route test caught it, but only because that
route had one.

This guard makes the coupling explicit and refuses the combination
outright, so the remaining families cannot repeat it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("ENCRYPTION_KEY", "")

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests._repo import REPO  # noqa: E402


_SRC = [p for p in (list((REPO / "features").rglob("*.py"))
                    + list((REPO / "capabilities").rglob("*.py")))
        if "/tests/" not in str(p) and "node_modules" not in str(p)]

#: BOTH access forms — ``user["_matched_perm"]`` and the far more
#: common ``user.get("_matched_perm")``.  The first draft matched only
#: the subscript form, found zero reads repo-wide, and passed
#: vacuously over the very bug it was written for.
_READ = re.compile(
    r'_matched_perm"\s*[\])]?\s*[!=]=\s*"(can_[a-z_]+)"')
#: only this dependency populates ``_matched_perm``
_ANY_GATE = re.compile(r"require_permission_any\(([^)]*)\)", re.S)


def test_no_module_reads_a_matched_perm_its_gates_no_longer_set():
    """A module that compares _matched_perm against a flag must still
    have a gate that CAN match that flag — i.e. a require_permission_any
    naming it.  Otherwise the comparison is dead and its narrowing is
    silently gone."""
    broken: list[str] = []
    for path in _SRC:
        src = path.read_text(encoding="utf-8", errors="ignore")
        wanted = set(_READ.findall(src))
        if not wanted:
            continue
        # The gate may live in the sibling router when a service takes
        # ``deps`` — check the whole feature/capability package.
        pkg_src = "".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in path.parent.rglob("*.py") if "/tests/" not in str(p))
        # Look for the CALL that can set it, not for the flag's name:
        # a first draft searched the package text and passed on
        # docstring mentions, so the mutation that reproduced the real
        # bug stayed green.  Only require_permission_any populates
        # _matched_perm (interfaces/api/deps.py).
        gates = " ".join(_ANY_GATE.findall(pkg_src))
        for flag in wanted:
            if f'"{flag}"' not in gates:
                broken.append(f"{path.relative_to(REPO)}: reads "
                              f"_matched_perm == {flag!r}, but no "
                              f"require_permission_any in the package "
                              f"names that flag — the narrowing is dead")
    assert not broken, "\n".join(broken)


def test_the_width_helper_is_the_migration_target_for_these_reads():
    """Documents the fix so the next family does not re-derive it:
    ``_matched_perm == "can_X_vehicle"`` means exactly "the wide grant
    is absent", which is what member_unit_scope answers — plus the
    member override the flags cannot express."""
    import inspect
    from capabilities.permissions import scope
    from interfaces.api import deps
    # The two-claim logic lives in ONE place (the shared core), and
    # the API adapter delegates to it — one implementation per rule,
    # or the copies drift.
    core = inspect.getsource(scope.unit_width)
    assert "PAIRED_UNIT_FEATURES[feature]" in core
    assert "can_for_account(" in core
    assert "unit_width(" in inspect.getsource(deps.member_unit_scope)
