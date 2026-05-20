"""Tests for the RBAC permission system."""

import os
import pytest
from unittest.mock import patch

from adapters.storage import Role
from capabilities.iam.permissions import (
    FeatureSet,
    get_permissions,
    can,
    role_display,
)


# ── Role hierarchy ────────────────────────────────────────────────

class TestRolePermissions:
    """Verify the role→feature matrix is correct and consistent."""

    def test_all_roles_have_permissions(self):
        for role in Role:
            perms = get_permissions(role)
            assert isinstance(perms, FeatureSet), f"Missing perms for {role}"

    def test_unknown_role_returns_empty(self):
        perms = get_permissions("nonexistent")
        assert perms == FeatureSet()

    # ── Owner: full access ────────────────────────────────────────

    def test_owner_has_all_permissions(self):
        perms = get_permissions(Role.OWNER)
        for field_name in FeatureSet.__dataclass_fields__:
            assert getattr(perms, field_name) is True, (
                f"Owner should have {field_name}=True"
            )

    # ── Admin: no company management ──────────────────────────────

    def test_admin_cannot_manage_companies(self):
        assert not can(Role.ADMIN, "can_manage_companies")
        assert not can(Role.ADMIN, "can_manage_account")

    def test_admin_can_manage_users(self):
        assert can(Role.ADMIN, "can_manage_users")
        assert can(Role.ADMIN, "can_invite")

    def test_admin_has_all_fleet_features(self):
        for feat in ("can_faults", "can_critical", "can_fuel", "can_vehicle_all"):
            assert can(Role.ADMIN, feat), f"Admin should have {feat}"

    # ── Fleet Manager: no management ──────────────────────────────

    def test_fleet_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies"):
            assert not can(Role.FLEET, feat), f"Fleet should NOT have {feat}"

    def test_fleet_has_fleet_features(self):
        for feat in ("can_faults", "can_critical", "can_fuel", "can_vehicle_all",
                      "can_maintenance_all", "can_scorecard_all"):
            assert can(Role.FLEET, feat), f"Fleet should have {feat}"

    # ── Dispatcher: limited fleet access ──────────────────────────

    def test_dispatcher_no_faults(self):
        assert not can(Role.DISPATCHER, "can_faults")
        assert not can(Role.DISPATCHER, "can_critical")

    def test_dispatcher_has_fuel_and_location(self):
        assert can(Role.DISPATCHER, "can_fuel")
        assert can(Role.DISPATCHER, "can_vehicle_all")
        assert can(Role.DISPATCHER, "can_location_map")

    def test_dispatcher_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies"):
            assert not can(Role.DISPATCHER, feat)

    def test_dispatcher_no_cost_features(self):
        assert not can(Role.DISPATCHER, "can_fuel_cost")
        assert not can(Role.DISPATCHER, "can_cost_per_mile")

    # ── Driver: own-only access ───────────────────────────────────

    def test_driver_own_truck_only(self):
        assert can(Role.DRIVER, "can_vehicle_own")
        assert not can(Role.DRIVER, "can_vehicle_all")

    def test_driver_own_alerts_only(self):
        assert can(Role.DRIVER, "can_alerts_own")
        assert not can(Role.DRIVER, "can_alerts_all")

    def test_driver_no_fleet_reports(self):
        for feat in ("can_faults", "can_critical", "can_fuel"):
            assert not can(Role.DRIVER, feat)

    def test_driver_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies",
                      "can_manage_account"):
            assert not can(Role.DRIVER, feat)

    def test_driver_has_auto_reports(self):
        assert can(Role.DRIVER, "can_digest")

    def test_driver_own_geofence(self):
        assert can(Role.DRIVER, "can_geofence_own")
        assert not can(Role.DRIVER, "can_geofence_all")


# ── `can()` edge cases ───────────────────────────────────────────

class TestCanFunction:

    def test_nonexistent_feature_returns_false(self):
        assert not can(Role.OWNER, "can_fly_to_moon")

    def test_returns_bool(self):
        result = can(Role.OWNER, "can_faults")
        assert isinstance(result, bool)


# ── Role parsing ──────────────────────────────────────────────────

class TestRoleParsing:

    def test_from_str_valid(self):
        assert Role.from_str("owner") == Role.OWNER
        assert Role.from_str("fleet") == Role.FLEET
        assert Role.from_str("driver") == Role.DRIVER

    def test_from_str_case_insensitive(self):
        assert Role.from_str("  Owner  ") == Role.OWNER

    def test_from_str_invalid_raises(self):
        with pytest.raises(ValueError, match="Unknown role"):
            Role.from_str("superadmin")


# ── System Owner ──────────────────────────────────────────────────

class TestSystemOwner:

    def test_system_owner_check(self):
        with patch.dict(os.environ, {"SYSTEM_OWNER_IDS": "111,222"}):
            from capabilities.iam.permissions import _parse_system_owners
            ids = _parse_system_owners()
            assert 111 in ids
            assert 222 in ids
            assert 999 not in ids

    def test_empty_system_owners(self):
        with patch.dict(os.environ, {"SYSTEM_OWNER_IDS": ""}):
            from capabilities.iam.permissions import _parse_system_owners
            ids = _parse_system_owners()
            assert len(ids) == 0

    def test_role_display_all_roles(self):
        for role in Role:
            display = role_display(role)
            assert isinstance(display, str)
            assert len(display) > 0


# ── Privilege escalation prevention ───────────────────────────────

class TestPrivilegeEscalation:
    """Verify that lower roles never have more permissions than higher ones.

    The strict hierarchy applies to Owner → Admin → Fleet Manager.
    Dispatcher and Driver are lateral specializations with distinct
    feature profiles, so they are tested separately.
    """

    STRICT_HIERARCHY = [Role.OWNER, Role.ADMIN, Role.FLEET]

    def test_management_restricted_to_top_roles(self):
        """Only owner/admin should manage users; only owner manages companies."""
        assert can(Role.OWNER, "can_manage_companies")
        for role in [Role.ADMIN, Role.FLEET, Role.DISPATCHER, Role.DRIVER]:
            assert not can(role, "can_manage_companies")

    def test_strict_hierarchy_subset(self):
        """Each role in Owner→Admin→Fleet Mgr is a superset of the one below."""
        for i in range(len(self.STRICT_HIERARCHY) - 1):
            higher = get_permissions(self.STRICT_HIERARCHY[i])
            lower = get_permissions(self.STRICT_HIERARCHY[i + 1])
            for field_name in FeatureSet.__dataclass_fields__:
                if not getattr(higher, field_name):
                    assert not getattr(lower, field_name), (
                        f"{self.STRICT_HIERARCHY[i+1].value} has {field_name} "
                        f"but {self.STRICT_HIERARCHY[i].value} does not"
                    )

    def test_dispatcher_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies"):
            assert not can(Role.DISPATCHER, feat)

    def test_driver_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies"):
            assert not can(Role.DRIVER, feat)

    def test_driver_no_fleet_wide_access(self):
        """Driver should never have *_all permissions."""
        perms = get_permissions(Role.DRIVER)
        for field_name in FeatureSet.__dataclass_fields__:
            if field_name.endswith("_all"):
                assert not getattr(perms, field_name), (
                    f"Driver should not have fleet-wide {field_name}"
                )


class TestPermSsotDriftDetection:
    """Guard against drift between the canonical FeatureSet and the
    dashboard's Role Permissions admin UI.

    Every flag in ``FeatureSet`` must be customizable from the
    dashboard's RolePermissions.tsx ``PERM_GROUPS`` constant.  When a
    new flag is added to ``permissions.py`` but the admin UI is not
    updated, this test fails — preventing the regression that was
    found 2026-05-19 (4 flags were defined but invisible in the UI).

    A flag is considered "exposed" when its string name appears as a
    quoted literal inside the PERM_GROUPS array of RolePermissions.tsx.
    This is a substring match rather than an AST parse so the test
    works against the .tsx source without a TS toolchain.
    """

    def test_every_flag_appears_in_dashboard_perm_groups(self):
        import os
        import re
        from capabilities.iam.permissions import FeatureSet

        repo_root = os.path.dirname(os.path.dirname(__file__))
        tsx_path = os.path.join(
            repo_root,
            "interfaces/dashboard/src/pages/admin/RolePermissions.tsx",
        )
        with open(tsx_path) as f:
            content = f.read()

        # Extract just the PERM_GROUPS block so we don't accidentally
        # match a flag that's referenced elsewhere in the file
        # (e.g. an isolated TypeScript type reference).
        block = re.search(
            r"const PERM_GROUPS[^=]*=\s*\[(.+?)\];\s*\n",
            content,
            re.DOTALL,
        )
        assert block, "Could not locate PERM_GROUPS in RolePermissions.tsx"
        groups_src = block.group(1)

        flag_names = [f.name for f in FeatureSet.__dataclass_fields__.values()]
        missing = [n for n in flag_names if f"'{n}'" not in groups_src and f'"{n}"' not in groups_src]

        assert not missing, (
            f"FeatureSet flags missing from dashboard's PERM_GROUPS: {sorted(missing)}\n"
            "Add them to interfaces/dashboard/src/pages/admin/RolePermissions.tsx "
            "so admins can customize them per account."
        )
