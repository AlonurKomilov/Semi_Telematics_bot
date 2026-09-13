"""Guard: filtering a list builds the caller's scope once, not per row.

``filter_to_scope`` used to call ``row_in_scope`` for every row, and
each of those re-read ``_scope_identities``, re-parsed every entry and
rebuilt the frozen VehicleScope. That is quadratic in
(rows x scope size), and it is blocking CPU on the event loop: one
assistant question on a few-hundred-vehicle account slowed every other
request on that worker.

Measured on a 300-row list against a 300-vehicle scope: 286 ms before,
28 ms after.

Asserted by counting constructions rather than by timing — a clock
assertion is flaky on a shared machine, and the count is the actual
property.
"""

import pytest

from capabilities.ai.tools import scope as mod


def _args(n: int) -> dict:
    return {
        "_scope_vehicles": [f"T-{i:03d}" for i in range(n)],
        "_scope_identities": [[i, f"sam_{i}", f"T-{i:03d}"] for i in range(n)],
    }


def _rows(n: int) -> list[dict]:
    return [{"vehicle_name": f"T-{i:03d}", "vehicle_id": f"sam_{i}"}
            for i in range(n)]


def test_the_scope_is_built_once_for_the_whole_list(monkeypatch):
    calls = []
    real = mod._scope_of

    def _counting(tool_args, allowed):
        calls.append(1)
        return real(tool_args, allowed)

    monkeypatch.setattr(mod, "_scope_of", _counting)
    out = mod.filter_to_scope(_rows(50), _args(50))

    assert len(out) == 50, "every row is in scope in this fixture"
    assert len(calls) == 1, (
        f"the scope was rebuilt {len(calls)} times for 50 rows — once per "
        "row means re-parsing every identity for every row"
    )


def test_filtering_still_keeps_exactly_the_callers_rows():
    args = _args(3)
    rows = _rows(3) + [
        {"vehicle_name": "OTHER", "vehicle_id": "sam_999"},
        # A twin: same unit number, different provider id.
        {"vehicle_name": "T-001", "vehicle_id": "sam_other"},
    ]
    kept = mod.filter_to_scope(rows, args)
    assert [r["vehicle_id"] for r in kept] == ["sam_0", "sam_1", "sam_2"], (
        "the twin shares a name and must be split by the provider id"
    )


def test_an_unrestricted_caller_is_not_charged_for_a_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_scope_of",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
                            AssertionError("built a scope for an unrestricted caller")))
    rows = _rows(10)
    assert mod.filter_to_scope(rows, {}) is rows
    assert not calls


def test_an_empty_scope_still_fails_closed():
    assert mod.filter_to_scope(_rows(5), {"_scope_vehicles": []}) == []
