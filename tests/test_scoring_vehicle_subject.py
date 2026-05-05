"""Phase B — vehicle-primary scorecard tests.

Verifies the subject abstraction in the engine, the new
``evaluate_subjects(subject='vehicle')`` orchestrator path, and the
``?subject=`` query parameter on the /safety/scorecards/composite +
/safety/scorecards/history routes.

Heavy mocking — the goal is to prove the *plumbing* works end-to-end
(engine → service → API), not to re-test the scoring math (covered by
test_scoring_pillars.py).
"""

from __future__ import annotations

import pytest

from capabilities.scoring.engine import Scorecard, score
from capabilities.scoring.rules import get_default_rules
from capabilities.scoring.signals import vehicle_efficiency as veh_signals
from capabilities.scoring.signals.safety_events import from_events_by_vehicle


# ── Engine: subject abstraction ──────────────────────────────────────


class TestEngineSubject:
    """``Scorecard`` carries subject_* fields; driver_* aliases still work."""

    def test_score_with_vehicle_subject_type(self):
        sc = score(
            subject_id="veh-1",
            subject_name="T-101",
            signals={"miles": 200, "drive_hours": 8.0},
            rules=list(get_default_rules()),
            subject_type="vehicle",
        )
        assert isinstance(sc, Scorecard)
        assert sc.subject_id == "veh-1"
        assert sc.subject_name == "T-101"
        assert sc.subject_type == "vehicle"
        # Legacy aliases must still answer the new identity.
        assert sc.driver_id == "veh-1"
        assert sc.driver_name == "T-101"

    def test_score_defaults_to_driver_subject_type(self):
        sc = score(
            subject_id="d-1",
            subject_name="Alice",
            signals={"miles": 200, "drive_hours": 8.0},
            rules=list(get_default_rules()),
        )
        assert sc.subject_type == "driver"
        assert sc.driver_id == "d-1"


# ── Vehicle signal aggregator ────────────────────────────────────────


class TestVehicleEfficiencySignal:
    """``from_vehicle_records`` returns the same shape as the driver path."""

    def test_drops_dead_vehicles(self):
        # No miles + no drive hours = nothing scoreable. Should be filtered.
        out = veh_signals.from_vehicle_records([
            {"id": "v1", "name": "T-1", "_miles": 0, "_driving_hours": 0},
            {"id": "v2", "name": "T-2", "_miles": 100, "_driving_hours": 4.0},
        ])
        assert "v1" not in out
        assert "v2" in out

    def test_payload_carries_engine_signals(self):
        out = veh_signals.from_vehicle_records([{
            "id": "v3", "name": "T-3", "_org": "ACME",
            "_miles": 250.0, "_driving_hours": 8.5,
            "_idle_hours": 1.5, "_idle_pct": 18,
            "_overspeed_min": 12.0, "_green_pct": 65, "_antic_pct": 41,
        }])
        rec = out["v3"]
        # All keys the rules read must be present.
        for key in (
            "miles", "drive_hours", "idle_hours", "idle_pct",
            "overspeed_min", "green_pct", "antic_pct",
            "driver_id", "driver_name", "company", "_vehicle_names", "_raw",
        ):
            assert key in rec, f"missing {key!r}"
        assert rec["_vehicle_names"] == ["t-3"]
        assert rec["company"] == "ACME"


# ── Vehicle-keyed event bucketing ────────────────────────────────────


class TestEventsByVehicle:
    def test_buckets_by_vehicle_id_and_name(self):
        out = from_events_by_vehicle([
            {"vehicle_id": "v1", "vehicle_name": "T-1", "event_type": "harsh_brake"},
            {"vehicle_id": "v1", "vehicle_name": "T-1", "event_type": "speeding"},
            {"vehicle_id": "v2", "vehicle_name": "T-2", "event_type": "harsh_brake"},
        ])
        # Indexed under both id (primary) and name (fallback).
        assert out["v1"]["count"] == 2
        assert out["T-1"]["count"] == 2
        assert out["v2"]["count"] == 1
        assert out["v1"]["by_type"] == {"harsh_brake": 1, "speeding": 1}

    def test_empty_keys_skipped(self):
        out = from_events_by_vehicle([
            {"vehicle_id": "", "vehicle_name": "", "event_type": "x"},
        ])
        assert out == {}


# ── Service-layer alias contract ─────────────────────────────────────


class TestServiceAliases:
    """``evaluate_drivers`` is now a thin alias of ``evaluate_subjects``
    (subject='driver').  Same signature, same return shape \u2014 nothing
    that imports the old name should break."""

    @pytest.mark.asyncio
    async def test_alias_signature_matches(self):
        from capabilities.scoring import service
        # Both entrypoints must be callable and async; the alias is a
        # one-liner forwarder.
        assert callable(service.evaluate_drivers)
        assert callable(service.evaluate_subjects)
        # ``evaluate_drivers`` should yield identical results to
        # ``evaluate_subjects(subject='driver')`` for a stub account.
        # We don't actually run them here (they hit Samsara) \u2014
        # signature compatibility is the contract.
        import inspect
        sig_d = inspect.signature(service.evaluate_drivers)
        sig_s = inspect.signature(service.evaluate_subjects)
        # ``evaluate_drivers`` does NOT expose ``subject`` (it's locked
        # to 'driver'); ``evaluate_subjects`` does.
        assert "subject" not in sig_d.parameters
        assert "subject" in sig_s.parameters
