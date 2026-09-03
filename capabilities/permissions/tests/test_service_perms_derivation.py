"""derive_service_perms after the verb/scope flip.

The alerts inbox is a vehicle-alerts surface: it exists iff the role
opens Vehicles (can_view_vehicles).  Both derived flags now say that
one thing — WIDTH (all units / assigned trucks) is per MEMBER, Team
Management's answer, never a per-role flag — so the pair can no
longer disagree.  AI chat and the digest are always on.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import replace

from capabilities.permissions.roles import (
    ROLE_PERMISSIONS, FeatureSet, Role, derive_service_perms, get_permissions,
)


class TestPerRoleDerivation:
    def test_inbox_follows_vehicle_visibility_for_every_seed(self):
        for role, seed in ROLE_PERMISSIONS.items():
            fs = derive_service_perms(seed)
            assert fs.can_alerts_all == fs.can_alerts_vehicle == seed.can_view_vehicles, role

    def test_only_the_vehicle_less_role_has_no_inbox(self):
        vehicle_less = {r for r, s in ROLE_PERMISSIONS.items() if not s.can_view_vehicles}
        assert vehicle_less == {Role.RECRUITER}, vehicle_less
        fs = derive_service_perms(ROLE_PERMISSIONS[Role.RECRUITER])
        assert not fs.can_alerts_all and not fs.can_alerts_vehicle
        assert fs.can_ai_chat is True and fs.can_digest is True

    def test_the_pair_never_disagrees(self):
        for role, seed in ROLE_PERMISSIONS.items():
            fs = derive_service_perms(seed)
            assert fs.can_alerts_all == fs.can_alerts_vehicle, role.value

    def test_ai_assistant_and_digest_always_on(self):
        for role in Role:
            fs = derive_service_perms(ROLE_PERMISSIONS.get(role, FeatureSet()))
            assert fs.can_ai_chat is True and fs.can_digest is True


class TestServiceIsNeverWithheld:
    def test_inbox_present_without_any_alert_feature(self):
        bare = FeatureSet(can_view_vehicles=True)   # no alert features at all
        fs = derive_service_perms(bare)
        assert fs.can_alerts_all is True and fs.can_alerts_vehicle is True

    def test_masking_an_alert_feature_keeps_the_inbox(self):
        fs = derive_service_perms(replace(ROLE_PERMISSIONS[Role.OWNER], can_view_events=False))
        assert fs.can_alerts_all is True

    def test_masking_vehicles_removes_the_inbox(self):
        # There is no per-role "narrow" any more: a role that cannot
        # open Vehicles has no vehicle-alerts inbox.  Width for a role
        # that CAN is a Team Management matter.
        fs = derive_service_perms(replace(ROLE_PERMISSIONS[Role.OWNER], can_view_vehicles=False))
        assert fs.can_alerts_all is False and fs.can_alerts_vehicle is False


class TestDerivationRules:
    def test_get_permissions_applies_derivation(self):
        for role in Role:
            direct = derive_service_perms(ROLE_PERMISSIONS.get(role, FeatureSet()))
            via = get_permissions(role)
            assert (via.can_alerts_all, via.can_alerts_vehicle, via.can_ai_chat, via.can_digest) == \
                   (direct.can_alerts_all, direct.can_alerts_vehicle, direct.can_ai_chat, direct.can_digest), role
