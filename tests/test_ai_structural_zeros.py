"""Guard: a query that can never match must not be answerable as "none".

The AI tool audit's highest-harm cluster. Each of these answered zero
for a reason that had nothing to do with the fleet, and the model
narrated zero as a clean bill of health — on faults, on warning lamps,
on trucks that had been sitting for a week.

Production confirmed every one before it was changed:

* ``vehicle_fault_live`` held 33 rows and not one ``has_critical``,
  because the ingest derived its critical set from a ``_severity`` key
  the Samsara client never stamps.
* unresolved parking events topped out at 65 hours — under three days —
  while 5,189 resolved ones reached thirteen, so ``min_days=7`` (the
  value the tool's own description tells the model to pass) could only
  return nothing.
"""

import pytest

from features.vehicles.severity import classify_is_critical, lamps_are_critical


class TestBothLampShapesMeanCritical:
    """Live Samsara names the lamps; the warehouse reader only knows a
    COUNT of critical DTCs and synthesises ``{"red": True}``. A reader
    that knew one spelling reported every warehouse-served fleet as
    having zero critical faults."""

    def test_live_lamp_names(self):
        for lamp in ("stopIsOn", "protectIsOn", "emissionsIsOn"):
            assert lamps_are_critical({lamp: True}), lamp
            assert classify_is_critical({"_lights": {lamp: True}}), lamp

    def test_warehouse_synthesised_shape(self):
        assert lamps_are_critical({"red": True})
        assert classify_is_critical({"_lights": {"red": True}})

    def test_a_raw_fault_payload_needs_no_reshaping(self):
        vehicle = {"fault_codes": {"j1939": {"checkEngineLights": {"red": True}}}}
        assert classify_is_critical(vehicle)

    def test_nothing_on_is_not_critical(self):
        assert not lamps_are_critical({})
        assert not lamps_are_critical(None)
        assert not classify_is_critical({"_lights": {}, "_dtcs": []})
        assert not classify_is_critical({})

    def test_a_most_severe_dtc_is_still_critical(self):
        assert classify_is_critical(
            {"_lights": {}, "_dtcs": [{"fmiDescription": "Most Severe"}]}
        )


class TestTheIngestClassifiesInsteadOfAssuming:
    def test_the_critical_set_is_derived_from_the_lamps(self):
        """The ingest used to select on ``_severity``, which the CLIENT
        never stamps — only the service wrapper does, and the ingest
        calls the client directly. So the set was always empty, every
        row was written has_critical=0, and the critical tier vanished
        from the product."""
        import inspect

        from capabilities.integrations.samsara import sync

        src = inspect.getsource(sync.ingest_vehicle_faults)
        assert "classify_is_critical" in src, (
            "the ingest must classify, not read a key the client does not set"
        )
        assert 'v.get("_severity") == "critical"' not in src


class TestOverviewReportsWhatItCannotKnow:
    @pytest.mark.asyncio
    async def test_a_health_failure_is_not_a_count_of_zero(self, monkeypatch):
        """A bare except returning 0 handed the model a fabricated
        all-clear with ok:true."""
        import features.overview.ai_tool as mod

        async def _boom(*a, **k):
            raise RuntimeError("health source down")

        async def _fleet(*a, **k):
            return []

        monkeypatch.setattr(mod, "_svc_health", _boom)
        monkeypatch.setattr(mod, "_svc_fleet", _fleet, raising=False)

        res = await mod.get_account_stats({}, None, account_id=1, db=object())
        assert "vehicles_with_health_alerts" not in res, (
            "an unavailable source must be omitted, never counted as zero"
        )
        assert res.get("health_unavailable")


class TestParkingReadsTheHistory:
    @pytest.mark.asyncio
    async def test_a_stop_that_has_ended_still_answers_the_question(self):
        """The tracker resolves a stop the moment the truck moves, so
        the ACTIVE table only ever holds short stays. Asking for a week
        against it could only answer zero."""
        from features.parking.ai_tool import get_parked_vehicles

        class _DB:
            async def get_active_parking_events(self, account_id, attention_only=True):
                return [{"id": 1, "vehicle_name": "SHORT", "duration_hours": 20,
                         "location_class": "unsafe"}]

            async def get_parking_history(self, account_id, days=0, limit=50):
                return [{"id": 2, "vehicle_name": "NINE-DAYS",
                         "duration_hours": 216, "location_class": "unsafe"}]

        res = await get_parked_vehicles(
            {"min_days": 7}, None, account_id=1, db=_DB())
        names = [v["vehicle"] for v in res["vehicles"]]
        assert names == ["NINE-DAYS"], (
            "a nine-day stop that has since ended is exactly what "
            "'which trucks sat more than a week' is asking about"
        )
        assert res["still_parked_now"] == 0
        assert res["vehicles"][0]["still_parked"] is False
        # The window searched is stated, so the model cannot imply it
        # looked at all of history.
        assert res["window_days"] >= 8

    @pytest.mark.asyncio
    async def test_a_truck_parked_right_now_is_marked_as_such(self):
        from features.parking.ai_tool import get_parked_vehicles

        class _DB:
            async def get_active_parking_events(self, account_id, attention_only=True):
                return [{"id": 1, "vehicle_name": "STILL-THERE",
                         "duration_hours": 60, "location_class": "unsafe"}]

            async def get_parking_history(self, account_id, days=0, limit=50):
                return []

        res = await get_parked_vehicles(
            {"min_days": 2}, None, account_id=1, db=_DB())
        assert res["still_parked_now"] == 1
        assert res["vehicles"][0]["still_parked"] is True
