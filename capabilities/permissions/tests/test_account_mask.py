"""One account-level mask: departments AND feature switches, applied by
the resolver, read by nobody else.

Until 2026-09-07 ``accounts.coaching_enabled`` was a kill-switch seven
places read on their own — the service, the nightly job, the bot, /me,
the nav twice, the page — every one a path around the resolver, so a
plan mask would have become a fourth way to switch a feature off.  Now
the switch masks the feature's flags exactly as a disabled department
does, and the column has one reader.
"""

from __future__ import annotations

import ast
import os
from types import SimpleNamespace

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.modules import (
    ACCOUNT_FEATURE_SWITCHES, account_mask, feature_available, switched_off_flags,
)
from capabilities.permissions.registry import REGISTRY
from capabilities.permissions.roles import ROLE_PERMISSIONS, Role
from tests._repo import REPO, is_test_path


def _acct(disabled_modules: str = "", coaching_enabled: bool = True):
    return SimpleNamespace(disabled_modules=disabled_modules, coaching_enabled=coaching_enabled)


def test_every_switch_names_a_registry_feature_with_flags():
    for feature_id in ACCOUNT_FEATURE_SWITCHES:
        assert feature_id in REGISTRY, feature_id
        assert REGISTRY[feature_id].flags, f"{feature_id} has no flags to mask"


def test_the_switch_off_forces_the_feature_flags_off_and_nothing_else():
    fs = ROLE_PERMISSIONS[Role.SAFETY]
    assert fs.can_view_coaching and fs.can_manage_coaching   # the seed holds both
    masked = account_mask(fs, _acct(coaching_enabled=False))
    assert not masked.can_view_coaching and not masked.can_manage_coaching
    assert switched_off_flags(_acct(coaching_enabled=False)) == set(REGISTRY["coaching"].flags)
    # every other field untouched
    for name in fs.__dataclass_fields__:
        if name not in REGISTRY["coaching"].flags:
            assert getattr(masked, name) == getattr(fs, name), name


def test_the_switch_on_masks_nothing_and_the_department_mask_still_applies():
    fs = ROLE_PERMISSIONS[Role.SAFETY]
    assert account_mask(fs, _acct()) == fs
    # coaching is HR + Safety: one department off keeps it, both off drop it
    assert account_mask(fs, _acct("hr")).can_view_coaching
    assert not account_mask(fs, _acct("hr,safety")).can_view_coaching
    assert account_mask(fs, None) == fs                       # no row → unmasked (fail-open)


def test_feature_available_is_the_same_answer_for_a_caller_with_no_user():
    assert feature_available(_acct(), "coaching")
    assert not feature_available(_acct(coaching_enabled=False), "coaching")
    assert not feature_available(_acct("hr,safety"), "coaching")
    assert feature_available(_acct("hr"), "coaching")
    assert not feature_available(None, "coaching")            # no row → closed
    # a feature with no switch follows its departments alone
    assert feature_available(_acct(coaching_enabled=False), "driver_pay")
    assert not feature_available(_acct("accounting"), "driver_pay")
    # a core feature (no toggleable department, no switch) is always available
    core = next(e.id for e in REGISTRY.values()
                if set(e.modules) == {"core"} and e.id not in ACCOUNT_FEATURE_SWITCHES)
    assert feature_available(_acct("fleet,dispatch,safety,hr,accounting"), core)


# ── the column has one reader ──────────────────────────────────────

_SWITCH_COLUMNS = set(ACCOUNT_FEATURE_SWITCHES.values()) | {"payroll_enabled"}
# Storage may spell the column (schema, migrations, the row mapper, the
# dataclass, the update allow-list); the ONE reader is modules.py.
_STORAGE = (
    "adapters/storage/platform_schema.py",
    "adapters/storage/platform_migrations.py",
    "adapters/storage/core.py",
    "adapters/storage/models.py",
    "adapters/storage/accounts.py",
    "capabilities/permissions/modules.py",
)
_ROOTS = ("adapters", "capabilities", "features", "interfaces", "infra")


def _python_files():
    for root in _ROOTS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, root)):
            dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
            for f in filenames:
                if f.endswith(".py"):
                    p = os.path.join(dirpath, f)
                    if not is_test_path(p):
                        yield p


def _reads_switch_column(src: str) -> list[str]:
    """READS of a switch column: ``x.col``, ``getattr(x, "col")``,
    ``x["col"]``.  A dict key in a literal is not a read — /me still
    EMITS ``coaching_enabled`` for the page, derived through the mask's
    own helper."""
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and node.attr in _SWITCH_COLUMNS:
            hits.append(f"{node.lineno}: .{node.attr}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant)
              and node.args[1].value in _SWITCH_COLUMNS):
            hits.append(f"{node.lineno}: getattr(…, {node.args[1].value!r})")
        elif (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
              and node.slice.value in _SWITCH_COLUMNS):
            hits.append(f"{node.lineno}: [{node.slice.value!r}]")
    return hits


def test_no_production_code_reads_a_switch_column_beside_the_mask():
    offenders = {}
    for p in _python_files():
        rel = os.path.relpath(p, REPO)
        if rel in _STORAGE:
            continue
        hits = _reads_switch_column(open(p, encoding="utf-8").read())
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "a switch column is read outside the mask — the resolver already "
        "masks the feature's flags; ask permissions (or feature_available):\n"
        + "\n".join(f"  {k}: {v}" for k, v in offenders.items()))


def test_the_dashboard_has_no_second_path_beside_the_permission():
    """The nav and the palette filtered Driver Pay / Coaching on two /me
    booleans; the pages may still explain a switched-off feature, the
    routers may not hide on it."""
    for rel in ("interfaces/dashboard/src/components/Sidebar.tsx",
                "interfaces/dashboard/src/components/shell/CommandPalette.tsx"):
        src = open(os.path.join(REPO, rel), encoding="utf-8").read()
        for col in _SWITCH_COLUMNS:
            assert f"user?.{col}" not in src and f"user.{col}" not in src, (rel, col)
