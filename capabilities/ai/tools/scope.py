"""Shared Vehicle-Access scope filtering for AI tools.

For a company/vehicle-restricted caller the orchestrator injects the allowed
vehicle names as ``tool_args["_scope_vehicles"]`` (see
``capabilities/ai/intelligence.py`` and ``capabilities/ai/scope.py``), and —
when the registry can resolve them — the matching identity rungs as
``tool_args["_scope_registry_ids"]`` / ``tool_args["_scope_external_ids"]``.
Every account-wide tool — wherever it lives (central or a feature's
``ai_tool.py``) — uses these helpers so the filtering is identical and lives
in one place.

Rows are admitted by the strongest rung BOTH sides carry: registry id
first (survives provider renames — a driver scoped to "229" still reaches
the truck the provider renamed "229 Idris Ahmed"), the provider's vehicle
id next, exact lowercased name last.  Name equality alone cannot separate
same-number twins across companies (two different 103s), which is exactly
what the id rungs are for.

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


def _rung_sets(tool_args: dict) -> tuple[set[int], set[str]]:
    """The id rungs the orchestrator resolved for this caller (may be empty)."""
    rids: set[int] = set()
    for v in tool_args.get("_scope_registry_ids") or []:
        try:
            rids.add(int(v))
        except (TypeError, ValueError):
            continue
    exts = {str(v).strip() for v in (tool_args.get("_scope_external_ids") or []) if v}
    return rids, exts


def row_in_scope(row: dict, tool_args: dict, key: str = "vehicle_name") -> bool:
    """Whether one row belongs to the caller's scope, by the strongest
    rung both sides share.  Unrestricted callers admit everything."""
    allowed = scope_vehicle_set(tool_args)
    if allowed is None:
        return True
    rids, exts = _rung_sets(tool_args)
    rid = row.get("registry_id")
    if rids and rid is not None:
        try:
            return int(rid) in rids
        except (TypeError, ValueError):
            pass
    ext = str(row.get("vehicle_id") or row.get("id") or "").strip()
    if exts and ext:
        return ext in exts
    return str(row.get(key) or "").strip().lower() in allowed


def filter_to_scope(rows: list[dict], tool_args: dict,
                    key: str = "vehicle_name") -> list[dict]:
    """Keep only rows in the caller's scope.

    Unrestricted callers get ``rows`` unchanged; a scoped caller gets only
    their allowed vehicles (``[]`` scope → empty list, fail-closed).
    """
    if scope_vehicle_set(tool_args) is None:
        return rows
    return [r for r in rows if row_in_scope(r, tool_args, key=key)]
