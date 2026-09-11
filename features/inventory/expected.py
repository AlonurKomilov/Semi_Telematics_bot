"""What a vehicle is SUPPOSED to carry — the Config family, both scopes.

Until this existed, Inventory could only answer "what has somebody
recorded on this truck?".  That leaves two useful questions unanswerable:
completeness, and the strongest case the feature has — a dashcam nobody
ever typed in cannot go ``missing``.  It simply is not there, and nothing
notices.  Declaring the expectation turns that silence into a fact.

TWO SCOPES, AND WHY THE SPLIT IS NOT A PREFERENCE
-------------------------------------------------
``capabilities/config/docs/ARCHITECTURE.md`` states the blast-radius
rule: config that changes what data MEANS is account-wide, always, and
two roles must never disagree about the same fact.  Whether truck 103 is
short its ELD is a fact about the truck, not an opinion that varies with
who is looking — so the CATALOGUE is account-wide.

What legitimately varies by role is ATTENTION.  The owner put it exactly:
something showing red for one role pulls a second role's focus onto
something that is not theirs — dispatch cares that the straps are aboard,
safety does not, and safety's screen going red about straps costs safety
the thing red is for.  Which rows a role is FLAGGED on is arrangement,
which is the role scope's own territory.

So: one catalogue, many focuses.  The count of what a truck owes is the
same for everybody; the red is each role's own.  ``config/role.py`` names
this moment — "when a feature grows genuinely per-feature role config,
that is the moment to split it out".

Matching is by CATEGORY AND COUNT, never by label.  Categories are an
open vocabulary but a stable key; labels are free text somebody types
standing at a truck ("front dashcam", "dash cam", "DashCam 2"), and a
template keyed on those would drift the day it shipped.

No new table and no new permission — the family forbids both.  The
catalogue is ``account_settings["inventory_expected"]`` behind
``can_manage_config_all``; a role's focus is
``account_settings["inventory_focus.<role>"]`` behind
``can_manage_config_role``, with the own-role wall enforced by the router.
"""
from __future__ import annotations

import json
from typing import Any

#: The catalogue: one truth about what a vehicle carries.
CATALOGUE_KEY = "inventory_expected"
#: One per role.  A role with no row is focused on EVERYTHING, which is
#: the honest default: a role that has never narrowed its attention has
#: not asked to stop seeing anything.
FOCUS_KEY_PREFIX = "inventory_focus."


# A trailer's default is deliberately EMPTY rather than absent: an empty
# catalogue means "nothing is expected here", which is a real answer and
# keeps trailers out of every completeness count until somebody says
# otherwise.  Inventing a default for equipment we do not know the shape
# of would flag a whole fleet on our guess.
STANDARD_TRAILER_EXPECTED: list[dict] = []

# `required` is the dial that keeps the count honest.  A toll transponder
# is normal to carry and normal not to — flagging every truck without one
# teaches people to ignore the flag, which costs more than the flag is
# worth.  It is declared, counted on its own row, and not enforced.
STANDARD_TRUCK_EXPECTED: list[dict] = [
    {"category": "camera",           "label": "Dashcam",          "quantity": 1, "required": True},
    {"category": "eld",              "label": "ELD",              "quantity": 1, "required": True},
    {"category": "fuel_card",        "label": "Fuel card",        "quantity": 1, "required": True},
    {"category": "toll_transponder", "label": "Toll transponder", "quantity": 1, "required": False},
]

STANDARD_EXPECTED: dict[str, list[dict]] = {
    "truck": STANDARD_TRUCK_EXPECTED,
    "trailer": STANDARD_TRAILER_EXPECTED,
}

#: The vehicle types a catalogue can be written for — the same two PTI
#: checklists are written for, because it is the same split of the same
#: registry.
EXPECTED_VEHICLE_TYPES = tuple(STANDARD_EXPECTED)


def standard_for(vehicle_type: str) -> list[dict]:
    """The shipped default for a vehicle type — a copy, never the list.

    A caller that mutated the module-level list would change what every
    later reader sees.
    """
    return [dict(row) for row in STANDARD_EXPECTED.get(vehicle_type, [])]


def _clean_row(raw: Any, order: int) -> dict | None:
    """One stored row, or None if it cannot be trusted.

    Tolerant by design, like every other account setting: an unknown or
    malformed row falls out rather than taking the whole catalogue with
    it.  A template that refuses to load because one row is bad would
    report an entire fleet as expecting nothing.
    """
    if not isinstance(raw, dict):
        return None
    category = "_".join(str(raw.get("category") or "").strip().lower().split())[:40]
    if not category:
        return None
    try:
        quantity = max(1, min(99, int(raw.get("quantity") or 1)))
    except (TypeError, ValueError):
        quantity = 1
    return {
        "category": category,
        "label": str(raw.get("label") or "")[:120],
        "quantity": quantity,
        "required": bool(raw.get("required", True)),
        "sort_order": order,
    }


async def get_catalogue(db: Any, account_id: int) -> dict[str, list[dict]]:
    """Every vehicle type's expected list, merged over the shipped defaults.

    An account that has never written one gets ``{}`` for every type — NOT
    the standard.  Seeding on read would silently start flagging a fleet
    that never asked to be measured.
    """
    stored: Any = None
    try:
        raw = await db.get_account_setting(account_id, CATALOGUE_KEY, "")
    except Exception:
        raw = ""
    if raw:
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            stored = None

    out: dict[str, list[dict]] = {t: [] for t in EXPECTED_VEHICLE_TYPES}
    if isinstance(stored, dict):
        for vtype, rows in stored.items():
            if vtype not in out or not isinstance(rows, list):
                continue
            seen: set[str] = set()
            for i, raw_row in enumerate(rows):
                row = _clean_row(raw_row, i + 1)
                # One row per category: the last one wins, the way a
                # form's last field does.
                if row and row["category"] not in seen:
                    seen.add(row["category"])
                    out[vtype].append(row)
    return out


async def save_catalogue(db: Any, account_id: int, catalogue: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Replace the whole catalogue.  Returns what was actually stored.

    A REPLACE, not a merge: the editor sends the list it is looking at,
    and with a merge there is no way to express "this row is gone" — a
    catalogue nobody can delete a row from is one that only ever grows.
    """
    clean: dict[str, list[dict]] = {}
    for vtype in EXPECTED_VEHICLE_TYPES:
        rows = catalogue.get(vtype) or []
        seen: set[str] = set()
        kept: list[dict] = []
        for i, raw_row in enumerate(rows if isinstance(rows, list) else []):
            row = _clean_row(raw_row, i + 1)
            if row and row["category"] not in seen:
                seen.add(row["category"])
                kept.append(row)
        clean[vtype] = kept
    await db.set_account_setting(account_id, CATALOGUE_KEY, json.dumps(clean))
    return clean


async def get_role_focus(db: Any, account_id: int, role: str) -> list[str] | None:
    """The categories this role is flagged on, or None for "everything".

    None and [] are different answers and both are reachable: None means
    the role has never narrowed its attention, [] means it deliberately
    asked to be flagged on nothing.  Collapsing them would make "stop
    flagging me entirely" impossible to say.
    """
    try:
        raw = await db.get_account_setting(account_id, FOCUS_KEY_PREFIX + role, "")
    except Exception:
        raw = ""
    if not raw:
        return None
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(stored, list):
        return None
    return [str(c)[:40] for c in stored if str(c or "").strip()]


async def save_role_focus(db: Any, account_id: int, role: str, categories: list[str]) -> list[str]:
    clean = []
    for c in categories:
        key = "_".join(str(c or "").strip().lower().split())[:40]
        if key and key not in clean:
            clean.append(key)
    await db.set_account_setting(account_id, FOCUS_KEY_PREFIX + role, json.dumps(clean))
    return clean


async def coverage(
    db: Any, account_id: int, vehicle_id: int, vehicle_type: str, *, role: str = "",
) -> dict:
    """What this vehicle owes its catalogue, and what of that is THIS
    role's to worry about.

    ``expected``/``present`` are account-wide truth and identical for
    every role — the blast-radius rule.  ``flagged`` is the subset of the
    short rows inside this role's focus: the red, which is each role's
    own.  A role focused on everything sees every short row flagged,
    which is what a role that has never narrowed its attention means.
    """
    catalogue = (await get_catalogue(db, account_id)).get(vehicle_type, [])
    if not catalogue:
        return {"expected": 0, "present": 0, "rows": [], "flagged": []}

    have = await db.count_inventory_by_category(account_id, vehicle_id)
    focus = await get_role_focus(db, account_id, role) if role else None

    rows, expected, present = [], 0, 0
    for t in catalogue:
        want, got = int(t["quantity"]), have.get(t["category"], 0)
        if t["required"]:
            expected += want
            present += min(got, want)
        rows.append({
            "category": t["category"],
            "label": t["label"],
            "quantity": want,
            "required": bool(t["required"]),
            "present": got,
            "short": max(0, want - got),
        })
    flagged = [
        r["category"] for r in rows
        if r["short"] > 0 and (focus is None or r["category"] in focus)
    ]
    return {"expected": expected, "present": present, "rows": rows, "flagged": flagged}
