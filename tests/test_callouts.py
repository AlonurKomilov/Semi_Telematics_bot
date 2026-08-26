"""Callouts — the shared in-place statement lane.

Born from truck 548640: its gateway had power and a satellite fix but
was not on the engine bus, so it reported 0 miles across 86 days while
genuinely driving.  Mileage read it as an idle asset, and the blank
"—" fields read like the platform's bug.

The tests that matter here are the ones that keep the feature from
crying wolf (a warning nobody believes is worse than no warning) and
the one that keeps a standing condition from being silently deduped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from capabilities.callouts import known_keys, register_callout
from capabilities.callouts.registry import CalloutSpec
from features.vehicles.callouts import (
    ENGINE_DATA_GAP_HOURS,
    NO_ENGINE_DATA,
    detect_no_engine_data,
)

REPO = Path(__file__).resolve().parent.parent


# ── The rule ─────────────────────────────────────────────────────

def test_gps_without_odometer_is_blind():
    """The signature: the device proves it is alive and located, and
    the engine bus still says nothing."""
    assert detect_no_engine_data(
        has_gps=True, odometer_present=False, odometer_age_hours=None,
    ) is True


def test_parked_truck_is_not_blind():
    """No GPS either — the gateway is asleep, not deaf.  Without this
    guard every parked truck in the yard would raise a callout."""
    assert detect_no_engine_data(
        has_gps=False, odometer_present=False, odometer_age_hours=None,
    ) is False


def test_recent_odometer_is_not_blind():
    """A truck that reported this morning is fine, even if this
    particular tick carried no reading."""
    assert detect_no_engine_data(
        has_gps=True, odometer_present=False,
        odometer_age_hours=ENGINE_DATA_GAP_HOURS - 1,
    ) is False
    assert detect_no_engine_data(
        has_gps=True, odometer_present=False,
        odometer_age_hours=ENGINE_DATA_GAP_HOURS + 1,
    ) is True


def test_reading_this_tick_clears_it():
    assert detect_no_engine_data(
        has_gps=True, odometer_present=True, odometer_age_hours=None,
    ) is False


def test_rule_ignores_optional_signals():
    """The false-positive trap this rule exists to avoid.

    Measured on the production fleet: 99 of 100 telematics trucks
    report odometer/hours/fuel/DEF/coolant, but only 92 report RPM and
    95 oil pressure.  A "n of eleven signals missing" rule would flag
    eight healthy trucks.  The rule must therefore depend on NOTHING
    but GPS presence and odometer age — asserted by signature so a
    future edit cannot quietly widen it.
    """
    import inspect
    params = set(inspect.signature(detect_no_engine_data).parameters)
    assert params == {"has_gps", "odometer_present", "odometer_age_hours"}


# ── The registry ─────────────────────────────────────────────────

def test_registry_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        register_callout("test.bogus", kind="nope", severity="info",
                         owner="test")


def test_registry_rejects_conflicting_redeclaration():
    register_callout("test.dup", kind="caveat", severity="info", owner="a")
    # Same shape twice is harmless (a module imported twice).
    register_callout("test.dup", kind="caveat", severity="info", owner="a")
    with pytest.raises(ValueError, match="already declared"):
        register_callout("test.dup", kind="condition", severity="warn",
                         owner="b")


def test_mileage_flags_are_caveats():
    """Caveats qualify data and must never be dismissible — the kind
    is what encodes that, so it is worth pinning."""
    from capabilities.callouts import get_spec
    for flag in ("device_change", "estimated", "catchup", "partial",
                 "reset", "rebase"):
        spec = get_spec(f"mileage.{flag}")
        assert isinstance(spec, CalloutSpec)
        assert spec.kind == "caveat"


def test_no_engine_data_is_a_condition():
    from capabilities.callouts import get_spec
    spec = get_spec(NO_ENGINE_DATA)
    assert spec is not None and spec.kind == "condition"


# ── The seam ─────────────────────────────────────────────────────

def _catalog_keys() -> set[str]:
    src = (REPO / "interfaces/dashboard/src/components/callouts"
                  "/calloutCatalog.ts").read_text()
    body = src.split("CALLOUT_CATALOG", 1)[1]
    return set(re.findall(r"'([a-z_]+\.[a-z_]+)':\s*\{", body))


def test_every_backend_key_is_renderable():
    """The drift guard.

    A key the backend can emit but the dashboard's catalog does not
    know would reach a user as a raw string like
    ``vehicle.no_engine_data``.  Declaring keys in two languages is
    the price of the split; this test is what makes it safe.
    """
    missing = set(known_keys()) - _catalog_keys() - {"test.dup"}
    assert not missing, f"catalog is missing: {sorted(missing)}"


def _line_vocabulary() -> set[str]:
    """The line names the resolver actually renders, read from it.

    Parsed rather than restated because this list is now the OPEN half
    of the contract: a callout answers only the lines that fit it, so
    the set grows, and a copy of it here would drift the first time it
    did.  ``CALLOUT_LINES`` in useCallout.ts is the source.
    """
    src = (REPO / "interfaces/dashboard/src/components/callouts/useCallout.ts").read_text()
    m = re.search(r"export const CALLOUT_LINES = \[(.*?)\] as const", src, re.S)
    assert m, "CALLOUT_LINES not found — did the resolver's vocabulary move?"
    names = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert names, "CALLOUT_LINES parsed empty"
    return names


# The FIELD names a callout may declare: the two standalone strings
# plus every labelled line the resolver knows.  A locale entry using a
# name the resolver does not read is invisible copy — which is exactly
# how the six mileage explanations went dark for one commit when
# ``body`` was split into why/affects/do.
CALLOUT_FIELDS = {"title", "short"} | _line_vocabulary()


def test_no_locale_entry_carries_a_field_nothing_reads():
    """An unread field is worse than a missing one.

    A missing string shows the key and someone notices; a field the
    resolver stopped reading keeps looking correct in the JSON while
    rendering nothing at all.
    """
    strings = json.loads(
        (REPO / "interfaces/dashboard/src/locales/en.json").read_text()
    ).get("callout", {})
    for key, block in strings.items():
        if key == "labels":
            continue
        stray = set(block) - CALLOUT_FIELDS
        assert not stray, f"{key} declares unread field(s): {sorted(stray)}"


def test_every_line_name_has_a_label_in_every_locale():
    """The resolver translates ``callout.labels.<name>`` for whatever
    line it renders, so a vocabulary word with no label string reaches
    the user as the raw key next to real copy.  Adding a line name is
    the moment this fires."""
    for lang in ("en", "ru", "uz", "es", "fr", "uk", "am", "pa", "so"):
        labels = json.loads(
            (REPO / f"interfaces/dashboard/src/locales/{lang}.json").read_text()
        )["callout"]["labels"]
        missing = _line_vocabulary() - set(labels)
        assert not missing, f"{lang}: no label for line(s) {sorted(missing)}"


def test_every_locale_declares_the_same_callout_fields():
    """A translation that answers fewer questions than English is a
    silently thinner card in that language, not an error anyone sees."""
    base = json.loads(
        (REPO / "interfaces/dashboard/src/locales/en.json").read_text()
    )["callout"]
    for lang in ("ru", "uz", "es", "fr", "uk", "am", "pa", "so"):
        other = json.loads(
            (REPO / f"interfaces/dashboard/src/locales/{lang}.json").read_text()
        ).get("callout", {})
        for key, block in base.items():
            assert key in other, f"{lang}: missing callout {key}"
            missing = set(block) - set(other[key])
            assert not missing, (
                f"{lang}/{key} is missing {sorted(missing)}"
            )


# A value that is ONLY placeholders and punctuation is the same string
# in every language by necessity — "{{old}} → {{new}}" has nothing to
# translate.  Anything with a letter outside a placeholder does.
_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")


def _has_translatable_words(value: str) -> bool:
    return any(ch.isalpha() for ch in _PLACEHOLDER.sub("", value))


def test_no_locale_silently_ships_the_english_string():
    """Field parity is not translation parity.

    The older guard checks every locale declares the same FIELDS, and
    it passed for months while all six mileage caveats sat in English
    in all eight non-English locales — 96 strings.  A translated label
    above an English sentence does not read as "not translated yet",
    it reads as broken, and nothing could see it because the fields
    were all present.
    """
    base = json.loads(
        (REPO / "interfaces/dashboard/src/locales/en.json").read_text()
    )["callout"]
    for lang in ("ru", "uz", "es", "fr", "uk", "am", "pa", "so"):
        other = json.loads(
            (REPO / f"interfaces/dashboard/src/locales/{lang}.json").read_text()
        )["callout"]
        untranslated = [
            f"{key}.{field}"
            for key, block in base.items()
            for field, value in block.items()
            if _has_translatable_words(value)
            and other.get(key, {}).get(field) == value
        ]
        assert not untranslated, (
            f"{lang}: still English — {sorted(untranslated)}"
        )


def test_every_key_has_english_copy():
    strings = json.loads(
        (REPO / "interfaces/dashboard/src/locales/en.json").read_text()
    ).get("callout", {})
    for key in known_keys():
        if key.startswith("test."):
            continue
        assert key in strings, f"no copy for {key}"
        assert strings[key].get("title"), f"{key} has no title"


# ── Storage lifecycle ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_condition_opens_touches_and_resolves(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"].id

    opened = await db.open_or_touch_condition(
        acct, key=NO_ENGINE_DATA, vehicle_id="v1", vehicle_name="548640",
    )
    assert opened is True, "first observation opens the condition"

    again = await db.open_or_touch_condition(
        acct, key=NO_ENGINE_DATA, vehicle_id="v1", vehicle_name="548640",
    )
    assert again is False, "still-true must not re-open (it would re-notify)"

    live = await db.get_open_conditions(acct)
    assert [r["key"] for r in live] == [NO_ENGINE_DATA]

    assert await db.resolve_condition(
        acct, key=NO_ENGINE_DATA, vehicle_id="v1") is True
    assert await db.get_open_conditions(acct) == []
    # Resolving twice is a no-op, so a recovery announces once.
    assert await db.resolve_condition(
        acct, key=NO_ENGINE_DATA, vehicle_id="v1") is False


@pytest.mark.asyncio
async def test_recurrence_opens_a_fresh_condition(seeded_db):
    """The reason this lives in its own table.

    ``device_event_log`` carries UNIQUE(account, vehicle, kind, old,
    new), so a truck that goes blind, gets fixed, and goes blind again
    would have been swallowed as a duplicate — the second outage would
    never reach anyone.
    """
    db = seeded_db["db"]
    acct = seeded_db["account"].id

    await db.open_or_touch_condition(acct, key=NO_ENGINE_DATA, vehicle_id="v9")
    await db.resolve_condition(acct, key=NO_ENGINE_DATA, vehicle_id="v9")
    reopened = await db.open_or_touch_condition(
        acct, key=NO_ENGINE_DATA, vehicle_id="v9",
    )
    assert reopened is True, "a recurrence is news, not a duplicate"
    assert len(await db.get_open_conditions(acct)) == 1


@pytest.mark.asyncio
async def test_open_conditions_scope_to_requested_vehicles(seeded_db):
    """The vehicle list asks only about the rows on its page."""
    db = seeded_db["db"]
    acct = seeded_db["account"].id
    for vid in ("a", "b", "c"):
        await db.open_or_touch_condition(
            acct, key=NO_ENGINE_DATA, vehicle_id=vid)
    scoped = await db.get_open_conditions(acct, vehicle_ids=["b"])
    assert [r["vehicle_id"] for r in scoped] == ["b"]


@pytest.mark.asyncio
async def test_conditions_are_account_scoped(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"].id
    await db.open_or_touch_condition(acct, key=NO_ENGINE_DATA, vehicle_id="v1")
    assert await db.get_open_conditions(acct + 999) == []


# ── Trailers ─────────────────────────────────────────────────────

def test_sweep_only_sees_provider_rows():
    """Trailers and manual entries cannot be flagged.

    This account has 187 active registry vehicles but only 101 with a
    telematics id; the other 86 are trailers and hand-added trucks.
    They are safe because the sweep walks the rows the PROVIDER
    returned, never the registry — asserted here rather than trusted,
    since a future edit could reasonably reach for the registry list.
    """
    src = (REPO / "capabilities/integrations/samsara/sync.py").read_text()
    body = src.split("async def _sweep_vehicle_conditions", 1)[1]
    body = body.split("\nasync def ", 1)[0]
    assert "for row in rows:" in body
    for forbidden in ("list_vehicles", "registry_rows", "get_identity_map"):
        assert forbidden not in body, (
            f"sweep must not consult {forbidden} — trailers would be flagged"
        )


# ── Dismissal ────────────────────────────────────────────────────

def test_callout_id_is_stable_while_a_fault_is_open():
    """``since`` is the occurrence's opened_at, which does not move
    while the fault is open — so a dismissal survives a page reload."""
    from capabilities.callouts.models import callout_id
    a = callout_id("vehicle.no_engine_data", "vehicle:281", "2026-05-12T09:14:00")
    b = callout_id("vehicle.no_engine_data", "vehicle:281", "2026-05-12T09:14:00")
    assert a == b


def test_callout_id_changes_when_a_fault_recurs():
    """The property the whole design turns on.

    A truck that goes blind, gets fixed, and goes blind again opens a
    NEW ``vehicle_conditions`` row with a new ``opened_at``.  If the id
    did not move with it, the first dismissal would silence the second
    outage forever.
    """
    from capabilities.callouts.models import callout_id
    first = callout_id("vehicle.no_engine_data", "vehicle:281", "2026-05-12T09:14:00")
    again = callout_id("vehicle.no_engine_data", "vehicle:281", "2026-08-01T04:02:00")
    assert first != again


def test_callout_id_never_collides_across_features():
    """Two features emitting a callout about the SAME entity must not
    produce one identity — dismissing a stalled load sync cannot be
    allowed to silence a blind engine bus."""
    from capabilities.callouts.models import callout_id
    veh = callout_id("vehicle.no_engine_data", "vehicle:281", "T")
    load = callout_id("loads.sync_stalled", "vehicle:281", "T")
    assert veh != load


def test_guidance_id_is_permanent():
    """No occurrence, so no ``since``: a suggestion dismissed once
    stays dismissed, which is what a suggestion deserves."""
    from capabilities.callouts.models import callout_id
    assert "#" not in callout_id("alerts.routing_nudge", "account:1", "")


def test_wire_carries_the_id_for_every_callout():
    """Not a nullable extra: the capability mints one for each, so the
    client never has to compose an identity itself."""
    from capabilities.callouts.models import Callout, callout_wire
    wire = callout_wire([
        Callout(key="vehicle.no_engine_data", entity="vehicle:281", since="T"),
        Callout(key="mileage.partial"),
    ])
    assert all(item.get("callout_id") for item in wire)


# ── Detector registry (the layer inversion) ──────────────────────

def test_detector_must_name_a_registered_callout():
    """A detector for an unknown key would open condition rows nothing
    can ever render — the reader would see empty fields with no
    explanation, which is the bug this whole lane exists to prevent."""
    from capabilities.callouts import register_detector
    with pytest.raises(ValueError, match="unregistered callout"):
        register_detector("nope.nothing", lambda row, prior: None)


def test_discover_finds_the_feature_detector():
    """The capability may not import a feature, so contributors are
    imported BY NAME and register themselves.  Without this the ingest
    sweep would find nothing and no condition would ever open.
    """
    from capabilities.callouts import condition_detectors, discover
    discover()
    assert NO_ENGINE_DATA in {d.key for d in condition_detectors()}


def test_detector_reads_a_row_the_ingest_already_has():
    """The detector's whole contract: (row, prior) -> params or None,
    computed from what the tick holds — no extra query, no provider
    call."""
    from capabilities.callouts import condition_detectors, discover
    discover()
    det = next(d for d in condition_detectors() if d.key == NO_ENGINE_DATA)

    blind = {"lat": 43.1, "lon": -89.3, "odometer_mi": None,
             "gateway_serial": "GY4P"}
    assert det.detect(blind, {}) == {"gateway": "GY4P"}

    healthy = {"lat": 43.1, "lon": -89.3, "odometer_mi": 389_542.0}
    assert det.detect(healthy, {}) is None

    parked_no_gps = {"lat": None, "lon": None, "odometer_mi": None}
    assert det.detect(parked_no_gps, {}) is None
