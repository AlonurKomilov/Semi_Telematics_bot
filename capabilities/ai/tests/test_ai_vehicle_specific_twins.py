"""Vehicle-specific tools stop taking the first "103".

The client returns a LIST ("0, 1, or 2+ matches"); the tools took
``detail[0]``.  Now the registry resolves the name first, the caller's
scope rungs ride along for VEHICLE_SPECIFIC tools too, and an ambiguous
name is answered with a question rather than another company's truck.
"""

from __future__ import annotations

import os
from types import SimpleNamespace as V

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import features.location.ai_tool as LOC
from capabilities.ai.tools.registry import execute_tool


class _DB:
    def __init__(self, rows): self.rows = rows
    async def list_vehicles(self, account_id, **kw):
        return [r for r in self.rows if r.is_active or kw.get("include_inactive")]
    async def list_archived_vehicles(self, *a, **k): return []


OSY = V(id=42, unit_number="103", company_code="OSY", telematics_ref="sam-42", is_active=True)
G1 = V(id=99, unit_number="103", company_code="G1", telematics_ref="sam-99", is_active=True)


class _Detail:
    def __init__(self): self.calls = []
    async def __call__(self, account_id, name, company=None):
        self.calls.append(company)
        return [{"name": name, "_org": company or "?", "location": {"reverseGeo": {}}}]


@pytest.mark.asyncio
class TestGetVehicleLocation:
    async def test_scoped_caller_gets_their_own_twin(self, monkeypatch):
        svc = _Detail(); monkeypatch.setattr(LOC, "_svc_detail", svc)
        res = await execute_tool(
            "get_vehicle_location", {"vehicle_name": "103"}, None,
            account_id=1, db=_DB([OSY, G1]),
            scope_vehicles=["103"], scope_ladder={"identities": [[42, "sam-42", "103"]]},
        )
        assert "error" not in res, res
        # The identities reached the executor and picked OSY — the service
        # was asked for THAT company, not the first hit.
        assert svc.calls == ["OSY"]

    async def test_unscoped_caller_with_twins_is_asked_which(self, monkeypatch):
        svc = _Detail(); monkeypatch.setattr(LOC, "_svc_detail", svc)
        res = await execute_tool("get_vehicle_location", {"vehicle_name": "103"}, None,
                                 account_id=1, db=_DB([OSY, G1]))
        assert "say which company" in res.get("error", ""), res
        assert svc.calls == []          # never reached the provider

    async def test_company_argument_answers_the_question(self, monkeypatch):
        svc = _Detail(); monkeypatch.setattr(LOC, "_svc_detail", svc)
        res = await execute_tool("get_vehicle_location",
                                 {"vehicle_name": "103", "company": "G1"}, None,
                                 account_id=1, db=_DB([OSY, G1]))
        assert "error" not in res and svc.calls == ["G1"]

    async def test_no_twins_is_unchanged(self, monkeypatch):
        svc = _Detail(); monkeypatch.setattr(LOC, "_svc_detail", svc)
        res = await execute_tool("get_vehicle_location", {"vehicle_name": "103"}, None,
                                 account_id=1, db=_DB([OSY]))
        assert "error" not in res and svc.calls == ["OSY"]

    async def test_unregistered_name_keeps_the_old_path(self, monkeypatch):
        # Nothing in the registry: fall back to a name-only lookup, exactly
        # as before — no worse for a truck that predates the registry.
        svc = _Detail(); monkeypatch.setattr(LOC, "_svc_detail", svc)
        res = await execute_tool("get_vehicle_location", {"vehicle_name": "777"}, None,
                                 account_id=1, db=_DB([OSY]))
        assert "error" not in res and svc.calls == [None]


@pytest.mark.asyncio
async def test_identities_are_injected_for_vehicle_specific_tools(monkeypatch):
    seen = {}
    async def spy(tool_args, samsara_client, account_id=None, db=None):
        seen.update(tool_args); return {"ok": True}
    from capabilities.ai.tools import registry as R
    monkeypatch.setattr(R, "get_tool_handler", lambda n: spy if n == "get_vehicle_location" else None)
    await execute_tool("get_vehicle_location", {"vehicle_name": "103"}, None, account_id=1,
                       db=_DB([OSY]), scope_vehicles=["103"],
                       scope_ladder={"identities": [[42, "sam-42", "103"]]})
    assert seen.get("_scope_vehicles") == ["103"]
    assert seen.get("_scope_identities") == [[42, "sam-42", "103"]]
