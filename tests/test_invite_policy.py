"""Manager tier (per-user seniority) + the invites feature's targeting policy.

"Manager" is NOT a role — it's a per-user ``is_manager`` tier layered on the
base role (capabilities/permissions/roles.MANAGER_GRANTS).  A recruiting team
lead is a ``recruiter`` + ``is_manager``, gaining ``can_invite`` and
``can_manage_carrier_directory``.  "Who a manager may invite" is owned by the
invites FEATURE (features/settings/invites.service), not the RBAC layer — a
recruiting manager may build ONLY a recruiter team, never fleet/hr/etc.
"""

from dataclasses import asdict

from adapters.storage.models import Role
from capabilities.permissions.roles import (
    ROLE_PERMISSIONS, MANAGER_GRANTS, apply_manager_grants,
    role_supports_manager, get_permissions, role_tier, TIER_GRANTS,
)
from features.settings.invites.service import invite_authorized, MANAGER_INVITE_ONLY


class TestManagerTier:
    def test_manager_is_not_a_role(self):
        """The retired recruiter_manager role must be gone; any stored value
        folds back to the base recruiter role (the tier lives on the user)."""
        assert not hasattr(Role, "RECRUITER_MANAGER")
        assert Role.from_str("recruiter_manager") is Role.RECRUITER

    def test_manager_grant_is_recruiter_plus_exactly_three_flags(self):
        """A recruiter MANAGER = the recruiter baseline PLUS exactly
        can_invite + can_manage_carrier_directory + can_manage_role_bot
        (role-bot roster management is a manager right — 8c0edbb).  The
        read flag can_carrier_directory is on BOTH, so it is NOT a
        difference."""
        base = get_permissions(Role.RECRUITER)
        mgr = apply_manager_grants(base, Role.RECRUITER, True)
        b, m = asdict(base), asdict(mgr)
        diff = {k: (b[k], m[k]) for k in m if b[k] != m[k]}
        assert diff == {
            "can_invite": (False, True),
            "can_manage_carrier_directory": (False, True),
            "can_manage_role_bot": (False, True),
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
            {"can_invite", "can_manage_carrier_directory", "can_manage_role_bot"}
        )

    def test_department_tier_seed_grants(self):
        """Each team-lead tier seeds exactly the agreed delta (owners retune
        per-account afterward — these are only the defaults)."""
        assert TIER_GRANTS[Role.FLEET].grants == frozenset(
            {"can_invite", "can_manage_work_hours", "can_risk_report_all",
             "can_manage_role_bot"})
        assert TIER_GRANTS[Role.SAFETY].grants == frozenset(
            {"can_manage_scorecard_rules", "can_invite", "can_manage_role_bot"})
        assert TIER_GRANTS[Role.DISPATCHER].grants == frozenset(
            {"can_manage_work_hours", "can_manage_poi_layers", "can_invite",
             "can_manage_role_bot"})
        assert TIER_GRANTS[Role.HR].grants == frozenset(
            {"can_manage_work_hours", "can_manage_applications",
             "can_convert_to_driver", "can_manage_role_bot"})
        assert TIER_GRANTS[Role.ACCOUNTING].grants == frozenset(
            {"can_work_orders_all", "can_maintenance_all",
             "can_parts", "can_service_tasks", "can_manage_role_bot"})
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


class TestInviteTargetingPolicy:
    def test_manager_may_invite_only_recruiter(self):
        # Manager authority is rank-independent: a recruiter-manager (rank 2)
        # may invite a recruiter (rank 2) — normally same-rank is blocked.
        ok, _ = invite_authorized("recruiter", True, "recruiter")
        assert ok is True
        for blocked in ("hr", "fleet", "dispatcher", "accounting", "driver", "safety"):
            ok, reason = invite_authorized("recruiter", True, blocked)
            assert ok is False and reason.startswith("manager_invite_restricted:"), blocked

    def test_employee_recruiter_cannot_invite_by_rank(self):
        # A non-manager recruiter has no can_invite AND can't out-rank a peer,
        # so even if it reached the check, rank blocks a same-rank recruiter.
        ok, reason = invite_authorized("recruiter", False, "recruiter")
        assert ok is False and reason == "cant_invite_higher"

    def test_unconstrained_roles_use_rank(self):
        assert invite_authorized("owner", False, "fleet")[0] is True
        assert invite_authorized("admin", False, "recruiter")[0] is True
        assert invite_authorized("hr", False, "driver")[0] is True
        # owner can never be created via invite
        ok, reason = invite_authorized("owner", False, "owner")
        assert ok is False and reason == "owner_via_invite"

    def test_department_managers_invite_own_role_only(self):
        # A team-lead manager may invite ONLY their own role (rank-independent),
        # never other departments.
        for role in ("fleet", "safety", "dispatcher", "accounting"):
            ok, _ = invite_authorized(role, True, role)
            assert ok is True, role
            for blocked in ("driver", "hr", "admin", "recruiter"):
                if blocked == role:
                    continue
                ok, reason = invite_authorized(role, True, blocked)
                assert ok is False and reason.startswith("manager_invite_restricted:"), (role, blocked)
        # HR manager keeps driver (HR's base role invites drivers by rank —
        # promoting to manager must not take that away) + gains own-role.
        assert invite_authorized("hr", True, "hr")[0] is True
        assert invite_authorized("hr", True, "driver")[0] is True
        assert invite_authorized("hr", True, "fleet")[0] is False

    def test_policy_lives_with_the_invites_feature(self):
        assert MANAGER_INVITE_ONLY == {
            "recruiter": {"recruiter"},
            "fleet": {"fleet"},
            "safety": {"safety"},
            "dispatcher": {"dispatcher"},
            "hr": {"hr", "driver"},
            "accounting": {"accounting"},
        }
