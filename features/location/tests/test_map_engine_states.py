"""The map's engine word comes with the row when the warehouse served it.

``get_vehicles_for_map`` used to ask Samsara for engine states on EVERY
call — a second provider round-trip per map refresh, per account — even
though the warehouse row it had just been handed already carried the
ingest's resolved word.  Now the row's own word wins and the provider
is asked only for rows that arrived without one: the live-Samsara
fallback path, whose overview payload never carries it.
"""

from __future__ import annotations

import pytest

import features.location.service as svc


def _row(vid: str, engine: str | None = None, speed: float = 0.0) -> dict:
    loc = {"latitude": 1.0, "longitude": 2.0, "speedMilesPerHour": speed}
    if engine is not None:
        loc["engineStates"] = {"value": engine}
    return {"id": vid, "name": f"T{vid}", "location": loc}


@pytest.fixture
def provider_calls(monkeypatch):
    """Stand in for the cached Samsara engine-states fetch; records
    every time the map reached for it."""
    calls: list[int] = []

    async def _states(account_id: int) -> dict[str, str]:
        calls.append(account_id)
        return {"2": "Idle"}

    monkeypatch.setattr(svc, "_get_engine_states_by_id", _states)
    return calls


def _overview_of(rows):
    async def _overview(account_id, company=None):
        return rows
    return _overview


async def test_warehouse_rows_carry_their_word_and_never_ask_the_provider(monkeypatch, provider_calls):
    # The warehouse's vocabulary (moving / idle / off) — what every row
    # the reader serves actually says — answers in the provider's words.
    monkeypatch.setattr(svc, "get_vehicles_overview",
                        _overview_of([_row("1", "moving", 40), _row("2", "idle"), _row("3", "off")]))
    out = await svc.get_vehicles_for_map(1)
    assert [v["engineState"] for v in out] == ["On", "Idle", "Off"]
    assert [svc.classify_vehicle_status(v) for v in out] == ["moving", "idle", "stopped"]
    assert provider_calls == []


async def test_provider_words_on_a_row_are_taken_as_they_are(monkeypatch, provider_calls):
    monkeypatch.setattr(svc, "get_vehicles_overview",
                        _overview_of([_row("1", "On", 40), _row("2", "Off")]))
    out = await svc.get_vehicles_for_map(1)
    assert [v["engineState"] for v in out] == ["On", "Off"]
    assert provider_calls == []


async def test_only_wordless_rows_are_filled_from_the_provider(monkeypatch, provider_calls):
    monkeypatch.setattr(svc, "get_vehicles_overview",
                        _overview_of([_row("1", "On", 40), _row("2")]))
    out = await svc.get_vehicles_for_map(1)
    assert out[0]["engineState"] == "On"
    assert out[1]["engineState"] == "Idle"
    assert provider_calls == [1]


async def test_a_word_the_classifier_does_not_trust_is_treated_as_absent(monkeypatch, provider_calls):
    # An ingest with nothing to resolve leaves "", and a provider value
    # we do not model must not leak through as authoritative either.
    monkeypatch.setattr(svc, "get_vehicles_overview",
                        _overview_of([_row("2", ""), _row("3", "Cranking")]))
    out = await svc.get_vehicles_for_map(1)
    assert out[0]["engineState"] == "Idle"
    assert "engineState" not in out[1]
    assert provider_calls == [1]


async def test_a_provider_that_has_nothing_leaves_the_row_wordless(monkeypatch):
    async def _nothing(account_id):
        return {}
    monkeypatch.setattr(svc, "_get_engine_states_by_id", _nothing)
    monkeypatch.setattr(svc, "get_vehicles_overview", _overview_of([_row("9", None, 30)]))
    out = await svc.get_vehicles_for_map(1)
    assert "engineState" not in out[0]
    # ...and the classifier's speed heuristic still gives an answer.
    assert svc.classify_vehicle_status(out[0]) == "moving"


def test_the_classifier_reads_the_word_the_map_put_on_the_row():
    assert svc.classify_vehicle_status(
        {"engineState": "On", "location": {"speedMilesPerHour": 40}}) == "moving"
    assert svc.classify_vehicle_status(
        {"engineState": "On", "location": {"speedMilesPerHour": 0}}) == "idle"
    assert svc.classify_vehicle_status(
        {"engineState": "Off", "location": {"speedMilesPerHour": 0}}) == "stopped"
