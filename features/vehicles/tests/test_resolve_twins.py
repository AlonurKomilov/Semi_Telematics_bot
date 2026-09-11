"""``resolve_one``: a unit number that two live trucks share is a
question, not a coin toss — and the caller's scope answers it when the
ladder can tell the twins apart."""

from __future__ import annotations

import os
from types import SimpleNamespace as V

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from features.vehicles.resolve import Ambiguous, ambiguity_error, resolve_one, OutOfScope, resolve_for_tool, company_for


class _DB:
    def __init__(self, rows): self.rows = rows
    async def list_vehicles(self, account_id, **kw):
        return [r for r in self.rows if r.is_active or kw.get("include_inactive")]


OSY = V(id=42, unit_number="103", company_code="OSY", telematics_ref="sam-42", is_active=True)
G1 = V(id=99, unit_number="103", company_code="G1", telematics_ref="sam-99", is_active=True)
NOCO = V(id=7, unit_number="103", company_code="", telematics_ref="", is_active=True)
RETIRED = V(id=5, unit_number="103", company_code="OSY", telematics_ref="", is_active=False)


@pytest.mark.asyncio
class TestResolveOne:
    async def test_single_match_resolves(self):
        assert (await resolve_one(_DB([OSY]), 1, "103")).id == 42

    async def test_twins_are_ambiguous_for_an_unscoped_caller(self):
        r = await resolve_one(_DB([OSY, G1]), 1, "103")
        assert isinstance(r, Ambiguous) and {v.id for v in r.candidates} == {42, 99}
        msg = ambiguity_error(r)["error"]
        assert "say which company" in msg and "G1" in msg and "OSY" in msg

    async def test_company_argument_picks_the_twin(self):
        assert (await resolve_one(_DB([OSY, G1]), 1, "103", company="g1")).id == 99

    async def test_scope_identities_pick_the_callers_own_twin(self):
        args = {"_scope_vehicles": ["103"], "_scope_identities": [[42, "sam-42", "103"]]}
        assert (await resolve_one(_DB([OSY, G1]), 1, "103", tool_args=args)).id == 42

    async def test_name_only_scope_cannot_split_twins_so_it_asks(self):
        # Pre-backfill assignment: the ladder has only the name rung, both
        # twins satisfy it, and pretending otherwise would be a guess.
        args = {"_scope_vehicles": ["103"]}
        assert isinstance(await resolve_one(_DB([OSY, G1]), 1, "103", tool_args=args), Ambiguous)

    async def test_company_outside_scope_is_a_refusal_not_a_leak(self):
        """Denied is not the same as unknown.

        This used to assert ``is None`` — and None is what a truck the
        registry has never heard of returns, which callers answer by
        asking the provider about the company the MODEL named. So a
        caller scoped to OSY who asked about "103 at G1" had the gate
        pass (it matches by name, which cannot split twins), the scope
        filter reject G1's row, and G1's company string handed to the
        provider anyway. The sentinel is what keeps the two apart.
        """
        args = {"_scope_vehicles": ["103"], "_scope_identities": [[42, "sam-42", "103"]]}
        out = await resolve_one(_DB([OSY, G1]), 1, "103", company="G1", tool_args=args)
        assert isinstance(out, OutOfScope)
        assert out.company == "G1"

    async def test_a_truck_the_registry_never_heard_of_is_still_unknown(self):
        """The retired/unregistered/typo fallback must survive: rows are
        empty BEFORE the scope step, so nothing was denied."""
        args = {"_scope_vehicles": ["103"], "_scope_identities": [[42, "sam-42", "103"]]}
        assert await resolve_one(_DB([OSY, G1]), 1, "888", tool_args=args) is None
        assert company_for(None, {"company": "G1"}) == "G1"

    async def test_the_tool_wrapper_turns_a_denial_into_an_error(self):
        args = {"vehicle_name": "103", "company": "G1",
                "_scope_vehicles": ["103"],
                "_scope_identities": [[42, "sam-42", "103"]]}
        vehicle, err = await resolve_for_tool(_DB([OSY, G1]), 1, args)
        assert vehicle is None
        assert err and "not in your vehicle access" in err["error"]

    async def test_retired_rows_do_not_count(self):
        assert (await resolve_one(_DB([RETIRED, G1]), 1, "103")).id == 99

    async def test_empty_company_is_a_value(self):
        r = await resolve_one(_DB([NOCO, G1]), 1, "103")
        assert isinstance(r, Ambiguous)
        assert "(no company)" in ambiguity_error(r)["error"]

    async def test_unknown_name_is_none_so_callers_keep_their_old_path(self):
        assert await resolve_one(_DB([OSY]), 1, "888") is None
        assert await resolve_one(None, 1, "103") is None
