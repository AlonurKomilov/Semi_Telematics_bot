"""Manager tier — the per-user seniority layered on a base role.

"Manager" is NOT a role: it is a per-user ``is_manager`` tier applied on
top of the base role (MANAGER_GRANTS).  A recruiting team lead is a
``recruiter`` + ``is_manager``, gaining can_invite and
can_manage_carrier_directory.

Split from features/settings/tests/test_invite_policy.py, which asserted two subjects that
were never setup for each other: this half touches only the RBAC layer
and never calls invite_authorized.  The other half — who a manager may
INVITE — is owned by the invites feature and lives with it.
"""

from dataclasses import asdict

from adapters.storage.models import Role
from capabilities.permissions.roles import (
    ROLE_PERMISSIONS, MANAGER_GRANTS, apply_manager_grants,
    role_supports_manager, get_permissions, role_tier, TIER_GRANTS,
)


class TestManagerTier:
    def test_manager_is_not_a_role(self):
        """The retired recruiter_manager role must be gone; any stored value
        folds back to the base recruiter role (the tier lives on the user)."""
        assert not hasattr(Role, "RECRUITER_MANAGER")
        assert Role.from_str("recruiter_manager") is Role.RECRUITER

    def test_manager_grant_is_recruiter_plus_exactly_four_flags(self):
        """A recruiter MANAGER = the recruiter baseline PLUS exactly
        can_invite + can_manage_carrier_directory + can_manage_role_bot
        (role-bot roster management is a manager right — 8c0edbb) +
        can_manage_config_role (team-default page config, the config
        family's role scope).  The read flag can_carrier_directory is on
        BOTH, so it is NOT a difference."""
        base = get_permissions(Role.RECRUITER)
        mgr = apply_manager_grants(base, Role.RECRUITER, True)
        b, m = asdict(base), asdict(mgr)
        diff = {k: (b[k], m[k]) for k in m if b[k] != m[k]}
        assert diff == {
            "can_invite": (False, True),
            "can_manage_carrier_directory": (False, True),
            "can_manage_role_bot": (False, True),
            "can_manage_config_role": (False, True),
        }, diff
        assert base.can_carrier_directory is True and mgr.can_carrier_directory is True

    def test_employee_and_non_capable_roles_are_no_ops(self):
        base = get_permissions(Role.RECRUITER)
        assert apply_manager_grants(base, Role.RECRUITER, False) == base   # employee
        drv = get_permissions(Role.DRIVER)
        assert apply_manager_grants(drv, Role.DRIVER, True) == drv         # no tier

    def test_manager_capability_map(self):
        # Every department role has a manager tier now; only DRIVER (miniapp
        # persona) and OWNER (a seat, managed via co-owner) have none.
        for r in (Role.RECRUITER, Role.ADMIN, Role.FLEET, Role.SAFETY,
                  Role.DISPATCHER, Role.HR, Role.ACCOUNTING):
            assert role_supports_manager(r) is True, r
        for r in (Role.DRIVER, Role.OWNER):
            assert role_supports_manager(r) is False, r
        assert MANAGER_GRANTS[Role.RECRUITER] == frozenset(
            {"can_invite", "can_manage_carrier_directory",
             "can_manage_role_bot", "can_manage_config_role"}
        )

    def test_department_tier_seed_grants(self):
        """Each team-lead tier seeds exactly the agreed delta (owners retune
        per-account afterward — these are only the defaults)."""
        # Risk Summary left the fleet tier when the fold seeded it on the
        # base fleet role (owner decision 2026-09-04: default ON).
        assert TIER_GRANTS[Role.FLEET].grants == frozenset(
            {"can_invite", "can_manage_work_hours", 
             "can_manage_role_bot", "can_manage_config_role"})
        # Safety's scoring-config ownership rode can_manage_scorecard_rules
        # until that flag folded into the account-scope config permission
        # (capabilities/config/docs/ARCHITECTURE.md).
        assert TIER_GRANTS[Role.SAFETY].grants == frozenset(
            {"can_manage_config_all", "can_invite", "can_manage_role_bot",
             "can_manage_config_role"})
        assert TIER_GRANTS[Role.DISPATCHER].grants == frozenset(
            {"can_manage_work_hours", "can_manage_poi_layers", "can_invite",
             "can_manage_role_bot", "can_manage_config_role"})
        # Onboarding left this tier for the HR BASE role (2026-07-30): an
        # HR employee who administers the roster finishes the hire the
        # recruiter approved, so it can't be a manager-only grant.
        assert TIER_GRANTS[Role.HR].grants == frozenset(
            {"can_manage_work_hours", "can_manage_applications",
             "can_manage_role_bot", "can_manage_config_role"})
        assert TIER_GRANTS[Role.ACCOUNTING].grants == frozenset(
            {"can_manage_work_orders", "can_manage_maintenance",
             "can_manage_parts", "can_manage_service_tasks", "can_manage_role_bot",
             "can_manage_config_role"})
        # Every seed grant must be flags the BASE role lacks (a real delta).
        from dataclasses import asdict
        for role, tier in TIER_GRANTS.items():
            base = asdict(ROLE_PERMISSIONS[role])
            for flag in tier.grants:
                assert base.get(flag) is False, f"{role.value}: {flag} already on base"

    def test_admin_full_tier(self):
        """Admin has a Full/Standard tier: Full admin adds the account-config
        flags a standard admin lacks (billing is NOT a delta — admins have it)."""
        t = role_tier(Role.ADMIN)
        assert t is not None
        assert t.senior_label == "Full admin" and t.base_label == "Standard admin"
        assert role_supports_manager(Role.ADMIN) is True
        base = get_permissions(Role.ADMIN)
        full = apply_manager_grants(base, Role.ADMIN, True)
        for flag in ("can_manage_integrations", "can_manage_storage",
                     "can_manage_permissions", "can_manage_account",
                     "can_manage_work_hours"):
            assert getattr(base, flag) is False and getattr(full, flag) is True, flag
        assert base.can_manage_billing is True   # already a standard-admin power
        assert set(TIER_GRANTS) == {
            Role.RECRUITER, Role.ADMIN, Role.FLEET, Role.SAFETY,
            Role.DISPATCHER, Role.HR, Role.ACCOUNTING,
        }
