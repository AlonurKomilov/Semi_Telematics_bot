"""Shared Vehicle-Access scope filtering for AI tools.

For a company/vehicle-restricted caller the orchestrator injects the allowed
vehicle names as ``tool_args["_scope_vehicles"]`` (see
``capabilities/ai/intelligence.py`` and ``capabilities/ai/scope.py``).  Every
account-wide tool — wherever it lives (central or a feature's ``ai_tool.py``)
— uses these helpers so the filtering is identical and lives in one place.

Contract:
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


def filter_to_scope(rows: list[dict], tool_args: dict,
                    key: str = "vehicle_name") -> list[dict]:
    """Keep only rows whose ``key`` vehicle name is in the caller's scope.

    Unrestricted callers get ``rows`` unchanged; a scoped caller gets only
    their allowed vehicles (``[]`` scope → empty list, fail-closed).
    """
    allowed = scope_vehicle_set(tool_args)
    if allowed is None:
        return rows
    return [r for r in rows if str(r.get(key) or "").strip().lower() in allowed]
