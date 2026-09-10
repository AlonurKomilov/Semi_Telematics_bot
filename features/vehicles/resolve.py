"""Resolve ONE registry vehicle from a unit number the model supplied.

A unit number is a reusable LABEL: two live trucks can share "103"
across a customer's companies.  Every AI tool that takes a
``vehicle_name`` used to look the name up and take the first hit —
``detail[0]`` over a client result documented as "0, 1, or 2+ matches"
— so a caller scoped to OSY's 103 could be answered about G1's, and an
unscoped owner got whichever came back first with no sign there was a
choice.

This is the read-side twin of the write path's rule
(``features/vehicles/documents/ai_actions.py``): ambiguity is a
question, not a coin toss.

Rules, in order:
  1. registry rows: active, same unit number (case-insensitive);
  2. an explicit ``company`` argument narrows to that company;
  3. the caller's Vehicle-Access scope narrows by the identity ladder
     (registry id first, provider id, then name) — a scoped caller's
     own twin wins when the ladder can tell them apart;
  4. exactly one left → that vehicle; more → ``Ambiguous``; none →
     ``None``.

``None`` also covers a name the registry has never seen.  Callers then
fall back to today's name-based behaviour — no worse for a truck that
predates the registry, and gone once it is registered.  An empty
``company_code`` is a real value here (nearly half the live registry
carries none), never an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ambiguous:
    name: str
    candidates: list  # registry Vehicle rows


def _scope_for(tool_args: dict | None):
    """The caller's scope as a ``VehicleScope``, or ``None`` = unrestricted."""
    from capabilities.ai.tools.scope import scope_vehicle_set
    from capabilities.permissions.vehicle_scope import VehicleIdentity, VehicleScope

    args = tool_args or {}
    allowed = scope_vehicle_set(args)
    if allowed is None:
        return None
    raw = args.get("_scope_identities")
    if not raw:
        return VehicleScope.from_names(allowed)
    ids = []
    for entry in raw:
        if isinstance(entry, (list, tuple)):
            rid, ext, nm = (list(entry) + [None, None, None])[:3]
        elif isinstance(entry, dict):
            rid, ext, nm = entry.get("registry_id"), entry.get("external_id"), entry.get("name")
        else:
            continue
        ids.append(VehicleIdentity.make(registry_id=rid, external_id=ext, name=nm))
    return VehicleScope.of(*ids)


async def resolve_one(
    db: Any, account_id: int, name: str, *,
    company: str | None = None, tool_args: dict | None = None,
):
    """See the module docstring.  Returns a registry ``Vehicle``,
    an ``Ambiguous``, or ``None``."""
    unit = (name or "").strip().lower()
    if not unit or db is None or account_id is None:
        return None
    rows = [
        v for v in await db.list_vehicles(account_id)
        if getattr(v, "is_active", True)
        and (getattr(v, "unit_number", "") or "").strip().lower() == unit
    ]
    if company:
        want = company.strip().upper()
        rows = [v for v in rows if (getattr(v, "company_code", "") or "").strip().upper() == want]
    scope = _scope_for(tool_args)
    if scope is not None:
        rows = [
            v for v in rows
            if scope.allows(
                registry_id=getattr(v, "id", None),
                external_id=(getattr(v, "telematics_ref", "") or None),
                name=getattr(v, "unit_number", None),
            )
        ]
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        return Ambiguous(name=name, candidates=rows)
    return None


def ambiguity_error(amb: Ambiguous) -> dict:
    """The write path's wording, plus the companies to choose from —
    an answer the model can act on without a second round-trip."""
    companies = sorted({
        (getattr(v, "company_code", "") or "").strip().upper() or "(no company)"
        for v in amb.candidates
    })
    return {
        "error": (
            f"More than one active truck is numbered {amb.name} — say which "
            f"company ({', '.join(companies)})."
        ),
    }


async def resolve_for_tool(db: Any, account_id: int, tool_args: dict):
    """``(vehicle, error)`` for a tool's ``vehicle_name`` / ``company`` args.
    ``error`` is set only when the name is ambiguous; ``vehicle`` is
    ``None`` when the registry cannot say (callers keep today's path)."""
    name = tool_args.get("vehicle_name", "") or ""
    company = (tool_args.get("company") or "").strip() or None
    r = await resolve_one(db, account_id, name, company=company, tool_args=tool_args)
    if isinstance(r, Ambiguous):
        return None, ambiguity_error(r)
    return r, None


def company_of(vehicle) -> str | None:
    """The resolved vehicle's company code, or ``None`` when it has none
    (then rows cannot be split by company and name must do)."""
    code = (getattr(vehicle, "company_code", "") or "").strip().upper() if vehicle else ""
    return code or None


def company_for(vehicle, tool_args: dict) -> str | None:
    """The company to ask the provider about: the resolved truck's, else
    the one the model NAMED.

    The second half matters when the registry has no active row for
    (name, company) — a retired truck, an unregistered one, a typo.
    Falling back to a company-less lookup there would let the provider
    answer with the OTHER company's twin, which is the exact wrong answer
    this module exists to end.  Asking for the named company instead
    yields nothing, or that company's truck, and never its sibling.

    Known limit: an explicit company naming a RETIRED twin while a live
    sibling exists is not refused by the live-tool retirement check
    (``retired_vehicle_named`` is name-keyed and a live name wins), so
    the provider may answer with stale readings.  Reachable only when the
    model names the company of a truck that has left; unchanged from
    before, and narrower than the twin leak it replaces.
    """
    return company_of(vehicle) or ((tool_args.get("company") or "").strip().upper() or None)


def row_company(row: dict) -> str:
    """A provider/warehouse row's company, however that row spells it."""
    return (row.get("_org") or row.get("company") or row.get("company_code") or "").strip().upper()
