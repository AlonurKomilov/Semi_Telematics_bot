"""Guard: every AI WRITE tool must declare an explicit vehicle-scope shape,
and that declaration must match the frozenset it's classified into.

This turns "someone added a write tool and forgot to make it scope-safe"
from a silent same-tenant isolation break (the acknowledge_alerts bug) into
a CI failure.  It mirrors tests/test_layer_boundaries.py: a structural
invariant enforced by a test rather than per-tool discipline.

The runtime gate (`_check_tool_permission`) also fails closed for an
unclassified write, so this test is the early-warning; the gate is the
last-resort deny.
"""

from __future__ import annotations

import capabilities.ai.tools  # noqa: F401  (import side effect: registers every tool)
from capabilities.ai.tools import get_all_tool_schemas
from capabilities.permissions.roles import (
    ACCOUNT_WIDE_TOOLS, SCOPE_AWARE_TOOLS, VEHICLE_SPECIFIC_TOOLS,
)

# scope value → the frozenset membership it promises.
VALID_SCOPES = {"vehicle_param", "resource_ids", "account_unscoped"}


def _write_tools() -> list[dict]:
    return [s for s in get_all_tool_schemas() if s.get("writes")]


def test_there_is_at_least_one_write_tool():
    # Sanity: if this ever hits zero, the registry didn't load and the rest
    # of the guard would vacuously pass.
    assert _write_tools(), "no writes:True tools registered — did the import fail?"


def test_every_write_tool_declares_a_valid_scope():
    bad = {
        s["name"]: s.get("scope")
        for s in _write_tools()
        if s.get("scope") not in VALID_SCOPES
    }
    assert not bad, (
        "write tools missing/invalid `scope` (must be one of "
        f"{sorted(VALID_SCOPES)}): {bad}"
    )


def test_scope_declaration_matches_frozenset_classification():
    """A tool's declared scope must line up with how the gate will treat it:
      • vehicle_param   ⇒ VEHICLE_SPECIFIC_TOOLS  (gate rejects a bad vehicle_name)
      • resource_ids    ⇒ SCOPE_AWARE_TOOLS       (gate injects _scope_vehicles)
      • account_unscoped⇒ ACCOUNT_WIDE_TOOLS but NOT SCOPE_AWARE_TOOLS (gate blocks scoped)
    """
    problems: list[str] = []
    for s in _write_tools():
        name, scope = s["name"], s.get("scope")
        if scope == "vehicle_param" and name not in VEHICLE_SPECIFIC_TOOLS:
            problems.append(f"{name}: scope=vehicle_param but not in VEHICLE_SPECIFIC_TOOLS")
        elif scope == "resource_ids" and name not in SCOPE_AWARE_TOOLS:
            problems.append(f"{name}: scope=resource_ids but not in SCOPE_AWARE_TOOLS")
        elif scope == "account_unscoped" and (
            name not in ACCOUNT_WIDE_TOOLS or name in SCOPE_AWARE_TOOLS
        ):
            problems.append(
                f"{name}: scope=account_unscoped but not in "
                f"ACCOUNT_WIDE_TOOLS\\SCOPE_AWARE_TOOLS"
            )
    assert not problems, "; ".join(problems)


def test_scope_aware_is_a_subset_of_account_wide():
    # The gate's block rule (`in ACCOUNT_WIDE_TOOLS and not in SCOPE_AWARE_TOOLS`)
    # only makes sense if scope-aware tools are themselves account-wide.
    leaked = SCOPE_AWARE_TOOLS - ACCOUNT_WIDE_TOOLS
    assert not leaked, f"SCOPE_AWARE_TOOLS not ⊆ ACCOUNT_WIDE_TOOLS: {sorted(leaked)}"
