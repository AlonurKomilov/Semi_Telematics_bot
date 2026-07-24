"""Company code on alert rows (bell/board) — the _attach_company enrichment.

Multi-company accounts tag each /pending alert with its unit's company
code so a row reads "130 · ACME · Parking", not just the unit.  Keyed on
vehicle_id (names collide across companies); blank when unknown or when
the account is single-company (empty map) so no chip renders.
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-use-only")

from capabilities.alerting.router import _attach_company


def test_tags_each_alert_by_vehicle_id():
    alerts = [
        {"id": 1, "vehicle_id": "v-130", "vehicle_name": "130"},
        {"id": 2, "vehicle_id": "v-6862", "vehicle_name": "6862"},
    ]
    veh_map = {"v-130": "ACME", "v-6862": "BETA"}
    out = _attach_company(alerts, veh_map)
    assert [a["company"] for a in out] == ["ACME", "BETA"]


def test_unknown_vehicle_gets_blank():
    alerts = [{"id": 1, "vehicle_id": "v-x", "vehicle_name": "X"}]
    _attach_company(alerts, {"v-130": "ACME"})
    assert alerts[0]["company"] == ""          # not in map → no chip


def test_empty_map_leaves_company_blank():
    """Single-company account (display_map = {}) → every row blank, so the
    UI shows no company chip (the code would be noise)."""
    alerts = [{"id": 1, "vehicle_id": "v-130"}, {"id": 2, "vehicle_id": "v-2"}]
    _attach_company(alerts, {})
    assert all(a["company"] == "" for a in alerts)


def test_none_company_code_becomes_blank():
    alerts = [{"id": 1, "vehicle_id": "v-130"}]
    _attach_company(alerts, {"v-130": None})    # unmapped vehicle in registry
    assert alerts[0]["company"] == ""
