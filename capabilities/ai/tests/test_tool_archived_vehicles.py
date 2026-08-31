"""What the assistant may say about a truck that left the fleet.

Two answers, and the split is the point:

  a LIVE question — where is it, what's its fuel, what faults does it
  have — is REFUSED, because the honest answer is "it was archived",
  not a months-old reading presented as current;

  a HISTORICAL question — its work orders, its costs, its past
  inspections — is ANSWERED exactly as before, because keeping that
  readable is the entire reason archiving retires a truck instead of
  deleting it. A fleet manager reconstructing an accident is precisely
  who needs it.

Enforced in ONE place. There is no shared vehicle resolver in the AI
path — four live tools resolve a name four different ways, and
`create_maintenance_task` does not resolve at all, storing the name as
free text — so a per-handler check would have been four checks and one
hole. The schema declares, `execute_tool` enforces, and all three
provider loops (Anthropic / OpenAI / Gemini) go through it.
"""
from __future__ import annotations

import pytest

from capabilities.ai.tools.registry import (
    _refuse_live_on_retired,
    get_all_tool_schemas,
    get_tool_schema,
)


#: Every registered tool's stance on a retired vehicle.
#:
#:   live       refuses — it answers about the truck NOW
#:   gated      reads warehouse live state, which the ingest gate stops
#:              writing for an archived truck, so it empties on its own
#:   historical answers — the record is why archiving keeps the row
#:   n/a        never identifies a single vehicle
#:
#: Undeclared fails. That is the mechanism: a new tool has to say what
#: it does about a truck the customer retired, at the moment its author
#: still knows the answer.
TOOL_STANCE: dict[str, str] = {
    # Refused for an archived truck — declared in their schemas.
    "check_vehicle_camera": "live",
    "get_vehicle_detail": "live",
    "get_vehicle_location": "live",
    "get_vehicle_odometer": "live",
    "get_vehicle_faults": "live",
    "create_maintenance_task": "live",
    # Live data, but account-wide: no argument names one vehicle, so
    # there is nothing to refuse. They read the warehouse, which the
    # ingest gate stops feeding, so an archived truck simply stops
    # appearing. `search_vehicles` must NOT refuse — `name_contains`
    # is a filter, and killing a whole search because one retired truck
    # matches it would be a worse answer than the one it replaced.
    "search_vehicles": "gated",
    "get_vehicle_health": "gated",
    "get_low_fuel_vehicles": "gated",
    "get_parked_vehicles": "gated",
    "get_rolling_stopped": "gated",
    "get_undriven_vehicles": "gated",
    "get_weather": "gated",
    "get_account_stats": "gated",
    # Answer for a retired truck, deliberately.
    "get_vehicle_maintenance": "historical",
    "get_vehicle_history": "historical",
    "get_vehicle_events": "historical",
    "get_vehicle_fuel_costs": "historical",
    "get_alert_history": "historical",
    "get_recent_work_orders": "historical",
    "get_recent_inspections": "historical",
    "get_maintenance_summary": "historical",
    "get_events_summary": "historical",
    "get_efficiency_summary": "historical",
    "get_fuel_cost_summary": "historical",
    "create_work_order": "historical",
    "acknowledge_alerts": "historical",
    "import_inventory_items": "historical",
    # Not about a vehicle at all.
    # Reports across the roster and never names one truck; archived
    # vehicles are excluded in the tool itself, since "which trucks
    # have no insurance" is a question about the fleet you RUN.
    "get_vehicle_documents_status": "n/a",
    # Filing NEW paperwork on a truck that left the fleet is not a
    # thing anyone means to do — you do not renew the registration of a
    # tractor you sold.  Reading a retired truck's papers stays open;
    # that is the archive's whole promise.
    "file_vehicle_document": "live",
    "get_geofences": "n/a",
    "get_drivers_list": "n/a",
    "get_driver_efficiency": "n/a",
    "get_driver_scorecard": "n/a",
    "get_driver_hos_status": "n/a",
    "get_driver_applications": "n/a",
    "search_knowledge_base": "n/a",
    "read_attachment": "n/a",
}


def test_every_tool_declares_its_stance_on_a_retired_vehicle():
    registered = {s["name"] for s in get_all_tool_schemas()}
    # A floor, not just non-empty: the registry fills by IMPORT, so a
    # partially-imported package would leave this guard checking six
    # tools and passing — the exact shape of a guard everyone trusts
    # and nobody is protected by.
    assert len(registered) >= 30, (
        f"only {len(registered)} tools registered — the package did not "
        "fully import, so this guard is inspecting a fraction of the "
        "surface"
    )

    undeclared = registered - set(TOOL_STANCE)
    assert not undeclared, (
        f"tool(s) with no stance on a retired vehicle: {sorted(undeclared)}. "
        "Add each to TOOL_STANCE — 'live' (and declare vehicle_scope + "
        "vehicle_arg in its schema so the dispatcher refuses it), 'gated', "
        "'historical', or 'n/a'."
    )
    stale = set(TOOL_STANCE) - registered
    assert not stale, (
        f"TOOL_STANCE names tool(s) that no longer exist: {sorted(stale)}"
    )


def test_the_live_tools_are_exactly_the_ones_that_refuse():
    """The map and the schemas must agree.

    A tool marked live here but not declared in its schema would sail
    straight past the dispatcher — the map would say it is handled and
    nothing would happen, which is the worst of both.
    """
    by_map = {k for k, v in TOOL_STANCE.items() if v == "live"}
    by_schema = {
        s["name"] for s in get_all_tool_schemas()
        if s.get("vehicle_scope") == "live"
    }
    assert by_map == by_schema, (
        f"map says {sorted(by_map)}, schemas say {sorted(by_schema)}"
    )
    for name in by_schema:
        assert get_tool_schema(name).get("vehicle_arg"), (
            f"{name} declares vehicle_scope='live' but no vehicle_arg — "
            "the dispatcher has no argument to resolve, so it refuses "
            "nothing"
        )


class _DB:
    """Stands in for the tenant DB's one lookup."""

    def __init__(self, retired=None, boom=False):
        self._retired = retired
        self._boom = boom
        self.asked: list[str] = []

    async def retired_vehicle_named(self, account_id, name):
        self.asked.append(name)
        if self._boom:
            raise RuntimeError("db down")
        return self._retired


_RETIRED = {
    "id": 16, "unit_number": "6862", "company_code": "PTG",
    "is_active": False, "archived_reason": "operator",
    "status_before_archive": "shop", "updated_at": "2026-08-26T05:34:00Z",
}


@pytest.mark.asyncio
async def test_a_live_question_about_a_retired_truck_is_refused():
    db = _DB(retired=_RETIRED)
    out = await _refuse_live_on_retired(
        "get_vehicle_location", {"vehicle_name": "6862"}, 10000001, db)
    assert out is not None and out["ok"] is False
    assert out["archived"] is True
    assert "6862" in out["error"]
    # It must REDIRECT, not dead-end: a bare failure reads as the
    # product being broken, and the history really is still there.
    assert "history" in out["error"].lower()
    assert "work orders" in out["error"].lower()


@pytest.mark.asyncio
async def test_a_historical_question_about_the_same_truck_is_allowed():
    db = _DB(retired=_RETIRED)
    out = await _refuse_live_on_retired(
        "get_vehicle_history", {"vehicle_name": "6862"}, 10000001, db)
    assert out is None, "the record is the reason archiving keeps the row"
    assert db.asked == [], "a historical tool must not even ask"


@pytest.mark.asyncio
async def test_a_live_truck_that_inherited_the_number_is_never_refused():
    """A door number is reusable.

    `retired_vehicle_named` returns None the moment any ACTIVE row
    carries the name, so the live truck wins and asking about it can
    never be refused on account of its predecessor.
    """
    db = _DB(retired=None)
    out = await _refuse_live_on_retired(
        "get_vehicle_location", {"vehicle_name": "6862"}, 10000001, db)
    assert out is None


@pytest.mark.asyncio
async def test_it_fails_open():
    """A stale reading is a smaller harm than an assistant that stops
    answering because a lookup hiccuped."""
    db = _DB(boom=True)
    out = await _refuse_live_on_retired(
        "get_vehicle_location", {"vehicle_name": "6862"}, 10000001, db)
    assert out is None


@pytest.mark.asyncio
async def test_no_vehicle_named_means_nothing_to_refuse():
    db = _DB(retired=_RETIRED)
    for args in ({}, {"vehicle_name": ""}, {"vehicle_name": "   "}):
        assert await _refuse_live_on_retired(
            "get_vehicle_location", args, 10000001, db) is None
    assert db.asked == []


@pytest.mark.asyncio
async def test_a_sweep_retired_truck_says_so_differently():
    """"Archived" and "stopped reporting" are different facts, and a
    person reading the answer deserves the right one."""
    db = _DB(retired={**_RETIRED, "archived_reason": "sweep"})
    out = await _refuse_live_on_retired(
        "get_vehicle_detail", {"vehicle_name": "6862"}, 10000001, db)
    assert "stopped reporting" in out["error"]
