"""The live map says WHO supplies each truck.

Three surfaces now read this one field — the dashboard's Live Map, the
browser extension's panel, and (already) the vehicle page's marks — so
dropping it from the payload would blank the provenance in all three at
once, silently, because every reader treats it as optional.

The registry merge already puts ``source``/``sources`` on the row; the
map endpoint's job is only to carry them through, which is exactly the
kind of one-line mapping that gets lost in a refactor.
"""
from __future__ import annotations

import pytest

from features.location import router as loc


@pytest.mark.asyncio
async def test_the_map_carries_creator_and_enrichers(monkeypatch):
    async def _vehicles(account_id, company=None):
        return [{
            "id": "v1", "name": "224", "_org": "PTG",
            "source": "samsara", "sources": ["samsara", "datatruck"],
            "location": {"latitude": 41.0, "longitude": -87.0},
        }]
    monkeypatch.setattr(loc, "get_vehicles_for_map", _vehicles)
    monkeypatch.setattr(loc, "get_user_company_codes", _none_scoped)
    monkeypatch.setattr(loc, "filter_by_allowed_companies", lambda d, a, **k: d)
    monkeypatch.setattr(loc, "filter_by_assigned_trucks", _no_narrowing)

    out = await loc.map_vehicles(company=None, user=_USER)
    props = out["features"][0]["properties"]
    assert props["source"] == "samsara"
    assert props["sources"] == ["samsara", "datatruck"]


@pytest.mark.asyncio
async def test_a_truck_with_no_provenance_reports_empty_not_missing(monkeypatch):
    """A reader that finds the KEY missing cannot tell "no provenance"
    from "this build forgot to send it" — so the keys are always there."""
    async def _vehicles(account_id, company=None):
        return [{"id": "v2", "name": "301",
                 "location": {"latitude": 1.0, "longitude": 2.0}}]
    monkeypatch.setattr(loc, "get_vehicles_for_map", _vehicles)
    monkeypatch.setattr(loc, "get_user_company_codes", _none_scoped)
    monkeypatch.setattr(loc, "filter_by_allowed_companies", lambda d, a, **k: d)
    monkeypatch.setattr(loc, "filter_by_assigned_trucks", _no_narrowing)

    props = (await loc.map_vehicles(company=None, user=_USER))["features"][0]["properties"]
    assert props["source"] == "" and props["sources"] == []


_USER = {"account_id": 1, "sub": "1", "role": "owner", "uid": 1}


async def _none_scoped(user):
    return []


async def _no_narrowing(data, user, name_key="name"):
    return data
