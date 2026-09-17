"""The bot's fuel line says its age once the reading is past its SLA.

A Telegram message cannot carry the dashboard's warn dot, so it carries
the words — the same words, at the same threshold, from the same
declared number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capabilities.formatting import format_vehicle_detail


def _vehicle(fuel_time: str | None) -> dict:
    return {
        "name": "101", "year": 2020, "make": "VOLVO", "model": "VNL",
        "vin": "4V4NC9EH5LN204420", "license_plate": "PWP3914",
        "location": {"address": "I-95"},
        "fuel": {"value": 45, "time": fuel_time},
        "fault_codes": {},
    }


def _text(v: dict) -> str:
    return "\n".join(format_vehicle_detail(v, show_company=False, show_faults=False))


def test_a_fresh_reading_carries_no_age():
    now = datetime.now(timezone.utc)
    out = _text(_vehicle((now - timedelta(minutes=2)).isoformat()))
    assert "45%" in out and " · " not in out.split("45%")[1].split("\n")[0]


def test_a_stale_reading_says_how_old_in_the_dashboards_words():
    now = datetime.now(timezone.utc)
    out = _text(_vehicle((now - timedelta(days=21)).isoformat()))
    fuel_line = next(l for l in out.split("\n") if "45%" in l)
    assert fuel_line.rstrip().endswith("· 21d ago"), fuel_line


def test_no_clock_means_no_words_not_a_guess():
    fuel_line = next(l for l in _text(_vehicle(None)).split("\n") if "45%" in l)
    assert " · " not in fuel_line
