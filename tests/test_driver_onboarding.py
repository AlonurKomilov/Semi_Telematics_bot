"""Driver onboarding — the recruiting hand-off, and who may finish it.

The model (owner decision 2026-07-30): the recruiter runs the pipeline
and APPROVES; onboarding mints a USER, so it belongs to driver
administration.  What must hold:

  * the grant is its own — narrower than ``can_invite`` AND than
    ``can_manage_drivers``, so Fleet (who administers drivers) does not
    inherit hiring, and the recruiter no longer holds it;
  * a hire is only possible from 'approved' — the FMCSA vetting gate;
  * the queue is reachable WITHOUT the recruiting dashboard, which is the
    whole point of splitting the two.
"""

from __future__ import annotations

import os
import sys
from dataclasses import fields

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from capabilities.permissions.roles import (
    ROLE_PERMISSIONS, TIER_GRANTS, FeatureSet, Role,
)
from capabilities.permissions.modules import FLAG_MODULES


class TestTheGrantMoved:
    def test_the_old_flag_is_gone(self):
        names = {f.name for f in fields(FeatureSet)}
        assert "can_convert_to_driver" not in names
        assert "can_onboard_drivers" in names

    def test_hiring_is_never_inherited(self):
        """Fleet administers drivers but must not hire; the recruiter runs
        the pipeline but must not mint users."""
        fleet = ROLE_PERMISSIONS[Role.FLEET]
        assert fleet.can_manage_drivers is True
        assert fleet.can_onboard_drivers is False

        rec = ROLE_PERMISSIONS[Role.RECRUITER]
        assert rec.can_manage_applications is True      # still runs the pipeline
        assert rec.can_onboard_drivers is False         # hands over
        assert rec.can_invite is False                  # unchanged

    def test_hr_can_finish_what_the_recruiter_approved(self):
        hr = ROLE_PERMISSIONS[Role.HR]
        assert hr.can_manage_drivers is True
        assert hr.can_onboard_drivers is True
        # ...and it is NOT a manager-only power: the base role holds it, so
        # the tier must not re-grant a flag the base already has.
        assert "can_onboard_drivers" not in TIER_GRANTS[Role.HR].grants

    def test_owner_and_admin_keep_it(self):
        for r in (Role.OWNER, Role.ADMIN):
            assert ROLE_PERMISSIONS[r].can_onboard_drivers is True

    def test_module_mask_follows_the_flag(self):
        assert FLAG_MODULES["can_onboard_drivers"] == frozenset({"hr"})
        assert "can_convert_to_driver" not in FLAG_MODULES


class TestTheSubFeatureIsReal:
    def test_it_has_a_home_and_a_hub_contribution(self):
        """What separates a sub-feature from a component (FEATURES.md):
        its own folder AND its own contribution to a hub."""
        import features.drivers.onboarding.router as r
        import features.drivers.onboarding.alert  # noqa: F401
        from capabilities.alerting.registry import alert_sources
        assert r.router.prefix == "/drivers/onboarding"
        assert any(s.key == "driver_onboarding_stale_check" for s in alert_sources())

    def test_the_hire_left_the_applications_router(self):
        import features.applications.router as ar
        paths = {getattr(r, "path", "") for r in ar.router.routes}
        assert not any(p.endswith("/convert") for p in paths)
