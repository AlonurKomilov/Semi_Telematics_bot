"""Alert triggers — the catalog's promises and the row's rules.

A trigger is one person's sentence: "tell me when DEF drops below 10%".
What makes it safe is everything the person does NOT choose — the catalog
owns direction, re-arm band, freshness, cadence and whether a reading
means anything with the engine off.  These tests pin the parts that would
fail silently: a metric naming a column the warehouse no longer has, a
threshold nobody could ever cross, a reading trusted when it should not
be.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.alerting.triggers import catalog as cat
from capabilities.alerting.triggers.models import (
    MAX_TRIGGERS_PER_USER, AlertTrigger, validate,
)


class TestCatalog:
    def test_every_metric_names_a_real_warehouse_column(self):
        """The catalog is the only place that knows a metric maps to a
        column.  If the warehouse renames one, this is what says so —
        otherwise every trigger on that metric silently reads NULL and
        stops firing, which looks exactly like a quiet fleet."""
        from adapters.storage import platform_schema  # noqa: F401  import guard

        # The columns the minute tier is contracted to carry.  Kept here
        # deliberately rather than introspected: a test that reads the
        # live schema would agree with a rename instead of failing on it.
        MINUTE_COLUMNS = {
            "vehicle_id", "vehicle_name", "captured_at", "source_ts",
            "engine_state", "speed_mph", "fuel_pct", "def_pct",
            "odometer_mi", "engine_hours", "fault_count",
            "dtc_critical_count", "battery_v", "oil_psi", "coolant_c",
            "engine_load_pct", "rpm",
        }
        for m in cat.CATALOG:
            assert m.source == cat.MINUTE, f"{m.key} reads an unexpected tier"
            assert m.column in MINUTE_COLUMNS, (
                f"{m.key} names column {m.column!r}, which the minute tier "
                "does not carry — triggers on it would read NULL forever")

    def test_columns_needed_always_asks_for_the_judging_fields(self):
        """A sweep cannot judge a reading without knowing when it was taken
        and whether the engine was running."""
        cols = set(cat.columns_needed(["fuel_pct"]))
        assert {"source_ts", "engine_state", "vehicle_id"} <= cols
        assert "fuel_pct" in cols

    def test_direction_is_pinned_not_chosen(self):
        """Every metric declares which way it matters.  'Fuel above 26%'
        is not a control anyone needs and cannot be expressed."""
        assert cat.get_metric("fuel_pct").direction == "below"
        assert cat.get_metric("coolant_c").direction == "above"
        assert all(m.direction in ("below", "above") for m in cat.CATALOG)

    def test_the_catalog_is_a_whitelist(self):
        assert cat.get_metric("odometer_mi") is None, (
            "odometer is monotonic — once in breach, permanently in breach")
        assert cat.get_metric("speed_mph") is None, (
            "speeding is event-shaped; as a level it fires on every "
            "truck on every highway")
        assert cat.get_metric("../../etc/passwd") is None
        assert cat.get_metric("") is None

    def test_engine_gated_metrics_are_exactly_the_running_ones(self):
        """Measured on a real fleet over 24h: engine OFF averages 11.0V and
        14.4psi, running averages 13.8V and 42psi.  A resting battery reads
        low because nothing is charging it — so a 'battery below 12V'
        trigger without this gate DMs about every parked truck nightly."""
        gated = {m.key for m in cat.CATALOG if m.requires_engine == "on"}
        assert gated == {"battery_v", "oil_psi", "coolant_c"}
        # A tank level is valid parked; gating it would lose real alerts.
        assert cat.get_metric("fuel_pct").requires_engine is None
        assert cat.get_metric("def_pct").requires_engine is None

    def test_running_metrics_expire_faster_than_levels(self):
        """An hour-old oil-pressure reading says nothing about now; an
        hour-old fuel level still does."""
        assert cat.get_metric("oil_psi").stale_after_minutes <= 60
        assert cat.get_metric("coolant_c").stale_after_minutes <= 60
        assert cat.get_metric("fuel_pct").stale_after_minutes > 60

    def test_hysteresis_is_absolute_and_never_zero(self):
        """Absolute, not a percentage: 5% of 12.6V and 5% of 190°C are
        different physics, and a percentage collapses near zero.  Zero
        would let a reading sitting on the line alert on every sweep."""
        for m in cat.CATALOG:
            assert m.hysteresis > 0, m.key


class TestReadingUsable:
    def test_a_dropout_is_not_a_breach(self):
        """Battery reports a flat 0.0V often enough that treating it as a
        reading would fire every below-trigger on the fleet at once."""
        m = cat.get_metric("battery_v")
        assert cat.reading_usable(m, 0.0, "moving") is False
        assert cat.reading_usable(m, 12.4, "moving") is True

    def test_missing_and_unparseable_are_not_readings(self):
        m = cat.get_metric("fuel_pct")
        assert cat.reading_usable(m, None, "moving") is False
        assert cat.reading_usable(m, "", "moving") is False

    def test_engine_off_is_not_evidence_for_a_running_metric(self):
        m = cat.get_metric("oil_psi")
        assert cat.reading_usable(m, 2.0, "off") is False
        assert cat.reading_usable(m, 2.0, "idle") is True
        assert cat.reading_usable(m, 2.0, "moving") is True
        # A blank engine state is not a claim that the engine runs.
        assert cat.reading_usable(m, 2.0, "") is False

    def test_a_level_metric_is_valid_parked(self):
        assert cat.reading_usable(cat.get_metric("fuel_pct"), 24.0, "off") is True


class TestCrossing:
    def test_breach_follows_the_metric_direction(self):
        fuel, coolant = cat.get_metric("fuel_pct"), cat.get_metric("coolant_c")
        assert cat.breaches(fuel, 24, 26) is True
        assert cat.breaches(fuel, 28, 26) is False
        assert cat.breaches(coolant, 110, 105) is True
        assert cat.breaches(coolant, 100, 105) is False

    def test_recovery_needs_the_whole_band_not_just_the_line(self):
        """Sitting exactly on the threshold must NOT re-arm: that is the
        flapping the band exists to prevent."""
        fuel = cat.get_metric("fuel_pct")          # hysteresis 5
        assert cat.recovered(fuel, 26, 26) is False
        assert cat.recovered(fuel, 30, 26) is False
        assert cat.recovered(fuel, 31, 26) is True

    def test_recovery_for_an_above_metric_runs_the_other_way(self):
        coolant = cat.get_metric("coolant_c")      # hysteresis 5
        assert cat.recovered(coolant, 105, 105) is False
        assert cat.recovered(coolant, 99, 105) is True


class TestValidate:
    def test_an_unknown_metric_is_refused(self):
        assert "not a metric" in validate("cpu_temp", 10)

    def test_a_threshold_that_could_never_fire_is_refused(self):
        """Fuel below 1% is a control that does nothing — the tank hits
        empty first."""
        err = validate("fuel_pct", 1)
        assert err and "never fire" in err

    def test_a_threshold_that_fires_on_everything_is_refused(self):
        err = validate("fuel_pct", 95)
        assert err and "almost every vehicle" in err

    def test_the_number_the_owner_asked_for_is_allowed(self):
        assert validate("fuel_pct", 26) == ""
        assert validate("def_pct", 10) == ""
        assert validate("battery_v", 12.4) == ""

    def test_account_scope_is_refused_with_its_reason(self):
        err = validate("fuel_pct", 26, scope="account")
        assert "not available yet" in err and "Alerts board" in err

    def test_non_numeric_thresholds_are_refused(self):
        assert "has to be a number" in validate("fuel_pct", "twenty-six")


class TestDescribe:
    def test_a_trigger_reads_as_a_sentence(self):
        t = AlertTrigger(id=1, account_id=1, owner_user_id=2,
                         metric="def_pct", threshold=10.0)
        assert t.describe() == "DEF level below 10%"

    def test_a_whole_number_loses_its_decimal_point(self):
        t = AlertTrigger(id=1, account_id=1, owner_user_id=2,
                         metric="fuel_pct", threshold=26.0)
        assert "26%" in t.describe() and "26.0" not in t.describe()

    def test_a_retired_metric_still_renders(self):
        """A row naming a metric the catalog dropped must stay visible so
        its owner can delete it — vanishing rows are how people conclude
        the product ate their settings."""
        t = AlertTrigger(id=1, account_id=1, owner_user_id=2,
                         metric="gone", threshold=5.0)
        assert t.spec is None
        assert "gone" in t.describe()


class TestCap:
    def test_there_is_a_cap_and_it_is_modest(self):
        assert 5 <= MAX_TRIGGERS_PER_USER <= 50
