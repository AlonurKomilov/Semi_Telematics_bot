"""Match a shop invoice against a vehicle's OPEN maintenance tasks.

Why this exists: a work order and the maintenance schedule only meet if
somebody remembers to link them.  Unlinked, the brake job you just paid
for still reads as "overdue brakes" on the maintenance page.  The AI is
already reading the invoice, so this proposes the links — the human
approves them together with the work order.

Deterministic on purpose: the matching runs HERE, in plain Python, not
in the model.  A hallucinated task id can't enter the payload, the
behaviour is unit-testable, and the executor re-validates every id
against the account + vehicle + still-open state anyway.
"""

from __future__ import annotations

import re

# Statuses that mean "this task is finished or abandoned" — everything
# else (pending / overdue / in_progress) is open work.  The bot writes
# 'done' where the API writes 'completed'; both count as closed.
CLOSED_STATUSES = frozenset({"completed", "done", "cancelled"})

# Curated matching vocabulary, keyed on the SERVICE TASK's
# ``canonical_key`` (adapters/storage/service_tasks.py) — not on a
# private list of slugs.  Keeping these keys tied to the seeded
# standard tasks is what stops this file from becoming a fourth
# drifting copy of the task vocabulary; a task with no entry here
# still matches through its own description words.
#
# Specific by design — one hit is enough to suggest, so a generic word
# here would spam every WO.
_TASK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "oil":            ("oil", "lube", "lof"),
    "tires":          ("tire", "tyre", "tread", "rotation", "rotate",
                       "alignment", "align", "balance"),
    "brakes":         ("brake", "brakes", "rotor", "rotors", "caliper",
                       "drum", "drums", "shoe", "shoes"),
    "inspection":     ("inspection", "inspect", "pm service"),
    "dot_inspection": ("dot", "fmcsa", "annual inspection"),
    "dpf_regen":      ("dpf", "regen", "particulate", "aftertreatment", "scr"),
    "def_refill":     ("def", "urea", "adblue"),
    "transmission":   ("transmission", "clutch", "gearbox"),
    "coolant":        ("coolant", "radiator", "antifreeze", "thermostat"),
    "battery":        ("battery", "batteries", "alternator", "starter"),
    "electrical":     ("electrical", "wiring", "harness", "fuse", "solenoid"),
    "air_filter":     ("air filter", "air cleaner"),
    "fuel_filter":    ("fuel filter", "water separator"),
    "alignment":      ("alignment", "align", "toe", "camber"),
    "suspension":     ("suspension", "spring", "shock", "airbag", "bushing"),
    "air_system":     ("air line", "air lines", "air dryer", "glad hand",
                       "air leak"),
    "hvac":           ("hvac", "a/c", "air conditioning", "heater", "blower"),
    "lighting":       ("headlight", "taillight", "marker light", "light bulb"),
    "lube":           ("lube", "grease", "chassis lubrication"),
    "pm_service":     ("pm service", "preventive maintenance"),
    "trailer_service": ("kingpin", "landing gear", "trailer service"),
}

# Words that carry no matching signal on their own.  Two families:
# generic repair verbs/nouns ("replace", "service") that appear on every
# invoice, and generic component words ("filter", "fluid") that would
# wrongly bind an AIR-filter task to an OIL-filter line.
_STOPWORDS = frozenset({
    # verbs / scheduling noise
    "replace", "replaced", "repair", "repaired", "check", "checked",
    "inspect", "service", "serviced", "install", "installed", "remove",
    "removed", "change", "changed", "fix", "fixed", "adjust", "clean",
    "needs", "need", "due", "scheduled", "schedule", "maintenance",
    "work", "order", "job", "shop", "labor", "parts", "part", "misc",
    # units / subjects
    "truck", "trailer", "unit", "vehicle", "front", "rear", "left",
    "right", "side", "both", "new", "old",
    # generic components — too weak to bind on alone
    "filter", "filters", "fluid", "fluids", "kit", "assembly", "assy",
    "line", "lines", "hose", "hoses", "seal", "gasket", "sensor",
    "valve", "pump", "belt", "bolt", "nut", "clamp", "cap", "set",
    # grammar
    "and", "the", "for", "with", "from", "per", "each", "all", "any",
})


def _words(text: str) -> set[str]:
    """Significant lowercase words (3+ chars, stopwords removed)."""
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) >= 3 and w not in _STOPWORDS
    }


def _mentions(haystack: str, phrase: str) -> bool:
    """Whole-word (or whole-phrase) containment — 'def' must not match
    'definitely', and 'brake' must not match 'brakebar'."""
    return re.search(rf"\b{re.escape(phrase)}\b", haystack) is not None


def invoice_haystack(
    parts: list[dict], labor: list[dict], *extra: str,
) -> str:
    """The searchable text of an invoice: line descriptions + any free
    text (notes / complaint), lowercased into one blob."""
    bits: list[str] = []
    for p in parts or []:
        bits.append(str(p.get("part_name") or p.get("name") or ""))
    for l in labor or []:
        bits.append(str(l.get("description") or ""))
    bits.extend(str(x or "") for x in extra)
    return " ".join(bits).lower()


def is_open_task(task: dict) -> bool:
    """Open = not finished/abandoned AND not already on another WO."""
    if str(task.get("status") or "").strip().lower() in CLOSED_STATUSES:
        return False
    return not task.get("work_order_id")


def suggest_task_links(
    open_tasks: list[dict], haystack: str, *, limit: int = 5,
) -> list[dict]:
    """Open tasks whose work the invoice appears to cover.

    Two independent signals, either is enough:
      * the task's TYPE vocabulary appears in the invoice text, or
      * a significant word from the task's own description appears
        (this is what catches 'custom' tasks — "Air line leak" ↔ an
        "Air line" part line).

    Returns ``[{id, task_type, description, matched_on}, ...]`` ordered
    by the caller's task order (already status/priority-sorted by the
    storage layer), capped at ``limit`` so a proposal stays readable.
    """
    out: list[dict] = []
    for task in open_tasks or []:
        if not is_open_task(task):
            continue
        # Prefer the service task's canonical key when the caller passes
        # an enriched row; fall back to the legacy slug (they agree for
        # standard tasks, which is what keeps this transition seamless).
        ttype = str(
            task.get("canonical_key") or task.get("task_type") or ""
        ).strip().lower()
        desc = str(task.get("description") or "")

        matched: str | None = None
        for kw in _TASK_KEYWORDS.get(ttype, ()):
            if _mentions(haystack, kw):
                matched = kw
                break
        if matched is None:
            for w in sorted(_words(desc)):
                if _mentions(haystack, w):
                    matched = w
                    break
        if matched is None:
            continue

        try:
            tid = int(task["id"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "id": tid,
            "task_type": ttype,
            "description": desc[:120],
            "matched_on": matched,
        })
        if len(out) >= limit:
            break
    return out


def describe_links(suggestions: list[dict]) -> str:
    """One human phrase for the approval summary — the user must see
    what they're linking BEFORE they approve, never after."""
    if not suggestions:
        return ""
    labels = []
    for s in suggestions:
        label = (s.get("task_type") or "").replace("_", " ") or "task"
        labels.append(f"#{s['id']} {label}")
    n = len(labels)
    return (
        f" Also links {n} open maintenance task{'s' if n > 1 else ''} "
        f"({', '.join(labels)})."
    )
