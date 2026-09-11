"""Guard: an AI tool's advertised vocabulary must exist in the data.

A filter value the store never holds matches no rows, and an empty
result reads to the model as "there are none" — a clean bill of health
on the exact question somebody asked because they were worried. This is
the highest-frequency failure the AI tool audit found: no attacker, every
user, every day, landing on faults, inspections and finished repairs.

Four vocabularies had drifted in shipped code:

* ``get_recent_inspections`` advertised ``pass`` / ``fail`` / ``defect``
  and ``pending`` / ``reviewed`` / ``resolved``. The columns hold
  lifecycle values and reviewer decisions; not one advertised value
  existed, so every filtered question answered zero.
* ``get_alert_history`` advertised ``event``. The store spells it
  ``events`` — 8,146 rows in production, unreachable — and the tool
  omitted ``scorecard`` entirely.
* ``get_recent_work_orders`` advertised ``closed`` and ``void`` (the
  brief interim vocabulary, translated only at the WRITE boundary) and
  never offered ``completed``, so "what did we get finished" filtered on
  a value no row holds.
* ``search_knowledge_base`` took free text against ten fixed keys.

The enums are DERIVED from their sources wherever a public symbol
exists, so the pairing below is what keeps the two honest.
"""

import capabilities.ai.tools as _tools  # noqa: F401  (registers every feature's tools)
from capabilities.ai.tools.registry import get_tool_schema


def _enum(tool: str, param: str) -> set[str]:
    schema = get_tool_schema(tool)
    assert schema, f"{tool} is not registered"
    props = schema.get("parameters", {}).get("properties", {})
    assert param in props, f"{tool} has no parameter {param}"
    values = props[param].get("enum")
    assert values, (
        f"{tool}.{param} has no enum — the handler accepts a fixed set, so "
        "the model is left to guess and a near miss returns nothing"
    )
    return set(values)


def test_inspection_filters_match_the_stored_vocabularies():
    from features.inspections.templates import (
        VALID_INSPECTION_STATUSES, VALID_REVIEW_STATUSES,
    )
    assert _enum("get_recent_inspections", "status") == set(VALID_INSPECTION_STATUSES)
    assert _enum("get_recent_inspections", "review_status") == set(VALID_REVIEW_STATUSES)


def test_inspections_can_still_answer_the_failed_question():
    """No status means "failed" — that question is about DEFECTS, and
    the tool has to offer a way to ask it or the description is writing
    a cheque the parameters cannot cash."""
    props = get_tool_schema("get_recent_inspections")["parameters"]["properties"]
    assert "with_defects" in props
    assert props["with_defects"]["type"] == "boolean"


def test_knowledge_categories_match_the_store():
    from adapters.storage.knowledge import KB_CATEGORIES
    assert _enum("search_knowledge_base", "category") == set(KB_CATEGORIES)


def test_work_order_statuses_are_the_ones_a_row_can_hold():
    from adapters.storage.work_orders import normalize_wo_status
    advertised = _enum("get_recent_work_orders", "status")
    # Every advertised value survives normalization unchanged — i.e. it
    # is a current value, not a legacy alias the write path translates.
    for value in advertised:
        assert normalize_wo_status(value) == value, (
            f"{value} is not a current work-order status"
        )
    assert "completed" in advertised, (
        "a finished repair is the most common thing anyone asks about"
    )
    assert not ({"closed", "void", "draft", "submitted"} & advertised), (
        "legacy statuses are accepted at the write boundary, never stored"
    )


def test_alert_types_cover_what_the_store_holds():
    """Checked against the types the alerting code itself works in, and
    pinned on the plural that carries the most rows."""
    advertised = _enum("get_alert_history", "alert_type")
    for essential in ("events", "parking", "fault", "fuel", "health",
                      "scorecard", "maintenance"):
        assert essential in advertised, f"{essential} is stored but not offered"
    assert "event" not in advertised, (
        "the singular matches no row — the safety-event family is 'events'"
    )
    assert _enum("get_alert_history", "severity") == {"critical", "warning", "info"}
    assert _enum("get_alert_history", "status") == {"active", "acknowledged", "cleared"}
