"""Services are granted per role — nothing is derived any more.

Until 2026-09-06 four service flags were computed by
``derive_service_perms`` (always on; the alerts inbox following vehicle
visibility) and hidden from the matrix.  The owner decided a service is
a channel granted per role like a feature: a future broker role may be
denied AI.  These pin the seeds that replaced the derivation and the
one thing the derivation could never do — a revocation that sticks.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import replace

from capabilities.permissions.roles import (
    ROLE_PERMISSIONS, FeatureSet, Role, normalize_stored_perm_keys,
)

SERVICE_VERBS = ("can_view_alerts", "can_view_ai_assistant", "can_view_reports")


class TestServiceSeeds:
    def test_every_role_seeds_the_ai_assistant_and_reports(self):
        for role, fs in ROLE_PERMISSIONS.items():
            assert fs.can_view_ai_assistant is True, role
            assert fs.can_view_reports is True, role
            assert fs.can_view_notifications is True, role
            assert fs.can_view_tours is True, role
            assert fs.can_view_knowledge_base is True, role
        # the field default carries every stored row that predates it
        assert FeatureSet().can_view_notifications is True

    def test_the_inbox_seed_follows_vehicle_visibility(self):
        # What the derivation used to compute is a seed now — and only
        # a seed: an owner may tick it on for a role without vehicles.
        for role, fs in ROLE_PERMISSIONS.items():
            assert fs.can_view_alerts is bool(fs.can_view_vehicles), role
        assert ROLE_PERMISSIONS[Role.RECRUITER].can_view_alerts is False

    def test_the_legacy_names_are_aliases_of_the_verbs(self):
        fs = ROLE_PERMISSIONS[Role.DRIVER]
        assert fs.can_ai_chat is fs.can_view_ai_assistant
        assert fs.can_digest is fs.can_view_reports
        assert fs.can_alerts_all is fs.can_view_alerts
        assert fs.can_alerts_vehicle is fs.can_view_alerts

    def test_nothing_is_derived_any_more(self):
        import capabilities.permissions.roles as roles
        assert not hasattr(roles, "derive_service_perms")
        assert not hasattr(roles, "DERIVED_SERVICE_FIELDS")


class TestARevocationSticks:
    def test_a_stored_false_resolves_false(self):
        # The point of the change: the derivation overwrote any stored
        # value with True; a stored row now decides.
        seed = ROLE_PERMISSIONS[Role.DISPATCHER]
        stored = normalize_stored_perm_keys({"can_view_ai_assistant": False})
        merged = replace(seed, **stored)
        assert merged.can_view_ai_assistant is False
        assert merged.can_ai_chat is False            # the alias follows

    def test_a_legacy_key_in_a_stored_row_lands_on_the_verb(self):
        stored = normalize_stored_perm_keys({"can_ai_chat": False, "can_digest": True})
        assert stored == {"can_view_ai_assistant": False, "can_view_reports": True}

    def test_the_bare_featureset_grants_no_service(self):
        # No hidden always-on: a role built from nothing has no channel.
        fs = FeatureSet()
        for v in SERVICE_VERBS:
            assert getattr(fs, v) is False, v
