"""A scheduled report is two grants: the Reports service and the type's
own view verb.  One rule (capabilities/reporting/scheduled/access.py),
read by the API at save time, by the bot before offering a type, and by
the delivery job for every row."""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions import roles
from capabilities.permissions.roles import FeatureSet
from capabilities.reporting.registry import REPORTS
from capabilities.reporting.scheduled.access import may_receive, perms_allow, report_permission


def test_every_type_names_its_own_verb_and_an_unknown_type_names_none():
    for spec in REPORTS:
        assert report_permission(spec.key) == spec.permission
        assert report_permission(spec.key).startswith("can_view_")
    assert report_permission("payroll") is None


def test_both_grants_are_needed():
    both = FeatureSet(can_view_reports=True, can_view_cameras=True)
    assert perms_allow(both, "camera")
    assert not perms_allow(FeatureSet(can_view_reports=True, can_view_cameras=False), "camera")
    assert not perms_allow(FeatureSet(can_view_reports=False, can_view_cameras=True), "camera")
    assert not perms_allow(both, "faults")          # a different type's verb
    assert not perms_allow(both, "no_such_report")


@pytest.mark.asyncio
async def test_the_delivery_question_is_asked_of_the_account_and_the_tier(monkeypatch):
    asked = []

    async def fake(role, account_id, is_manager=False, is_primary_owner=False, company_id=None):
        asked.append((role, account_id, is_manager, is_primary_owner))
        return FeatureSet(can_view_reports=True, can_view_faults=True)

    monkeypatch.setattr(roles, "get_user_permissions", fake)
    assert await may_receive(9, "safety", "faults", is_manager=True)
    assert not await may_receive(9, "safety", "camera")
    assert asked == [("safety", 9, True, False), ("safety", 9, False, False)]
