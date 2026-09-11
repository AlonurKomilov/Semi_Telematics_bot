"""Guard: an AI tool is gated on the permission its own feature opens.

TOOL_PERMISSIONS is a hand-maintained parallel authority. Nothing tied a
tool to the permission its feature's own routes require, and the default
for a missing row is OPEN — so drift here is silent and always widens or
narrows access without anyone noticing.

Four rows had drifted, and one of them mattered:

* ``check_vehicle_camera`` resolved through ``can_view_vehicles``.
  Dispatcher, HR, accounting and driver all hold that flag and none of
  them holds ``can_view_cameras``, so four roles the permission system
  denies cameras could reach a dashcam frame through the assistant.
* ``get_recent_inspections`` resolved through the maintenance MANAGE
  verb, denying dispatcher, HR and driver a DVIR tool their
  ``can_view_inspections`` entitles them to — drift in the closed
  direction.
* ``get_parked_vehicles`` used ``can_view_vehicles`` where the board
  uses ``can_view_parking``.
* ``search_knowledge_base`` required nothing at all.

``capabilities/permissions/registry.py`` is the SSOT for what each
feature opens, so the pairing below is derived from it rather than
retyped.
"""

import capabilities.ai.tools as _tools  # noqa: F401  (registers every feature's tools)
from capabilities.ai.tools.registry import get_tool_schema
from capabilities.permissions.roles import TOOL_PERMISSIONS

# tool -> the FEATURE whose permission it must resolve through. Only
# tools whose feature declares an `opens` entry in the registry appear;
# a cross-cutting tool (account stats, attachments) has no single owner
# and is deliberately absent.
TOOL_FEATURE = {
    "check_vehicle_camera": "cameras",
    "get_parked_vehicles": "parking",
    "get_recent_inspections": "inspections",
    "search_knowledge_base": "knowledge_base",
    "get_vehicle_events": "events",
    "get_events_summary": "events",
}


def _feature_flags(feature_id: str) -> set[str]:
    """What the registry says this feature opens, plus its own flags."""
    from capabilities.permissions.registry import ENTRIES

    for e in ENTRIES:
        if e.id == feature_id:
            # `opens` is the flag the nav entry needs; `flags` is every
            # permission the feature owns (inspections opens on the
            # manage verb but owns the view verb too).
            return set(e.opens or ()) | set(e.flags or ())
    raise AssertionError(f"no registry entry for feature {feature_id!r}")


def test_every_mapped_tool_resolves_through_its_own_feature():
    problems = []
    for tool, feature in TOOL_FEATURE.items():
        assert get_tool_schema(tool), f"{tool} is not registered"
        required = set(TOOL_PERMISSIONS.get(tool) or ())
        if not required:
            problems.append(f"{tool}: no permission required at all")
            continue
        owned = _feature_flags(feature)
        stray = required - owned
        if stray:
            problems.append(
                f"{tool}: gated on {sorted(stray)}, but the {feature} feature "
                f"opens {sorted(owned)}"
            )
    assert not problems, "AI gates drifted from their features:\n  " + "\n  ".join(problems)


def test_the_camera_tool_needs_the_camera_permission():
    """Called out on its own because it is the one with a person on the
    other end: an inward-facing dashcam frame of a driver at work."""
    assert TOOL_PERMISSIONS["check_vehicle_camera"] == ["can_view_cameras"]


def test_no_role_reaches_a_tool_its_feature_denies():
    """The end-to-end statement, across every role the product ships."""
    from adapters.storage import Role
    from capabilities.permissions.roles import get_permissions

    leaks = []
    for tool, feature in TOOL_FEATURE.items():
        required = TOOL_PERMISSIONS.get(tool) or []
        owned = _feature_flags(feature)
        for role in Role:
            try:
                perms = get_permissions(role)
            except Exception:
                continue
            gate_open = any(getattr(perms, p, False) for p in required)
            feature_open = any(getattr(perms, f, False) for f in owned)
            if gate_open and not feature_open:
                leaks.append(f"{role.value} reaches {tool} without any {feature} permission")
    assert not leaks, "\n  ".join(leaks)
