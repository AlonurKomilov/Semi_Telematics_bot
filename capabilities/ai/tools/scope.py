"""Shared Vehicle-Access scope filtering for AI tools.

For a company/vehicle-restricted caller the orchestrator injects the allowed
vehicle names as ``tool_args["_scope_vehicles"]`` (see
``capabilities/ai/intelligence.py`` and ``capabilities/ai/scope.py``), and —
when the registry can resolve them — one identity per vehicle as
``tool_args["_scope_identities"]`` (``[[registry_id, external_id, name], ...]``).
Every account-wide tool — wherever it lives (central or a feature's
``ai_tool.py``) — uses these helpers so the filtering is identical and lives
in one place.

Rows are admitted by the strongest rung BOTH sides carry: registry id
first (survives provider renames — a driver scoped to "229" still reaches
the truck the provider renamed "229 Idris Ahmed"), the provider's vehicle
id next, exact lowercased name last.  Name equality alone cannot separate
same-number twins across companies (two different 103s), which is exactly
what the id rungs are for.

The ladder itself lives in ``capabilities/permissions/vehicle_scope`` and
is delegated to here rather than restated: this file carried its own copy
over two pooled lists, which is the shape that denied a caller their own
not-yet-linked truck whenever a sibling assignment had a provider id.

Contract (unchanged):
  * ``_scope_vehicles`` absent / ``None`` → unrestricted (no filtering)
  * a list (even empty) → restrict to exactly those vehicles (``[]`` = none)
"""

from __future__ import annotations


def scope_vehicle_set(tool_args: dict) -> set[str] | None:
    """Return the caller's allowed vehicle names (lowercased) or ``None``.

    ``None`` means unrestricted; an empty set means "restricted to nothing"
    (fail-closed).  Use this when a tool filters inside a loop that already
    has other conditions; otherwise prefer :func:`filter_to_scope`.
    """
    scope = tool_args.get("_scope_vehicles")
    if scope is None:
        return None
    return {str(v).strip().lower() for v in scope if v}


def _scope_of(tool_args: dict, allowed: set[str]):
    """The caller's scope as the shared type.

    Identities when the orchestrator resolved them; otherwise the names
    alone, which is what an unresolvable registry leaves — the ladder's
    own documented floor.
    """
    from capabilities.permissions.vehicle_scope import VehicleIdentity, VehicleScope
    raw = tool_args.get("_scope_identities")
    if not raw:
        return VehicleScope.from_names(allowed)
    out = []
    for entry in raw:
        if isinstance(entry, (list, tuple)):
            rid, ext, nm = (list(entry) + [None, None, None])[:3]
        elif isinstance(entry, dict):
            rid, ext, nm = (entry.get("registry_id"), entry.get("external_id"),
                            entry.get("name"))
        else:
            continue
        out.append(VehicleIdentity.make(registry_id=rid, external_id=ext, name=nm))
    return VehicleScope.of(*out)


def row_in_scope(row: dict, tool_args: dict, key: str = "vehicle_name") -> bool:
    """Whether one row belongs to the caller's scope, by the strongest
    rung both sides share.  Unrestricted callers admit everything."""
    allowed = scope_vehicle_set(tool_args)
    if allowed is None:
        return True
    return _scope_of(tool_args, allowed).allows_row(row, name_key=key)


def filter_to_scope(rows: list[dict], tool_args: dict,
                    key: str = "vehicle_name") -> list[dict]:
    """Keep only rows in the caller's scope.

    Unrestricted callers get ``rows`` unchanged; a scoped caller gets only
    their allowed vehicles (``[]`` scope → empty list, fail-closed).
    """
    if scope_vehicle_set(tool_args) is None:
        return rows
    return [r for r in rows if row_in_scope(r, tool_args, key=key)]
