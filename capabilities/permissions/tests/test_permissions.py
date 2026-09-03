"""Tests for the RBAC permission system."""

import os
import pytest
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted
from unittest.mock import patch

from adapters.storage import Role
from capabilities.permissions.roles import (
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

    def test_every_role_in_hierarchy_display_emoji(self):
        """Every Role enum member must appear in ROLE_HIERARCHY,
        ROLE_DISPLAY and ROLE_EMOJI.

        Regression guard: hr + accounting were once missing from
        ROLE_HIERARCHY (→ rank 0 → couldn't invite anyone) and from the
        display/emoji dicts.  A new persona must never ship half-wired.
        """
        from capabilities.permissions.roles import (
            ROLE_HIERARCHY, ROLE_DISPLAY, ROLE_EMOJI, role_rank,
        )
        for role in Role:
            assert role.value in ROLE_HIERARCHY, (
                f"{role.value} missing from ROLE_HIERARCHY (would rank 0)"
            )
            assert role_rank(role) > 0, f"{role.value} has rank 0"
            assert role in ROLE_DISPLAY, f"{role.value} missing from ROLE_DISPLAY"
            assert role in ROLE_EMOJI, f"{role.value} missing from ROLE_EMOJI"

    def test_department_roles_can_invite_driver(self):
        """The exact bug we fixed: department roles must be able to
        invite a driver (rank > driver), not be locked out at rank 0.
        Recruiter (driver acquisition) is rank 2 for exactly this — it
        invites drivers once an owner enables can_invite in the matrix."""
        from capabilities.permissions.roles import validate_invite_role
        for dept in (Role.HR, Role.ACCOUNTING, Role.RECRUITER):
            ok, reason = validate_invite_role(dept, Role.DRIVER)
            assert ok, f"{dept.value} should invite driver, got {reason!r}"
            # …but not a peer or higher.
            ok2, _ = validate_invite_role(dept, Role.SAFETY)
            assert not ok2, f"{dept.value} must not invite safety (peer/higher)"

    def test_unknown_role_returns_empty(self):
        perms = get_permissions("nonexistent")
        assert perms == FeatureSet()

    # ── Owner: full access ────────────────────────────────────────

    def test_owner_has_all_permissions(self):
        from capabilities.permissions.roles import (
            DARK_FEATURE_FIELDS, DERIVED_SERVICE_FIELDS,
            ROLE_PERMISSIONS, TIER_GRANTS,
        )
        # Tier-only flags are DELIBERATELY never a base-role seed
        # (roles.py: can_manage_role_bot — "never a base-role seed; the
        # API re-checks is_manager+role regardless").  Computed, not
        # hardcoded: a tier flag that some base role DOES seed stops
        # being exempt automatically.
        base_seeded = {
            f for fs in ROLE_PERMISSIONS.values()
            for f in FeatureSet.__dataclass_fields__
            if getattr(fs, f)
        }
        tier_only = {
            f for t in TIER_GRANTS.values() for f in t.grants
        } - base_seeded
        perms = get_permissions(Role.OWNER)
        # The derived service surfaces (Alerts inbox, AI assistant): since
        # the verb/scope flip both inbox flags say one thing — the inbox
        # exists iff Vehicles is visible; WIDTH is per member — so the
        # owner holds both.  Assert those explicitly; blanket-check
        # every other field stays True for the owner.
        for field_name in FeatureSet.__dataclass_fields__:
            if (field_name in DERIVED_SERVICE_FIELDS
                    or field_name in tier_only
                    or field_name in DARK_FEATURE_FIELDS):
                continue
            assert getattr(perms, field_name) is True, (
                f"Owner should have {field_name}=True"
            )
        assert perms.can_alerts_all is True        # account-wide Alerts inbox
        assert perms.can_alerts_vehicle is True   # inbox exists; width is per member now
        assert perms.can_ai_chat is True           # always-on AI service

    def test_dark_features_seed_nobody_but_stay_grantable(self):
        """The dark-feature contract (owner decision 2026-07-30): a dark
        flag is seeded to NO role — the owner included — so nothing is
        marketed before it's ready.  Two guards keep it honest: every
        role defaults to False, AND the flag must never be
        owner-protected (that would turn dark into locked-out, since
        the matrix cell is the only way to ever grant it)."""
        from capabilities.permissions.roles import (
            DARK_FEATURE_FIELDS, OWNER_PROTECTED_PERMS, ROLE_PERMISSIONS,
        )
        for flag in DARK_FEATURE_FIELDS:
            for role, fs in ROLE_PERMISSIONS.items():
                assert getattr(fs, flag) is False, (
                    f"dark feature {flag} must not be seeded to {role}"
                )
            assert flag not in OWNER_PROTECTED_PERMS, (
                f"dark feature {flag} is owner-protected — that is a "
                "lockout, not a launch switch"
            )

    # ── Admin: no company management ──────────────────────────────

    def test_admin_cannot_manage_companies(self):
        assert not can(Role.ADMIN, "can_manage_companies")
        assert not can(Role.ADMIN, "can_manage_account")

    def test_admin_can_manage_users(self):
        assert can(Role.ADMIN, "can_manage_users")
        assert can(Role.ADMIN, "can_invite")

    def test_admin_has_all_fleet_features(self):
        for feat in ("can_faults", "can_fuel", "can_vehicle_all"):
            assert can(Role.ADMIN, feat), f"Admin should have {feat}"

    # ── Fleet Manager: no management ──────────────────────────────

    def test_fleet_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies"):
            assert not can(Role.FLEET, feat), f"Fleet should NOT have {feat}"

    def test_fleet_has_fleet_features(self):
        for feat in ("can_faults", "can_fuel", "can_vehicle_all",
                      "can_maintenance_all", "can_scorecard_all"):
            assert can(Role.FLEET, feat), f"Fleet should have {feat}"

    # ── Dispatcher: limited fleet access ──────────────────────────

    def test_dispatcher_no_faults(self):
        assert not can(Role.DISPATCHER, "can_faults")

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
        # The driver opens Vehicles; WIDTH (own truck) is Team
        # Management's built-in default, not a flag any more.
        from capabilities.permissions.fold import builtin_width
        assert can(Role.DRIVER, "can_view_vehicles")
        assert builtin_width("driver") == "assigned"

    def test_driver_own_alerts_only(self):
        # The inbox exists for anyone who sees vehicles; both derived
        # flags say so, and width is per member.
        assert can(Role.DRIVER, "can_alerts_vehicle")
        assert can(Role.DRIVER, "can_alerts_all")

    def test_driver_no_fleet_reports(self):
        for feat in ("can_faults", "can_fuel"):
            assert not can(Role.DRIVER, feat)

    def test_driver_no_management(self):
        for feat in ("can_invite", "can_manage_users", "can_manage_companies",
                      "can_manage_account"):
            assert not can(Role.DRIVER, feat)

    def test_driver_has_auto_reports(self):
        assert can(Role.DRIVER, "can_digest")

    def test_driver_own_geofence(self):
        assert can(Role.DRIVER, "can_geofence_vehicle")
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
            from capabilities.permissions.roles import _parse_system_owners
            ids = _parse_system_owners()
            assert 111 in ids
            assert 222 in ids
            assert 999 not in ids

    def test_empty_system_owners(self):
        with patch.dict(os.environ, {"SYSTEM_OWNER_IDS": ""}):
            from capabilities.permissions.roles import _parse_system_owners
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
        """A driver holds no MANAGE verb on any unit feature, and their
        width is 'assigned' by the built-in layer — the two facts that
        "no fleet-wide access" now decomposes into."""
        from capabilities.permissions.fold import builtin_width
        from capabilities.permissions.roles import UNIT_FEATURES
        perms = get_permissions(Role.DRIVER)
        for _noun, (_view, manage) in UNIT_FEATURES.items():
            if manage:
                assert not getattr(perms, manage), manage
        assert not perms.can_manage_vehicles
        assert builtin_width("driver") == "assigned"
    def test_every_flag_appears_in_dashboard_perm_groups(self):
        import os
        import re
        from capabilities.permissions.roles import FeatureSet

        repo_root = str(_REPO)
        # PERM_GROUPS moved to permRows.ts in the RoleLens refactor —
        # the guard follows the block, not the filename.
        tsx_path = os.path.join(
            repo_root,
            "interfaces/dashboard/src/features/permissions/permRows.ts",
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
        assert block, "Could not locate PERM_GROUPS in permRows.ts"
        groups_src = block.group(1)
        # The driver's view-own flags are DELIBERATELY not matrix rows —
        # they live in the dedicated Driver self-service panel
        # (DRIVER_PANEL_FLAGS; see the comment beside it).  Scan that
        # block too so the guard mirrors the real UI, not one array.
        # (DRIVER_PANEL_FLAGS itself is a one-line spread of these two.)
        for name in ("DRIVER_TRUCK", "DRIVER_RECORDS"):
            panel = re.search(
                rf"const {name}[^=]*=\s*\[(.+?)\];\s*\n",
                content,
                re.DOTALL,
            )
            if panel:
                groups_src += panel.group(1)

        from capabilities.permissions.roles import (
            DERIVED_SERVICE_FIELDS, ROLE_PERMISSIONS, TIER_GRANTS,
        )
        base_seeded = {
            f for fs in ROLE_PERMISSIONS.values()
            for f in FeatureSet.__dataclass_fields__
            if getattr(fs, f)
        }
        tier_only = {
            f for t in TIER_GRANTS.values() for f in t.grants
        } - base_seeded

        flag_names = [f.name for f in FeatureSet.__dataclass_fields__.values()]
        # Since the matrix flip (stage E of the verb/scope migration) a
        # field is exposed either under its own name or under a canonical
        # verb that WRITES to it: a view tick on a unit pair lands on both
        # halves, a manage tick on the wide half.  Built from the same
        # maps the PUT normalisation uses, so the guard and the write
        # path cannot disagree about what a row edits.
        from capabilities.permissions.roles import LEGACY_TO_CANONICAL
        # Fields are canonical since the flip; a row may still name a
        # field by a legacy alias (the two parked scoped rows do), so a
        # field counts as exposed under its own name or any alias of it.
        exposed_as: dict[str, set[str]] = {n: {n} for n in flag_names}
        for legacy, canonical in LEGACY_TO_CANONICAL.items():
            exposed_as.setdefault(canonical, set()).add(legacy)

        def _exposed(n: str) -> bool:
            return any(f"'{a}'" in groups_src or f'"{a}"' in groups_src
                       for a in exposed_as.get(n, {n}))

        # The derived service surfaces (Alerts inbox, AI assistant) are
        # intentionally NOT matrix rows — they're always-on system services
        # present for every role, shown read-only in the "System Services"
        # panel.  See derive_service_perms; they must NOT be customizable.
        missing = [
            n for n in flag_names
            if n not in DERIVED_SERVICE_FIELDS
            and n not in tier_only
            and not _exposed(n)
        ]

        assert not missing, (
            f"FeatureSet flags missing from dashboard's PERM_GROUPS: {sorted(missing)}\n"
            "Add them to interfaces/dashboard/src/features/permissions/permRows.ts "
            "so admins can customize them per account."
        )
