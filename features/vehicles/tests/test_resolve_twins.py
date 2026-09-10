"""``resolve_one``: a unit number that two live trucks share is a
question, not a coin toss — and the caller's scope answers it when the
ladder can tell the twins apart."""

from __future__ import annotations

import os
from types import SimpleNamespace as V

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from features.vehicles.resolve import Ambiguous, ambiguity_error, resolve_one


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

    async def test_company_outside_scope_is_nothing_not_a_leak(self):
        args = {"_scope_vehicles": ["103"], "_scope_identities": [[42, "sam-42", "103"]]}
        assert await resolve_one(_DB([OSY, G1]), 1, "103", company="G1", tool_args=args) is None

    async def test_retired_rows_do_not_count(self):
        assert (await resolve_one(_DB([RETIRED, G1]), 1, "103")).id == 99

    async def test_empty_company_is_a_value(self):
        r = await resolve_one(_DB([NOCO, G1]), 1, "103")
        assert isinstance(r, Ambiguous)
        assert "(no company)" in ambiguity_error(r)["error"]

    async def test_unknown_name_is_none_so_callers_keep_their_old_path(self):
        assert await resolve_one(_DB([OSY]), 1, "888") is None
        assert await resolve_one(None, 1, "103") is None
