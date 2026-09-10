"""Company scope for tools that are neither vehicle-keyed nor account-wide.

``get_geofences`` returned every zone to a company-scoped caller while the
REST route filtered by company.  The orchestrator now carries the caller's
company codes and injects ``_scope_companies`` into tools that declare
``company_scoped`` — a server channel, never model-supplied.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import features.geofencing.ai_tool as G
from capabilities.ai.tools.registry import execute_tool, get_tool_schema


def _fences():
    return [
        {"name": "Yard A", "_org": "OSY", "formattedAddress": "1 A St"},
        {"name": "Yard B", "_org": "G1", "formattedAddress": "2 B St"},
        {"name": "Shared", "_org": "", "formattedAddress": "3 C St"},
    ]


async def _svc(account_id, company=None):
    return _fences()


def test_get_geofences_declares_company_scope():
    assert get_tool_schema("get_geofences").get("company_scoped") is True


@pytest.mark.asyncio
class TestGeofenceCompanyScope:
    async def test_scoped_caller_sees_only_their_companies(self, monkeypatch):
        monkeypatch.setattr("features.geofencing.service.get_geofences", _svc)
        res = await execute_tool("get_geofences", {}, None, account_id=1, db=None,
                                 company_codes=["osy"])
        assert [z["name"] for z in res["geofences"]] == ["Yard A"]

    async def test_unrestricted_caller_sees_everything(self, monkeypatch):
        monkeypatch.setattr("features.geofencing.service.get_geofences", _svc)
        res = await execute_tool("get_geofences", {}, None, account_id=1, db=None)
        assert res["count"] == 3

    async def test_model_cannot_widen_its_own_scope(self, monkeypatch):
        """``_scope_companies`` is a server channel: a model-supplied value
        is stripped and replaced by the caller's real codes."""
        monkeypatch.setattr("features.geofencing.service.get_geofences", _svc)
        res = await execute_tool("get_geofences", {"_scope_companies": ["G1"]}, None,
                                 account_id=1, db=None, company_codes=["OSY"])
        assert [z["name"] for z in res["geofences"]] == ["Yard A"]
        # ...and with no real restriction a smuggled value grants nothing extra
        # either — it is simply dropped.
        res = await execute_tool("get_geofences", {"_scope_companies": ["G1"]}, None,
                                 account_id=1, db=None)
        assert res["count"] == 3
