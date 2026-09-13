"""Guard: a row cap must not change the answer to a ranking question.

Two tools sliced a list that was sorted by something other than the
thing being asked about, and reported the full scoped total beside the
slice — which reads to the model as "I looked at all of them".

* get_efficiency_summary took the first 30 of a list ordered by
  (company, name). On a multi-company account, "which truck had the
  worst MPG this week" was answered from whichever companies sort first
  alphabetically, and the truck the question was about was routinely not
  in the payload.
* get_weather took the first 30 of a list sorted COLDEST FIRST, while
  its own description advertised "extreme cold or heat". Asked which
  trucks were running hot, the assistant answered from the thirty
  coldest — a wrong answer indistinguishable from a right one.

Both now compute the extremes over every scoped vehicle, so the cap is a
token budget rather than a statement about the fleet.
"""

import pytest


def _eff(name, company, mpg, idle=10.0):
    return {"name": name, "_org": company, "_mpg": mpg, "_idle_pct": idle,
            "_green_pct": 80.0, "_miles": 500, "_engine_hours": 40,
            "_driving_hours": 30, "_idle_hours": 10, "_driver_name": "D"}


@pytest.mark.asyncio
async def test_the_worst_mpg_survives_the_cap(monkeypatch):
    """The worst truck sorts LAST by (company, name) — exactly the case
    the old slice dropped."""
    import features.vehicles.efficiency.ai_tool as mod

    fleet = [_eff(f"A-{i:03d}", "AAA", 8.0 + i * 0.01) for i in range(40)]
    fleet.append(_eff("Z-999", "ZZZ", 3.1))          # the answer

    async def _svc(account_id, days=7):
        return list(fleet)

    monkeypatch.setattr(mod, "_svc_fleet_eff", _svc, raising=False)
    res = await mod.get_efficiency_summary({"days": 7}, None, account_id=1)

    assert res["worst_mpg"]["vehicle"] == "Z-999", res["worst_mpg"]
    assert res["worst_mpg"]["value"] == 3.1
    # And it is in the returned rows, because they are ordered by MPG.
    assert res["vehicles"][0]["vehicle"] == "Z-999"
    # The cut is stated rather than implied.
    assert res["vehicle_count"] == 41
    assert res["vehicles_returned"] == 30
    assert res["truncated"] is True


@pytest.mark.asyncio
async def test_vehicles_with_no_mpg_do_not_win_the_ranking(monkeypatch):
    import features.vehicles.efficiency.ai_tool as mod

    fleet = [_eff("HAS", "AAA", 6.0), _eff("NONE", "AAA", None)]

    async def _svc(account_id, days=7):
        return list(fleet)

    monkeypatch.setattr(mod, "_svc_fleet_eff", _svc, raising=False)
    res = await mod.get_efficiency_summary({}, None, account_id=1)
    assert res["worst_mpg"]["vehicle"] == "HAS"
    assert res["vehicles"][-1]["vehicle"] == "NONE", "unrated sort last"


def _w(name, temp):
    return {"name": name, "_weather": {"temp_f": temp, "temp_c": (temp - 32) / 1.8},
            "location": {"reverseGeo": {"formattedLocation": "Somewhere"}}}


@pytest.mark.asyncio
async def test_the_hot_end_is_reachable(monkeypatch):
    """The source is sorted coldest-first; the hottest truck is last."""
    import features.live_map.ai_tool as mod

    fleet = [_w(f"COLD-{i:02d}", -10 + i) for i in range(40)]
    fleet.append(_w("HOT-1", 112.0))                 # the answer

    async def _svc(account_id):
        return list(fleet)

    monkeypatch.setattr(mod, "_svc_weather", _svc, raising=False)
    res = await mod.get_weather({}, None, account_id=1)

    assert res["summary"]["max_f"] == 112.0
    names = [v["vehicle"] for v in res["hottest"]]
    assert names[0] == "HOT-1", names
    assert res["summary"]["min_f"] == -10
    assert res["coldest"][0]["vehicle"] == "COLD-00"
    assert res["reporting_count"] == 41


@pytest.mark.asyncio
async def test_weather_counts_freezing_and_hot_over_every_reading(monkeypatch):
    import features.live_map.ai_tool as mod

    fleet = [_w("F1", 20), _w("F2", 32), _w("MILD", 70), _w("H1", 95), _w("H2", 104)]

    async def _svc(account_id):
        return list(fleet)

    monkeypatch.setattr(mod, "_svc_weather", _svc, raising=False)
    res = await mod.get_weather({}, None, account_id=1)
    assert res["summary"]["freezing_count"] == 2      # <= 32
    assert res["summary"]["hot_count"] == 2           # >= 95
    assert res["truncated"] is False


@pytest.mark.asyncio
async def test_weather_with_no_readings_says_so_rather_than_crashing(monkeypatch):
    import features.live_map.ai_tool as mod

    async def _svc(account_id):
        return [{"name": "NO-SENSOR", "_weather": {}, "location": {}}]

    monkeypatch.setattr(mod, "_svc_weather", _svc, raising=False)
    res = await mod.get_weather({}, None, account_id=1)
    assert res["reporting_count"] == 0
    assert res["summary"] == {}
    assert res["coldest"] == [] and res["hottest"] == []
