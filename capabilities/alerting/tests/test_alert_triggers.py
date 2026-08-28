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
        #
        # This list is only as good as its accuracy, and the first draft
        # proved it: it included ``vehicle_name``, which the minute tier
        # does NOT carry (that is a live-tier column).  The test passed,
        # and every sweep in production died on
        # ``column "vehicle_name" does not exist`` — caught per account,
        # logged, and therefore silent.  TestQueryShape below is the
        # companion that would have failed instead of agreeing.
        MINUTE_COLUMNS = {
            "vehicle_id", "captured_at", "source_ts",
            "engine_state", "speed_mph", "fuel_pct", "def_pct",
            "odometer_mi", "engine_hours", "fault_count",
            "dtc_critical_count", "battery_v", "oil_psi", "coolant_c",
            "engine_load_pct", "rpm", "lat", "lon", "last_driver_id",
            "registry_id", "account_id",
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

    def test_columns_needed_never_asks_for_a_display_name(self):
        """The minute tier is keyed by id and carries no name.  Asking
        for one killed every sweep, silently, because the failure is
        caught per account and logged.  The name comes from the live
        tier instead."""
        for keys in ([], ["fuel_pct"], list(cat.metric_keys())):
            assert "vehicle_name" not in cat.columns_needed(keys)

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

    def test_account_scope_names_the_check_that_already_covers_it(self):
        """A generic "not supported" reads as a missing feature.  The
        person asked for something reasonable that the product ALREADY
        does for them, and saying which check does it is the difference
        between a refusal and an answer."""
        fuel = validate("fuel_pct", 26, scope="account")
        assert "fuel check" in fuel and "20%" in fuel
        assert "Your own" in fuel, "and it must say what still works"
        deff = validate("def_pct", 10, scope="account")
        assert "health check" in deff and "10%" in deff
        coolant = validate("coolant_c", 105, scope="account")
        assert "fault code" in coolant or "ECU" in coolant

    def test_a_metric_nothing_else_watches_is_refused_for_the_other_reason(self):
        """Battery and oil have no producer anywhere — they are gated by
        the BOARD's condition, not by a duplicate.  Two different
        refusals because they lift at different times."""
        for metric, value in (("battery_v", 12.4), ("oil_psi", 20)):
            err = validate(metric, value, scope="account")
            assert "repeat-collapsing" in err, metric
            assert "already" not in err.split("—")[0], (
                f"{metric} must not be refused as a duplicate — nothing "
                "else watches it")

    def test_the_catalog_carries_the_verdict_not_this_module(self):
        """The map lives with the metric so the next reader finds it
        beside the thing it describes."""
        assert cat.get_metric("battery_v").account_scope == "allow"
        assert cat.get_metric("oil_psi").account_scope == "allow"
        for k in ("fuel_pct", "def_pct", "coolant_c"):
            m = cat.get_metric(k)
            assert m.account_scope == "refuse", k
            assert m.account_refused_because, f"{k} refuses without saying why"

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


class TestQueryShape:
    """Run the sweep's ACTUAL queries against a real schema.

    The hand-kept column list above is a contract, and a contract can be
    wrong: the first draft asserted the minute tier carries
    ``vehicle_name``, it does not, and every production sweep died on
    ``column "vehicle_name" does not exist`` while this file stayed
    green.  The failure was invisible because the sweep catches per
    account and logs.

    These execute the real SQL.  A column that is not there fails here,
    loudly, instead of at 3am in a log nobody is reading.
    """

    async def test_the_sweep_query_runs_against_the_real_tier(self, pg_db):
        from capabilities.alerting.triggers import catalog as c
        from capabilities.alerting.triggers.evaluator import _latest_per_vehicle
        # Every metric at once — the widest column set a sweep can ask for.
        rows = await _latest_per_vehicle(
            pg_db, 10_000_001, c.columns_needed(c.metric_keys()))
        assert rows == [] or isinstance(rows[0], dict)

    async def test_the_name_lookup_runs_against_the_real_tier(self, pg_db):
        """Names come from the LIVE tier, which is a different table with
        different columns — so it needs its own execution, not an
        assumption that whatever worked above works here."""
        from capabilities.alerting.triggers.evaluator import _names_for
        names = await _names_for(pg_db, 10_000_001)
        assert isinstance(names, dict)

    async def test_a_metric_column_that_vanished_is_caught_here(self, pg_db):
        """Proof this test can fail: ask for a column nothing carries and
        the query must raise, which is what makes the two tests above
        evidence rather than decoration."""
        import pytest as _pytest
        from capabilities.alerting.triggers.evaluator import _latest_per_vehicle
        with _pytest.raises(Exception):
            await _latest_per_vehicle(
                pg_db, 10_000_001, ["vehicle_id", "a_column_that_never_existed"])


class TestChannels:
    """Where one trigger goes, decided per TRIGGER and not per category.

    The notification matrix has one row per alert type, shared by everyone
    who receives it — it can say "faults reach me by email" but never "DEF
    reaches my phone while battery waits for email".  That is the whole
    reason a trigger carries its own channel list, and these pin the two
    rules that make it safe: the bell is not optional, and an unknown
    channel loses its own tick rather than the whole save.
    """

    def test_the_bell_is_always_first_and_never_stored(self):
        from capabilities.alerting.triggers.models import ALWAYS
        t = AlertTrigger(id=1, account_id=1, owner_user_id=1,
                         metric="fuel_pct", threshold=20, channels="email")
        assert t.delivery_channels == [ALWAYS, "email"]
        # Stored form stays exactly what was asked for — the bell is
        # prepended at delivery, so nothing can un-tick it by writing the
        # column.
        assert t.channels == "email"

    def test_a_trigger_with_no_extra_channels_still_reaches_the_bell(self):
        """Bell-only is legal and means the record exists but nothing
        buzzes — a trigger that fired and left no trace is
        indistinguishable from one that never fired."""
        from capabilities.alerting.triggers.models import ALWAYS
        t = AlertTrigger(id=1, account_id=1, owner_user_id=1,
                         metric="fuel_pct", threshold=20, channels="")
        assert t.delivery_channels == [ALWAYS]

    def test_an_unknown_channel_is_dropped_not_refused(self):
        from capabilities.alerting.triggers.models import clean_channels
        assert clean_channels(["email", "carrier_pigeon"]) == "email"
        # Order is the CATALOG's, not the caller's — two clients sending
        # the same set must store the same string, or "did this change?"
        # becomes unanswerable.
        assert clean_channels(["email", "telegram_dm"]) == \
            clean_channels(["telegram_dm", "email"])

    def test_the_default_is_one_definition(self):
        """The column default, the dataclass default and the create route
        all mean the same set.  A drift here is a trigger that silently
        delivers somewhere the person did not pick."""
        from capabilities.alerting.triggers.models import (
            DEFAULT_CHANNELS, DEFAULT_CHANNELS_CSV, TRIGGER_CHANNELS,
        )
        assert DEFAULT_CHANNELS_CSV == ",".join(DEFAULT_CHANNELS)
        assert all(c in TRIGGER_CHANNELS for c in DEFAULT_CHANNELS)
        t = AlertTrigger(id=1, account_id=1, owner_user_id=1,
                         metric="fuel_pct", threshold=20)
        assert t.channels == DEFAULT_CHANNELS_CSV
        # Push is deliberately absent: it needs a subscribed browser, and
        # a default depending on setup nobody did reads as "I ticked it
        # and nothing came".
        assert "web_push" not in DEFAULT_CHANNELS


class TestFiredHistory:
    """The Triggers tab reads FIRINGS, not sentences.

    Every column it renders is written into the notice at fire time.  The
    trigger it names can be edited or deleted afterwards, so a history
    that re-read today's threshold would quietly rewrite what last week's
    alert said.
    """

    def test_a_firing_is_shaped_from_its_own_meta(self):
        from capabilities.alerting.triggers.router import _fired_shape
        row = {"id": 7, "title": "Truck 12 — fuel level below 30%",
               "body": "Now 24%. Your alert trigger.",
               "severity": "warning", "created_at": "2026-08-26T10:00:00",
               "read_at": ""}
        meta = {"trigger_id": 3, "vehicle": "Truck 12", "vehicle_id": "v1",
                "metric": "fuel_pct", "threshold": 30, "value": 24}
        out = _fired_shape(row, meta)
        assert out["id"] == 7                    # the NOTICE id — marks read
        assert out["vehicle"] == "Truck 12"
        assert out["metric_label"] and out["unit"] == "%"
        assert out["says"] == "Fuel level below 30%"
        assert out["value"] == 24
        assert out["read"] is False

    def test_an_edited_threshold_does_not_rewrite_what_was_said(self):
        """The number in the row is the one that fired, taken from meta —
        the current trigger is never consulted."""
        from capabilities.alerting.triggers.router import _fired_shape
        row = {"id": 8, "title": "Truck 12 — fuel level below 30%", "body": "",
               "severity": "warning", "created_at": "", "read_at": ""}
        out = _fired_shape(row, {"metric": "fuel_pct", "threshold": 30})
        assert "30%" in out["says"]

    def test_a_row_written_before_meta_carried_columns_still_reads(self):
        """Rows already in the inbox have no vehicle or threshold in meta.
        They fall back to the notice text rather than rendering blank —
        a history that empties itself on deploy is worse than one that is
        merely less precise."""
        from capabilities.alerting.triggers.router import _fired_shape
        row = {"id": 9, "title": "Truck 12 — fuel level below 30%",
               "body": "Now 24%.", "severity": "warning",
               "created_at": "", "read_at": ""}
        out = _fired_shape(row, {})
        assert out["vehicle"] == "Truck 12"
        assert out["says"] == "fuel level below 30%"

    def test_a_malformed_meta_does_not_take_the_page_down(self):
        from capabilities.alerting.triggers.router import _meta
        assert _meta("") == {}
        assert _meta("not json") == {}
        assert _meta("[1, 2]") == {}             # valid JSON, wrong shape
        assert _meta('{"metric": "fuel_pct"}') == {"metric": "fuel_pct"}

    def test_a_retired_metric_still_produces_a_readable_row(self):
        """The catalog moved on; the history did not.  The row keeps the
        sentence the notice was written with instead of vanishing."""
        from capabilities.alerting.triggers.router import _fired_shape
        row = {"id": 10, "title": "Truck 3 — tyre pressure below 90psi",
               "body": "", "severity": "warning", "created_at": "", "read_at": ""}
        out = _fired_shape(row, {"metric": "tyre_psi", "threshold": 90})
        assert out["says"] == "tyre pressure below 90psi"
        assert out["metric_label"] == ""

    def test_bell_only_survives_a_round_trip_through_the_row(self):
        """The regression the first version of this suite missed.

        ``clean_channels([])`` stores '', which is the legal bell-only
        choice — but ``from_row`` read it with ``or``, so the empty string
        came back as the default pair.  Every reconstruction did it: the
        PATCH response told the UI the boxes were still ticked, and the
        sweep kept DMing someone who had explicitly unticked everything.
        The earlier test built the dataclass directly and never touched
        the path that was wrong.
        """
        from capabilities.alerting.triggers.models import ALWAYS, clean_channels
        row = {"id": 1, "account_id": 1, "owner_user_id": 1,
               "metric": "fuel_pct", "threshold": 20.0,
               "channels": clean_channels([])}
        assert row["channels"] == ""
        t = AlertTrigger.from_row(row)
        assert t.chosen_channels == []
        assert t.delivery_channels == [ALWAYS]

    def test_a_row_with_no_channels_column_still_gets_the_default(self):
        """The other side of the same coin: MISSING is not EMPTY.  A row
        read before the column existed has no choice recorded, and the
        default is the honest reading of that."""
        from capabilities.alerting.triggers.models import DEFAULT_CHANNELS
        t = AlertTrigger.from_row({"id": 1, "account_id": 1, "owner_user_id": 1,
                                   "metric": "fuel_pct", "threshold": 20.0})
        assert t.chosen_channels == list(DEFAULT_CHANNELS)

    def test_membership_is_parsed_not_substring_matched(self):
        """``"push" in "telegram_dm,web_push"`` is True as a substring and
        false as a fact.  Nothing collides today; the first shorter key
        added would have made every longer row claim it."""
        t = AlertTrigger(id=1, account_id=1, owner_user_id=1,
                         metric="fuel_pct", threshold=20, channels="web_push")
        assert t.chosen_channels == ["web_push"]
        assert "telegram_dm" not in t.chosen_channels


class TestVehicleTargeting:
    """Which vehicles a trigger watches.

    Empty means EVERY vehicle in the owner's scope — the meaning every
    trigger written before this column had, which is why the migration
    needed no backfill.  A selection NARROWS that and can never widen it.
    """

    def test_empty_means_all_not_none(self):
        t = AlertTrigger(id=1, account_id=1, owner_user_id=1,
                         metric="fuel_pct", threshold=30)
        assert t.target_ids == [] and t.targets_all is True

    def test_a_row_predating_the_column_watches_everything(self):
        """If '' did not mean "all", every existing trigger would go
        silent on deploy — the failure nobody reports, because a trigger
        that stops firing looks exactly like a quiet fleet."""
        t = AlertTrigger.from_row({"id": 1, "account_id": 1, "owner_user_id": 1,
                                   "metric": "fuel_pct", "threshold": 30.0})
        assert t.targets_all is True

    def test_ids_are_parsed_deduped_and_junk_dropped(self):
        from capabilities.alerting.triggers.models import clean_vehicle_ids
        assert clean_vehicle_ids([7, "7", " 9 ", "x", -1, 0]) == "7,9"
        t = AlertTrigger(id=1, account_id=1, owner_user_id=1, metric="fuel_pct",
                         threshold=30, vehicles="99, 12,abc,99")
        # Deduped on READ too: the count is a number a person checks
        # their work against, so "3 vehicles" for two would be a lie.
        assert t.target_ids == [99, 12]

    def test_targeting_uses_the_same_ladder_as_the_permission_wall(self):
        from capabilities.permissions.vehicle_scope import VehicleScope
        target = VehicleScope(registry_ids=frozenset({99}),
                              external_ids=frozenset({"dev-new"}))
        assert target.allows_row({"registry_id": 99, "vehicle_id": "dev-old"})
        assert target.allows_row({"registry_id": None, "vehicle_id": "dev-new"})
        assert not target.allows_row({"registry_id": 41, "vehicle_id": "dev-x"})

    def test_a_target_has_no_name_rung(self):
        """Deliberately absent.  Matching a target by NAME is how "230"
        once matched 2303 on a visibility wall — an over-match here is an
        alert about somebody else's truck."""
        from capabilities.permissions.vehicle_scope import VehicleScope
        target = VehicleScope(registry_ids=frozenset({99}))
        assert not target.allows_row(
            {"registry_id": None, "vehicle_id": "", "vehicle_name": "99"})


class TestDeviceCollapse:
    """One row per VEHICLE, where the query gives one row per DEVICE."""

    def test_two_devices_on_one_vehicle_collapse_to_the_newest(self):
        """Live on production: registry id 99 maps to two provider ids.
        A gateway swap re-points telematics_ref on the SAME registry row,
        and the retired device's last reading stays judgeable until the
        metric's staleness bar (24h for fuel)."""
        from capabilities.alerting.triggers.evaluator import _collapse_by_registry
        rows = [
            {"registry_id": 99, "vehicle_id": "old", "source_ts": "2026-08-26T01:00:00Z"},
            {"registry_id": 99, "vehicle_id": "new", "source_ts": "2026-08-26T09:00:00Z"},
        ]
        out = _collapse_by_registry(rows)
        assert len(out) == 1 and out[0]["vehicle_id"] == "new"

    def test_unplaced_rows_are_never_merged_together(self):
        """Three provider ids on the live account carry NULL registry_id.
        Collapsing them into one bucket would silently drop two real
        vehicles — they are unplaced, not the same truck."""
        from capabilities.alerting.triggers.evaluator import _collapse_by_registry
        rows = [{"registry_id": None, "vehicle_id": "a", "source_ts": "1"},
                {"registry_id": None, "vehicle_id": "b", "source_ts": "2"}]
        assert len(_collapse_by_registry(rows)) == 2

    def test_a_single_device_fleet_is_unchanged(self):
        from capabilities.alerting.triggers.evaluator import _collapse_by_registry
        rows = [{"registry_id": i, "vehicle_id": f"v{i}", "source_ts": "1"}
                for i in range(5)]
        assert len(_collapse_by_registry(rows)) == 5


class TestFeatureDimension:
    """"Feature" is DERIVED from the catalog, never stored on the row."""

    def test_every_metric_declares_its_owning_feature(self):
        for m in cat.CATALOG:
            assert m.feature, f"{m.key} has no feature"

    def test_the_trigger_row_stores_no_feature(self):
        """The catalog owns this fact.  A copy on the row would go stale
        the day a metric moves to another feature — and the row has no
        way to know it happened."""
        import dataclasses
        assert "feature" not in {f.name for f in dataclasses.fields(AlertTrigger)}

    def test_registry_id_is_read_so_the_top_rung_is_reachable(self):
        """Without this column VehicleScope's registry rung is dead code
        on the sweep's rows and every verdict falls to the provider id —
        exactly the identifier a gateway swap rewrites."""
        assert "registry_id" in cat.columns_needed(["fuel_pct"])


class TestTheSweepSeesTheSelection:
    """The two reads of a trigger must agree about what it watches.

    ``list_alert_triggers`` is what the dashboard shows; the sweep judges
    ``list_enabled_alert_triggers``.  The first shipped with the
    ``vehicles`` column and the second did not, so a person narrowing a
    trigger to one truck saw "on 1 vehicle" while the sweep fired on
    their whole fleet.

    Silent, because ``from_row`` reads a missing column as '' and '' is
    the legitimate "all my vehicles" default: no value says "this query
    forgot to ask".  So the guard has to EXECUTE the sweep's own query.
    """

    async def test_the_sweep_query_returns_the_vehicle_selection(self, pg_db):
        acct = await pg_db.create_account("Sweep Sees Co")
        user = await pg_db.create_user(telegram_id=9931, account_id=acct.id)
        made = await pg_db.create_alert_trigger(
            acct.id, user.id, metric="fuel_pct", threshold=30.0, vehicles="7,9")
        rows = await pg_db.list_enabled_alert_triggers()
        mine = next(r for r in rows if int(r["id"]) == int(made["id"]))
        assert "vehicles" in mine, (
            "the sweep's query does not select `vehicles` — every targeted "
            "trigger silently watches the whole fleet")
        assert AlertTrigger.from_row(mine).target_ids == [7, 9]

    async def test_both_reads_agree_about_one_trigger(self, pg_db):
        acct = await pg_db.create_account("Agree Co")
        user = await pg_db.create_user(telegram_id=9932, account_id=acct.id)
        made = await pg_db.create_alert_trigger(
            acct.id, user.id, metric="def_pct", threshold=10.0, vehicles="42")
        shown = next(r for r in await pg_db.list_alert_triggers(
            acct.id, owner_user_id=user.id) if int(r["id"]) == int(made["id"]))
        judged = next(r for r in await pg_db.list_enabled_alert_triggers()
                      if int(r["id"]) == int(made["id"]))
        assert (AlertTrigger.from_row(shown).target_ids
                == AlertTrigger.from_row(judged).target_ids == [42])

    async def test_an_untargeted_trigger_still_watches_everything(self, pg_db):
        acct = await pg_db.create_account("All Co")
        user = await pg_db.create_user(telegram_id=9933, account_id=acct.id)
        made = await pg_db.create_alert_trigger(
            acct.id, user.id, metric="fuel_pct", threshold=25.0)
        judged = next(r for r in await pg_db.list_enabled_alert_triggers()
                      if int(r["id"]) == int(made["id"]))
        assert AlertTrigger.from_row(judged).targets_all is True

    def test_the_tenant_router_import_resolves(self):
        """``_validate_targets`` and the vehicle picker both reach for
        ``get_router``.  It lives in ``infra.platform``; importing it from
        ``infra.tenant`` raises at CALL time, inside the function — which
        500s the picker, and with it the whole Add sheet, because the
        metrics fetch shares one Promise.all with the vehicle fetch."""
        from infra.platform import get_router
        assert callable(get_router)
        import infra.tenant
        assert not hasattr(infra.tenant, "get_router"), (
            "get_router now also lives in infra.tenant — if it moved, "
            "update the imports in triggers/router.py to match")


class TestEditingAPair:
    """Editing validates the pair that will EXIST, not the field that moved.

    A trigger is (metric, threshold) — neither half means anything alone.
    Changing only the metric leaves the old number attached to a new band,
    and changing only the number is checked against the metric already
    stored. Validating just the submitted field would let fuel's 30% land
    on battery voltage, whose band is 11–13.5V — never checked, because
    only one field changed.

    Oil pressure is the instructive counter-example: 30 is legal there
    too (5–40 psi), so the guard cannot be "refuse every cross-metric
    move". It has to judge the PAIR, which is why both halves are read
    back and validated together rather than the one that was sent.
    """

    def test_the_old_number_on_a_new_metric_is_refused(self):
        # 30 is a fine fuel percentage and a nonsense battery voltage.
        assert validate("fuel_pct", 30) == ""
        assert validate("battery_v", 30) != ""

    def test_a_number_legal_on_both_is_allowed(self):
        """Not every cross-metric move is wrong — the guard has to let the
        legal ones through, or editing becomes delete-and-recreate with
        extra steps."""
        assert validate("fuel_pct", 20) == ""
        assert validate("def_pct", 20) == ""

    def test_the_pair_is_what_the_catalog_judges(self):
        """Both halves reach `validate` together; there is no code path
        that judges a metric without a number or a number without a
        metric."""
        import inspect
        sig = inspect.signature(validate)
        assert list(sig.parameters)[:2] == ["metric_key", "threshold"]

    def test_an_unknown_metric_is_still_refused_on_edit(self):
        assert "not a metric" in validate("tyre_psi", 90)


class TestCompanyWall:
    """A trigger must not cross the Team Management company wall.

    The vehicle scope narrows an assigned DRIVER and returns None for
    every other role, so on its own it walls nobody who is restricted by
    COMPANY instead — which on the live account is four dispatchers.
    Three seams had to be closed, because each leaks on its own: the
    picker enumerates, the validator stores, and the sweep delivers.
    """

    def test_every_wall_composes_rather_than_flattens(self):
        """Each wall decides on the strongest rung it carries.  Merging
        them into one id set would demote whichever rung the other lacked
        — the company wall knows registry ids, the driver ladder also
        knows external ids and names."""
        from capabilities.alerting.triggers.evaluator import _AllOf
        from capabilities.permissions.vehicle_scope import VehicleScope
        company = VehicleScope(registry_ids=frozenset({1, 2}))
        driver = VehicleScope(registry_ids=frozenset({2, 3}))
        both = _AllOf([company, driver])
        assert both.allows_row({"registry_id": 2, "vehicle_id": "x"})
        assert not both.allows_row({"registry_id": 1, "vehicle_id": "x"})
        assert not both.allows_row({"registry_id": 3, "vehicle_id": "x"})

    def test_an_unrestricted_owner_is_still_unrestricted(self):
        """No company codes and not a driver means no wall — composing an
        empty list must not turn into deny-all."""
        from capabilities.alerting.triggers.evaluator import _AllOf
        assert _AllOf([]).allows_row({"registry_id": 99, "vehicle_id": "z"})

    def test_the_wall_survives_a_row_with_no_registry_id(self):
        """An unplaced row (registry_id NULL, first ingest tick) must not
        pass a registry-keyed company wall by default — it cannot be
        proven to be in an allowed company."""
        from capabilities.permissions.vehicle_scope import VehicleScope
        company = VehicleScope(registry_ids=frozenset({1}))
        assert not company.allows_row({"registry_id": None, "vehicle_id": "new"})


class TestConcurrentEdits:
    """Two PATCHes on one trigger must not compose into a pair nobody
    judged.

    A trigger is (metric, threshold); the route reads both and validates
    the pair that WILL exist. But two requests sending disjoint fields
    each validated against a stale read of the other's column and then
    wrote their own — send {threshold: 45} and {metric: "oil_psi"}
    concurrently against (fuel_pct, 30) and both pass validation, while
    the row lands on (oil_psi, 45): never judged, and outside oil
    pressure's 5–40 band.
    """

    async def test_a_stale_pair_loses_the_race(self, pg_db):
        acct = await pg_db.create_account("Race Co")
        user = await pg_db.create_user(telegram_id=9941, account_id=acct.id)
        made = await pg_db.create_alert_trigger(
            acct.id, user.id, metric="fuel_pct", threshold=30.0)
        tid = int(made["id"])

        # Request A commits first, on the pair it read.
        assert await pg_db.update_alert_trigger(
            acct.id, user.id, tid, threshold=45.0,
            expect_metric="fuel_pct", expect_threshold=30.0)

        # Request B validated against the SAME pre-read pair. Its guard
        # no longer matches, so it writes nothing.
        assert not await pg_db.update_alert_trigger(
            acct.id, user.id, tid, metric="oil_psi",
            expect_metric="fuel_pct", expect_threshold=30.0)

        rows = await pg_db.list_alert_triggers(acct.id, owner_user_id=user.id)
        row = next(r for r in rows if int(r["id"]) == tid)
        assert str(row["metric"]) == "fuel_pct" and float(row["threshold"]) == 45.0

    async def test_an_unguarded_patch_still_writes(self, pg_db):
        """enabled/channels read neither column, so they have nothing to
        be stale about and must not be made to fail on someone else's
        edit."""
        acct = await pg_db.create_account("Unguarded Co")
        user = await pg_db.create_user(telegram_id=9942, account_id=acct.id)
        made = await pg_db.create_alert_trigger(
            acct.id, user.id, metric="fuel_pct", threshold=30.0)
        tid = int(made["id"])
        await pg_db.update_alert_trigger(
            acct.id, user.id, tid, threshold=25.0,
            expect_metric="fuel_pct", expect_threshold=30.0)
        # No expectations passed — succeeds against the moved row.
        assert await pg_db.update_alert_trigger(
            acct.id, user.id, tid, enabled=False)

    async def test_the_guard_still_scopes_to_the_owner(self, pg_db):
        """The new predicate is additive — it must not become the only
        wall and let another account's row through."""
        acct = await pg_db.create_account("Guard Scope Co")
        user = await pg_db.create_user(telegram_id=9943, account_id=acct.id)
        made = await pg_db.create_alert_trigger(
            acct.id, user.id, metric="fuel_pct", threshold=30.0)
        assert not await pg_db.update_alert_trigger(
            acct.id, user.id + 999, int(made["id"]), threshold=20.0,
            expect_metric="fuel_pct", expect_threshold=30.0)
