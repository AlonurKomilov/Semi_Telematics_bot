"""The sweep: what fires, what stays quiet, and what a new trigger does.

These pin the four behaviours that separate a useful trigger from a
nuisance:

  • a crossing is announced ONCE, not on every sweep while it holds,
  • a reading that recovers only to the line does not re-arm,
  • a brand-new trigger inherits the fleet's state instead of announcing
    all of it,
  • not knowing (stale / implausible / engine off) produces silence, and
    silence never leaves a flag behind that would swallow the real
    crossing later.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.alerting.triggers import evaluator as ev
from capabilities.alerting.triggers.models import AlertTrigger


def _trigger(tid=1, metric="fuel_pct", threshold=26.0, account_id=42, owner=7):
    return AlertTrigger(id=tid, account_id=account_id, owner_user_id=owner,
                        metric=metric, threshold=threshold)


def _row(vid="v1", name="Truck 1", engine="moving", ts="__now__", **metrics):
    from datetime import datetime, timezone
    if ts == "__now__":
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"vehicle_id": vid, "vehicle_name": name, "engine_state": engine,
            "source_ts": ts, "captured_at": ts, **metrics}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No Redis, no tenant DB, no delivery — the crossing logic is what is
    under test, and the process-local flag store is a faithful stand-in
    for the Redis one (the evaluator uses whichever is available)."""
    ev._local_flags.clear()
    monkeypatch.setattr(ev.rcache, "is_available", lambda: False)
    sent: list[dict] = []

    async def fake_deliver(account_id, trig, row, value):
        sent.append({"account_id": account_id, "trigger": trig.id,
                     "vehicle": row.get("vehicle_id"), "value": value})
        # True = delivered.  ``fired`` counts what reached someone, not
        # what was attempted, so a stub that returned None would make
        # every fire look like a delivery failure.
        return True

    monkeypatch.setattr(ev, "_deliver", fake_deliver)

    # Unrestricted owner by default: these tests are about the CROSSING
    # logic, and scope has its own class below.  Left unstubbed the
    # resolver would fail closed (no platform DB here) and silence
    # everything — which is the guard working, not the subject.
    async def unrestricted(tenant, account_id, owner_user_id):
        return None

    monkeypatch.setattr(ev, "_owner_scope", unrestricted)
    return sent


def _sweep(monkeypatch, rows, triggers, tick=15):
    """Run one account evaluation against a fixed fleet snapshot."""
    async def fake_latest(tenant, account_id, columns):
        return rows

    async def fake_tenant(account_id):
        return object()

    monkeypatch.setattr(ev, "_latest_per_vehicle", fake_latest)
    monkeypatch.setattr(ev, "get_tenant_db", fake_tenant)
    import asyncio
    return asyncio.run(ev.evaluate_account(42, triggers, tick))


class TestSeeding:
    def test_a_new_trigger_inherits_the_fleet_instead_of_announcing_it(
            self, monkeypatch, _isolate):
        """Create a fuel trigger at 26% when half the fleet is already
        below it and you should hear nothing — you asked to be told when a
        truck CROSSES, not what was true when you pressed Save."""
        rows = [_row(f"v{i}", fuel_pct=20) for i in range(5)]
        stats = _sweep(monkeypatch, rows, [_trigger()])
        assert stats["seeded"] == 5 and stats["fired"] == 0
        assert _isolate == []

    def test_the_next_truck_to_cross_does_fire(self, monkeypatch, _isolate):
        below = [_row("v1", fuel_pct=20)]
        _sweep(monkeypatch, below, [_trigger()])          # seed
        _isolate.clear()
        # A second truck arrives already below — it crossed after seeding.
        rows = below + [_row("v2", fuel_pct=18)]
        stats = _sweep(monkeypatch, rows, [_trigger()])
        assert stats["fired"] == 1
        assert [s["vehicle"] for s in _isolate] == ["v2"]


class TestCrossingOnce:
    def test_a_held_breach_is_announced_once(self, monkeypatch, _isolate):
        trig = [_trigger()]
        _sweep(monkeypatch, [_row("v1", fuel_pct=30)], trig)     # seed, no breach
        _isolate.clear()
        first = _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)
        second = _sweep(monkeypatch, [_row("v1", fuel_pct=23)], trig)
        third = _sweep(monkeypatch, [_row("v1", fuel_pct=22)], trig)
        assert first["fired"] == 1
        assert second["fired"] == 0 and third["fired"] == 0
        assert len(_isolate) == 1, "a truck sitting low must not DM every sweep"

    def test_recovery_to_the_line_does_not_re_arm(self, monkeypatch, _isolate):
        """Threshold 26, band 5.  Back at 26 is still the line; only past
        31 is a genuine recovery — otherwise a truck hovering there alerts
        all day."""
        trig = [_trigger()]
        _sweep(monkeypatch, [_row("v1", fuel_pct=30)], trig)
        _isolate.clear()
        _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)      # fires
        _sweep(monkeypatch, [_row("v1", fuel_pct=26)], trig)      # on the line
        again = _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)
        assert again["fired"] == 0
        assert len(_isolate) == 1

    def test_a_real_recovery_re_arms_and_the_next_dip_fires(
            self, monkeypatch, _isolate):
        trig = [_trigger()]
        _sweep(monkeypatch, [_row("v1", fuel_pct=30)], trig)
        _isolate.clear()
        _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)      # fires
        cleared = _sweep(monkeypatch, [_row("v1", fuel_pct=80)], trig)  # refuelled
        _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)      # fires again
        assert cleared["cleared"] == 1
        assert len(_isolate) == 2, "a fresh tank then a fresh dip is real news"


class TestSilenceBeatsAGuess:
    def test_a_stale_reading_is_skipped(self, monkeypatch, _isolate):
        """A truck that read 24% three days ago has probably refuelled —
        firing on that asserts a fact nobody has."""
        trig = [_trigger()]
        old = _row("v1", fuel_pct=24, ts="2020-01-01T00:00:00+00:00")
        stats = _sweep(monkeypatch, [old], trig)
        assert stats["skipped"] == 1 and stats["fired"] == 0

    def test_an_implausible_reading_is_not_a_breach(self, monkeypatch, _isolate):
        """Battery reports a flat 0.0 V on dropout.  Treating it as a
        reading would fire on the whole fleet at once."""
        trig = [_trigger(metric="battery_v", threshold=12.0)]
        stats = _sweep(monkeypatch, [_row("v1", battery_v=0.0)], trig, tick=1)
        assert stats["skipped"] == 1 and stats["fired"] == 0

    def test_engine_off_is_not_low_oil_pressure(self, monkeypatch, _isolate):
        """A parked engine reads near zero psi.  Without the engine gate
        every parked truck would look like an oil-pressure emergency."""
        trig = [_trigger(metric="oil_psi", threshold=20.0)]
        parked = _sweep(monkeypatch, [_row("v1", engine="off", oil_psi=2.0)],
                        trig, tick=1)
        assert parked["skipped"] == 1 and parked["fired"] == 0

    def test_silence_leaves_no_flag_that_would_swallow_a_real_crossing(
            self, monkeypatch, _isolate):
        """The subtle one: if a skipped vehicle were marked in-breach, the
        genuine crossing that follows would be read as 'already said'."""
        trig = [_trigger()]
        _sweep(monkeypatch, [_row("v1", fuel_pct=30)], trig)          # seed
        _isolate.clear()
        _sweep(monkeypatch, [_row("v1", fuel_pct=24, ts="2020-01-01T00:00:00+00:00")],
               trig)                                                  # stale, skipped
        real = _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)   # fresh, low
        assert real["fired"] == 1


class TestIndependence:
    def test_two_people_on_different_numbers_do_not_interfere(
            self, monkeypatch, _isolate):
        """The reason a trigger id is the dedup key: watching fuel at 26
        and at 15 are separate conditions with separate histories."""
        mine = _trigger(tid=1, threshold=26.0, owner=7)
        theirs = _trigger(tid=2, threshold=15.0, owner=8)
        both = [mine, theirs]
        _sweep(monkeypatch, [_row("v1", fuel_pct=40)], both)          # seed
        _isolate.clear()
        _sweep(monkeypatch, [_row("v1", fuel_pct=24)], both)          # only mine
        assert [s["trigger"] for s in _isolate] == [1]
        _sweep(monkeypatch, [_row("v1", fuel_pct=14)], both)          # now theirs
        assert sorted(s["trigger"] for s in _isolate) == [1, 2]


class TestCadence:
    def test_a_metric_is_judged_on_its_own_period(self):
        """Coolant is worth re-reading every sweep; fuel is not — a tank
        does not drop ten percent in five minutes."""
        from capabilities.alerting.triggers.catalog import get_metric
        coolant, fuel = get_metric("coolant_c"), get_metric("fuel_pct")
        assert ev._due(coolant, 1) and ev._due(coolant, 2)
        assert ev._due(fuel, 3) and not ev._due(fuel, 1) and not ev._due(fuel, 2)

    def test_a_metric_not_due_is_not_read_at_all(self, monkeypatch, _isolate):
        trig = [_trigger()]                       # fuel: every 3rd sweep
        stats = _sweep(monkeypatch, [_row("v1", fuel_pct=10)], trig, tick=1)
        assert stats == {"fired": 0, "cleared": 0, "skipped": 0, "seeded": 0}


class TestSweepGrouping:
    def test_each_account_is_judged_against_its_own_fleet_only(
            self, monkeypatch, _isolate):
        """``list_enabled_alert_triggers`` is a platform-wide query — every
        account's rows in one list.  The grouping before evaluation is the
        only thing standing between that and one tenant's trigger being
        judged against another tenant's trucks."""
        import asyncio

        rows = [
            {"id": 1, "account_id": 10, "owner_user_id": 1, "metric": "fuel_pct",
             "threshold": 26.0, "scope": "personal", "origin": "user",
             "enabled": 1, "severity": "warning"},
            {"id": 2, "account_id": 20, "owner_user_id": 2, "metric": "fuel_pct",
             "threshold": 26.0, "scope": "personal", "origin": "user",
             "enabled": 1, "severity": "warning"},
        ]

        class _DB:
            async def list_enabled_alert_triggers(self):
                return rows

        seen: list[tuple[int, list[int]]] = []

        async def fake_evaluate(account_id, triggers, tick):
            seen.append((account_id, [t.id for t in triggers]))
            return {"fired": 0, "cleared": 0, "skipped": 0, "seeded": 0}

        async def fake_run(coro, **kw):
            await coro
            return True

        monkeypatch.setattr(ev, "get_platform_db", lambda: _DB())
        monkeypatch.setattr(ev, "evaluate_account", fake_evaluate)
        monkeypatch.setattr(ev, "run_account_job", fake_run)
        monkeypatch.setattr(ev, "get_tenant_db", lambda a: _noop())
        asyncio.run(ev.sweep_alert_triggers(None))

        assert sorted(seen) == [(10, [1]), (20, [2])], seen

    def test_the_sweep_runs_each_account_through_the_isolation_wrapper(
            self, monkeypatch, _isolate):
        """Not bookkeeping: run_account_job enters ``with_account()``,
        which sets app.account_id for Postgres RLS.  Skip it and the
        sweep's query matches zero rows under RLS — the feature goes
        silently dead with no error to find."""
        import asyncio

        class _DB:
            async def list_enabled_alert_triggers(self):
                return [{"id": 1, "account_id": 10, "owner_user_id": 1,
                         "metric": "fuel_pct", "threshold": 26.0,
                         "scope": "personal", "origin": "user",
                         "enabled": 1, "severity": "warning"}]

        wrapped: list[dict] = []

        async def fake_run(coro, **kw):
            wrapped.append(kw)
            await coro
            return True

        async def fake_evaluate(account_id, triggers, tick):
            return {"fired": 0, "cleared": 0, "skipped": 0, "seeded": 0}

        monkeypatch.setattr(ev, "get_platform_db", lambda: _DB())
        monkeypatch.setattr(ev, "evaluate_account", fake_evaluate)
        monkeypatch.setattr(ev, "run_account_job", fake_run)
        monkeypatch.setattr(ev, "get_tenant_db", lambda a: _noop())
        asyncio.run(ev.sweep_alert_triggers(None))

        assert len(wrapped) == 1
        assert wrapped[0]["account_id"] == 10
        assert wrapped[0]["tenant_db"] is not None, (
            "without the tenant handle run_account_job cannot enter "
            "with_account(), and RLS sees no rows")


class TestLongBreach:
    def test_a_breach_outliving_the_flag_ttl_does_not_re_announce(
            self, monkeypatch, _isolate):
        """A tank sitting low over a long weekend must stay quiet.  The
        flag is renewed on every sweep it is still in breach — without
        that renewal it expires and the next sweep reads a fresh
        crossing, which is precisely what this state prevents."""
        trig = [_trigger()]
        _sweep(monkeypatch, [_row("v1", fuel_pct=30)], trig)
        _isolate.clear()
        _sweep(monkeypatch, [_row("v1", fuel_pct=24)], trig)      # fires once
        renewals = []
        real_set = ev._set_breach

        async def counting_set(key, breached):
            if breached:
                renewals.append(key)
            await real_set(key, breached)

        monkeypatch.setattr(ev, "_set_breach", counting_set)
        for _ in range(5):
            _sweep(monkeypatch, [_row("v1", fuel_pct=23)], trig)
        assert len(_isolate) == 1, "a held breach must be announced once"
        assert len(renewals) == 5, "and its flag renewed on every sweep"


async def _noop():
    return object()


class TestOwnerScope:
    """A trigger is one person's, so it sees one person's fleet."""

    def test_a_restricted_owner_hears_only_about_their_own_trucks(
            self, monkeypatch, _isolate):
        """Without this a driver assigned one truck would be DM'd about
        all 102 — vehicles they cannot even open in the dashboard, which
        is a disclosure and not merely noise."""
        class _Scope:
            def allows_row(self, row, **kw):
                return str(row.get("vehicle_name")) == "Truck 1"

        async def scoped(tenant, account_id, owner_user_id):
            return _Scope()

        monkeypatch.setattr(ev, "_owner_scope", scoped)
        trig = [_trigger()]
        rows = [_row("v1", name="Truck 1", fuel_pct=40),
                _row("v2", name="Truck 2", fuel_pct=40)]
        _sweep(monkeypatch, rows, trig)                       # seed
        _isolate.clear()
        low = [_row("v1", name="Truck 1", fuel_pct=24),
               _row("v2", name="Truck 2", fuel_pct=24)]
        stats = _sweep(monkeypatch, low, trig)
        assert stats["fired"] == 1
        assert [s["vehicle"] for s in _isolate] == ["v1"]

    def test_an_unresolvable_scope_stays_silent_rather_than_guessing(
            self, monkeypatch, _isolate):
        """We cannot prove the person may see these trucks, so we say
        nothing.  Widening on failure is how a scope becomes a leak."""
        async def boom(tenant, account_id, owner_user_id):
            return ev._DENY_ALL

        monkeypatch.setattr(ev, "_owner_scope", boom)
        stats = _sweep(monkeypatch, [_row("v1", fuel_pct=10)], [_trigger()])
        assert stats["fired"] == 0 and stats["seeded"] == 0

    def test_the_scope_is_resolved_once_per_owner_not_per_trigger(
            self, monkeypatch, _isolate):
        calls: list[int] = []

        async def counting(tenant, account_id, owner_user_id):
            calls.append(owner_user_id)
            return None

        monkeypatch.setattr(ev, "_owner_scope", counting)
        mine = [_trigger(tid=1, owner=7), _trigger(tid=2, owner=7, threshold=15.0),
                _trigger(tid=3, owner=8)]
        _sweep(monkeypatch, [_row("v1", fuel_pct=40)], mine)
        assert sorted(calls) == [7, 8], calls
