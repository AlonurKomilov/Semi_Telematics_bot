"""Guards: the AI tool registry's DECLARATIONS must match its code.

Three properties, each of which has already been violated in shipped
code, and each cheap to state:

1. Every registered tool carries an explicit permission DECISION.  A
   missing row and a deliberate ``None`` are indistinguishable at the
   gate (``TOOL_PERMISSIONS.get(name)`` returns None either way and the
   tool is advertised to everyone), so "we meant it" has to be written
   down.
2. Every write EXECUTOR's tool declares ``writes``, and every tool that
   declares ``writes`` has an executor.  ``file_vehicle_document``
   shipped with an executor and no declaration: the approve endpoint
   reads ``writes`` from the code registry, so every Approve answered
   400 and the feature was dead from its first day — while also
   escaping the write-scope guard (which filters on ``writes``) and
   both write-suppression paths.  One missing key, four gates skipped.
3. Every name in the three classification frozensets is a tool that
   actually exists, so a rename cannot silently empty a set.

These read the registry rather than adding to it, so they stay true for
tools that do not exist yet.
"""

import capabilities.ai.tools as _tools  # noqa: F401  (import registers every feature's tools)
from capabilities.ai.tools.registry import (
    _ACTION_EXECUTORS, _TOOL_REGISTRY, _UNDO_EXECUTORS,
)
from capabilities.permissions.roles import (
    ACCOUNT_WIDE_TOOLS, SCOPE_AWARE_TOOLS, TOOL_PERMISSIONS,
    VEHICLE_SPECIFIC_TOOLS, FeatureSet,
)


def _registered() -> set[str]:
    return set(_TOOL_REGISTRY)


def test_the_registry_actually_loaded():
    """Everything below is vacuously true against an empty registry."""
    assert len(_registered()) > 30, (
        "the feature ai_tool imports in capabilities/ai/tools/__init__.py "
        "did not run — every guard in this file would pass on nothing"
    )


def test_every_tool_has_an_explicit_permission_decision():
    missing = sorted(_registered() - set(TOOL_PERMISSIONS))
    assert not missing, (
        "registered tools with no row in TOOL_PERMISSIONS — the gate "
        "cannot tell these apart from a deliberate None and advertises "
        "them to every role: " + ", ".join(missing)
    )


def test_no_permission_row_names_a_tool_that_does_not_exist():
    stale = sorted(set(TOOL_PERMISSIONS) - _registered())
    assert not stale, (
        "TOOL_PERMISSIONS rows for tools nobody registers (renamed or "
        "deleted): " + ", ".join(stale)
    )


def test_every_permission_row_names_a_real_permission():
    bad = [
        f"{tool} -> {perm}"
        for tool, perms in TOOL_PERMISSIONS.items()
        for perm in (perms or [])
        if not hasattr(FeatureSet, perm)
    ]
    assert not bad, (
        "TOOL_PERMISSIONS names permissions that do not exist on "
        "FeatureSet, so getattr(perms, p, False) is permanently False "
        "and the tool is unreachable: " + ", ".join(bad)
    )


def test_writes_declaration_and_executor_are_paired():
    declared = {n for n, e in _TOOL_REGISTRY.items() if e["schema"].get("writes")}
    executors = set(_ACTION_EXECUTORS)
    assert not (declared - executors), (
        "tools declaring writes:True with no registered executor — "
        "approve would 400: " + ", ".join(sorted(declared - executors))
    )
    assert not (executors - declared), (
        "registered executors whose tool does NOT declare writes:True — "
        "the approve endpoint refuses them and they escape the "
        "write-scope guard and write suppression: "
        + ", ".join(sorted(executors - declared))
    )


def test_every_undo_recipe_has_an_executor():
    orphans = sorted(set(_UNDO_EXECUTORS) - set(_ACTION_EXECUTORS))
    assert not orphans, (
        "undo recipes for actions that cannot be executed: "
        + ", ".join(orphans)
    )


def test_classification_sets_name_real_tools():
    reg = _registered()
    for label, members in (
        ("ACCOUNT_WIDE_TOOLS", ACCOUNT_WIDE_TOOLS),
        ("SCOPE_AWARE_TOOLS", SCOPE_AWARE_TOOLS),
        ("VEHICLE_SPECIFIC_TOOLS", VEHICLE_SPECIFIC_TOOLS),
    ):
        ghosts = sorted(members - reg)
        assert not ghosts, (
            f"{label} names tools that are not registered — a rename "
            f"silently dropped them out of the gate: " + ", ".join(ghosts)
        )


def test_scope_aware_is_a_subset_of_account_wide():
    """The dispatcher only injects scope for ACCOUNT_WIDE ∩ SCOPE_AWARE."""
    stray = sorted(SCOPE_AWARE_TOOLS - ACCOUNT_WIDE_TOOLS)
    assert not stray, (
        "SCOPE_AWARE_TOOLS entries missing from ACCOUNT_WIDE_TOOLS — "
        "these never receive _scope_vehicles: " + ", ".join(stray)
    )
