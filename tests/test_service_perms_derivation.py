"""Derived service-surface permissions (Alerts inbox, AI assistant).

Alerts and the AI assistant are always-on infrastructure SERVICES present for
EVERY role — not owner-toggled features.  The owner narrows only what FLOWS
THROUGH a service by disabling features (relevance.py gates alert *types*,
TOOL_PERMISSIONS gates AI *tools*); the service surface itself — the inbox, the
assistant — is always there.  ``capabilities.permissions.roles`` encodes this
in ``derive_service_perms``, which sets the inbox SCOPE from the role's vehicle
scope (but never withholds the inbox) and forces the AI assistant on.

These tests pin that contract: every role keeps the inbox, the scope tracks
vehicle scope, and neither service can be revoked per-role.
"""

import pytest
from dataclasses import replace

from adapters.storage import Role
from capabilities.permissions.roles import (
    FeatureSet,
    ROLE_PERMISSIONS,
    get_permissions,
    derive_service_perms,
    DERIVED_SERVICE_FIELDS,
)


# Exact derived (can_alerts_all, can_alerts_vehicle) for each role.  Every role
# HAS the inbox; only the scope differs, and it tracks vehicle scope:
# account-wide vehicle visibility → fleet-wide inbox, otherwise own-vehicle.
EXPECTED_INBOX = {
    Role.OWNER:      (True,  False),
    Role.ADMIN:      (True,  False),
    Role.FLEET:      (True,  False),
    Role.SAFETY:     (True,  False),
    Role.DISPATCHER: (True,  False),
    Role.HR:         (True,  False),
    Role.ACCOUNTING: (True,  False),
    Role.DRIVER:     (False, True),
    Role.RECRUITER:  (False, True),
}


class TestPerRoleDerivation:
    def test_every_role_has_expected_inbox_scope(self):
        """The derived inbox scope matches the pinned table for every role."""
        for role in Role:
            fs = derive_service_perms(ROLE_PERMISSIONS[role])
            got = (fs.can_alerts_all, fs.can_alerts_vehicle)
            assert got == EXPECTED_INBOX[role], (
                f"{role.value}: derived inbox {got} != expected "
                f"{EXPECTED_INBOX[role]}"
            )

    def test_every_role_keeps_the_inbox(self):
        """The Alerts inbox is a system service — NO role may be without it.
        Exactly one scope flag is set for every role."""
        for role in Role:
            fs = derive_service_perms(ROLE_PERMISSIONS[role])
            assert fs.can_alerts_all or fs.can_alerts_vehicle, (
                f"{role.value} lost the Alerts inbox — it is a system service"
            )

    def test_scopes_are_mutually_exclusive(self):
        """A role is never granted BOTH the fleet-wide and own-vehicle inbox —
        require_permission_any matches _all first, so the vehicle flag would be
        dead weight."""
        for role in Role:
            fs = derive_service_perms(ROLE_PERMISSIONS[role])
            assert not (fs.can_alerts_all and fs.can_alerts_vehicle), role.value

    def test_ai_assistant_always_on(self):
        """The AI assistant is an always-on service for every role."""
        for role in Role:
            assert derive_service_perms(ROLE_PERMISSIONS[role]).can_ai_chat is True

    def test_scheduled_reports_subscription_always_on(self):
        """can_digest (the Reports hub's scheduled-report subscription) is an
        always-on service for every role — which reports the digest contains
        still follows the role's per-report-type features."""
        for role in Role:
            assert derive_service_perms(ROLE_PERMISSIONS[role]).can_digest is True


class TestServiceIsNeverWithheld:
    def test_inbox_present_without_any_alert_feature(self):
        """A role with ZERO alert-bearing features still gets the inbox — the
        surface is a service; an empty inbox is fine, an absent one is not."""
        bare_fleet = FeatureSet(can_vehicle_all=True)   # no alert features
        bare_own = FeatureSet(can_vehicle_vehicle=True)  # no alert features
        assert derive_service_perms(bare_fleet).can_alerts_all is True
        assert derive_service_perms(bare_own).can_alerts_vehicle is True

    def test_masking_an_alert_feature_keeps_the_inbox(self):
        """Disabling a feature (e.g. via the module mask) removes its alerts
        from the inbox CONTENT but never removes the inbox itself."""
        # Accounting's only alert-bearing feature is can_fuel; strip it.
        masked = replace(ROLE_PERMISSIONS[Role.ACCOUNTING], can_fuel=False)
        fs = derive_service_perms(masked)
        assert fs.can_alerts_all is True  # still has the (now emptier) inbox

    def test_masking_vehicle_scope_narrows_but_keeps_inbox(self):
        """Even disabling the Fleet/Vehicles module (which masks
        can_vehicle_all) only narrows the inbox to own-vehicle scope — the
        inbox is never closed."""
        narrowed = replace(ROLE_PERMISSIONS[Role.OWNER], can_vehicle_all=False)
        fs = derive_service_perms(narrowed)
        assert fs.can_alerts_all is False
        assert fs.can_alerts_vehicle is True  # narrowed, not removed


class TestDerivationRules:
    def test_inbox_scope_follows_vehicle_scope(self):
        """Same role shape, different vehicle scope → different inbox scope."""
        fleet = FeatureSet(can_vehicle_all=True)
        own = FeatureSet(can_vehicle_all=False, can_vehicle_vehicle=True)
        assert derive_service_perms(fleet).can_alerts_all is True
        assert derive_service_perms(fleet).can_alerts_vehicle is False
        assert derive_service_perms(own).can_alerts_all is False
        assert derive_service_perms(own).can_alerts_vehicle is True

    def test_get_permissions_applies_derivation(self):
        """The sync role-default accessor derives too, so call-sites reading
        get_permissions(role).can_alerts_* (e.g. visible_main_buttons) agree
        with the account-aware resolver for a default-configured account."""
        for role in Role:
            gp = get_permissions(role)
            expect_all, expect_veh = EXPECTED_INBOX[role]
            assert gp.can_alerts_all is expect_all, role.value
            assert gp.can_alerts_vehicle is expect_veh, role.value
            assert gp.can_ai_chat is True, role.value


class TestDerivedFieldSet:
    def test_derived_fields_are_the_service_surfaces(self):
        assert DERIVED_SERVICE_FIELDS == frozenset(
            {"can_alerts_all", "can_alerts_vehicle", "can_ai_chat", "can_digest"}
        )
        # Every derived field is a real FeatureSet field.
        names = {f.name for f in FeatureSet.__dataclass_fields__.values()}
        assert DERIVED_SERVICE_FIELDS <= names
