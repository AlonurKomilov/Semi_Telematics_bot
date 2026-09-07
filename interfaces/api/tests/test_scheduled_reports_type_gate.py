"""PUT /user/scheduled-reports asks the report TYPE's own verb, not only
the Reports service verb the route is gated on."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
from fastapi import HTTPException

from capabilities.permissions.roles import FeatureSet
from capabilities.reporting.scheduled import router as home


class _Tenant:
    def __init__(self):
        self.saved = []

    async def subscribe_digest_ext(self, user_id, **kw):
        self.saved.append((user_id, kw["report_type"]))

    async def get_digest_subscriptions(self, user_id):
        return [{"report_type": t} for _, t in self.saved]


@pytest.fixture
def db_user(monkeypatch):
    async def fake(user, platform_db=None):
        return SimpleNamespace(id=5, email="a@b.c", email_verified=True)
    monkeypatch.setattr(home, "get_current_db_user", fake)


def _user(**flags):
    # the gate's stash: what require_permission resolved for this account
    return {"role": "dispatcher", "account_id": 1,
            "_perms": FeatureSet(can_view_reports=True, **flags)}


@pytest.mark.asyncio
async def test_a_type_the_role_cannot_view_is_refused_before_anything_is_written(db_user):
    tenant = _Tenant()
    body = home.ScheduledReportRequest(report_type="camera")
    with pytest.raises(HTTPException) as e:
        await home.upsert_scheduled_report(body, _user(can_view_cameras=False), None, tenant)
    assert e.value.status_code == 403
    assert "Camera Check" in e.value.detail
    assert tenant.saved == []


@pytest.mark.asyncio
async def test_a_type_the_role_holds_is_saved(db_user):
    tenant = _Tenant()
    body = home.ScheduledReportRequest(report_type="faults")
    out = await home.upsert_scheduled_report(body, _user(can_view_faults=True), None, tenant)
    assert tenant.saved == [(5, "faults")]
    assert out == {"scheduled_reports": [{"report_type": "faults"}]}
