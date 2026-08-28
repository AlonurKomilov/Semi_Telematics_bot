"""Who a manager may invite — owned by the invites FEATURE, not RBAC.

A recruiting manager may build ONLY a recruiter team, never fleet/hr/etc.
That rule is the invites feature's, and the last test here asserts
exactly that ownership.

Split from features/settings/tests/test_invite_policy.py.  The other half — what the
manager TIER grants — is a permissions question and lives there.
"""

from adapters.storage.models import Role
from capabilities.permissions.roles import role_tier
from features.settings.invites.service import invite_authorized, MANAGER_INVITE_ONLY


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
