"""The AI scope's identity rungs — renames and same-number twins.

The AI tools always matched by exact name, which fails in BOTH
directions once names stop being identities: a provider rename makes
the caller's OWN truck vanish from their answers (scope says "229",
rows say "229 Idris Ahmed"), while same-number twins across companies
("103" in G1 and OSY) are indistinguishable, so a company-restricted
caller's answers could include the other company's truck.  The rungs
resolve both: registry id first, provider id next, exact name last —
and a rung is used only when BOTH sides carry it, so rows written
before the identity backfill still reach their owner by name.
"""

from __future__ import annotations

from capabilities.ai.tools.scope import (
    filter_to_scope,
    row_in_scope,
    scope_vehicle_set,
)


def _args(names=None, rids=None, exts=None):
    args: dict = {}
    if names is not None:
        args["_scope_vehicles"] = names
    if rids is not None:
        args["_scope_registry_ids"] = rids
    if exts is not None:
        args["_scope_external_ids"] = exts
    return args


class TestContractUnchanged:
    def test_absent_scope_is_unrestricted(self):
        rows = [{"vehicle_name": "x"}]
        assert filter_to_scope(rows, {}) == rows
        assert scope_vehicle_set({}) is None

    def test_empty_scope_is_fail_closed(self):
        assert filter_to_scope([{"vehicle_name": "x"}], _args(names=[])) == []
        assert scope_vehicle_set(_args(names=[])) == set()

    def test_plain_name_scope_still_exact_matches(self):
        rows = [{"vehicle_name": "230"}, {"vehicle_name": "2303"}]
        got = filter_to_scope(rows, _args(names=["230"]))
        assert [r["vehicle_name"] for r in got] == ["230"]


class TestRenameReachability:
    def test_renamed_truck_comes_back_via_registry_id(self):
        # Scope "229" + registry rung; the row was renamed by the
        # provider but carries our id — the driver sees their truck.
        rows = [{"vehicle_name": "229 Idris Ahmed", "registry_id": 60}]
        args = _args(names=["229"], rids=[60])
        assert filter_to_scope(rows, args) == rows

    def test_renamed_truck_via_external_id(self):
        rows = [{"vehicle_name": "229 Idris Ahmed", "vehicle_id": "sam_60"}]
        args = _args(names=["229"], exts=["sam_60"])
        assert filter_to_scope(rows, args) == rows

    def test_renamed_truck_without_any_id_stays_invisible(self):
        # No shared rung → no match.  Honest: we cannot prove this row
        # is the caller's truck, and guessing is how leaks start.
        rows = [{"vehicle_name": "229 Idris Ahmed"}]
        assert filter_to_scope(rows, _args(names=["229"], rids=[60])) == []


class TestCompanyTwins:
    def test_other_companys_twin_is_rejected_by_id(self):
        # Both trucks are named 103.  The caller's scope carries G1's
        # registry id; OSY's 103 has a different one and is refused
        # BEFORE the identical name is consulted.
        rows = [
            {"vehicle_name": "103", "registry_id": 10},   # caller's (G1)
            {"vehicle_name": "103", "registry_id": 99},   # OSY twin
        ]
        got = filter_to_scope(rows, _args(names=["103"], rids=[10]))
        assert len(got) == 1 and got[0]["registry_id"] == 10

    def test_twin_without_ids_still_matches_by_name(self):
        # Pre-backfill rows carry no id: the name rung admits them, as
        # before this change — no worse, and gone once ids exist.
        rows = [{"vehicle_name": "103"}]
        assert filter_to_scope(rows, _args(names=["103"], rids=[10])) == rows


class TestInjectionSafety:
    def test_model_supplied_rungs_are_scrubbed(self):
        # The rung keys are a server-side channel, like _attachments —
        # a model must not be able to widen its own scope by supplying
        # them in tool arguments.
        import asyncio
        from capabilities.ai.tools import registry as reg

        seen = {}

        async def fake_handler(tool_args, client, account_id=None, db=None):
            seen.update(tool_args)
            return {"rows": []}

        name = "test_scope_probe_tool"
        reg._TOOL_REGISTRY[name] = {
            "handler": fake_handler, "schema": {"name": name},
        }
        try:
            asyncio.run(
                reg.execute_tool(
                    name,
                    {"_scope_registry_ids": [999], "_scope_external_ids": ["x"]},
                    None, 1, None,
                    scope_vehicles=None,
                )
            )
        finally:
            reg._TOOL_REGISTRY.pop(name, None)
        assert "_scope_registry_ids" not in seen
        assert "_scope_external_ids" not in seen

    def test_row_in_scope_reads_overview_id_key(self):
        # Overview rows carry the provider id under "id", not
        # "vehicle_id" — both spellings reach the external rung.
        args = _args(names=["229"], exts=["sam_60"])
        assert row_in_scope({"id": "sam_60", "name": "renamed"}, args, key="name")
