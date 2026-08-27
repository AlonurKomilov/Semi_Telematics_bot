"""The Engine badge must not claim "Off" it cannot know.

Two separate lies met on truck 548640's page.

1. ``classify_vehicle_status`` prefers a real engine reading, but
   looks for an ``engineState`` key while the warehouse supplies
   ``location.engineStates.value``.  The key never matches, so it
   always falls through to its speed heuristic — and a stationary
   truck classifies as "stopped".  On this fleet that made 26 trucks
   with a RUNNING engine (idle, wheels still) report "Off".
2. A truck whose device cannot read the engine bus has no state at
   all.  The ingest is deliberate about that — ``resolve_engine_state``
   returns UNKNOWN rather than guess, precisely so the roll-ups never
   count silence as a parked truck — and the display collapsed it
   back to "Off".

``_derive_engine_state`` now prefers the reported value and returns
EMPTY when there is none, which the dashboard renders as "unknown".
"""

from __future__ import annotations

from features.vehicles.router import _derive_engine_state


def test_idling_truck_is_not_off():
    """Engine running, wheels still — the case that hit 26 trucks."""
    assert _derive_engine_state("stopped", "idle") == "Idle"


def test_moving_truck_reads_on():
    assert _derive_engine_state("moving", "moving") == "On"


def test_genuinely_off_still_reads_off():
    """The fix must not make a real "off" unknowable."""
    assert _derive_engine_state("stopped", "off") == "Off"


def test_no_engine_feed_is_unknown_never_off():
    """Truck 548640: the device reports GPS but cannot read the
    engine.  The field is PRESENT and empty — the ingest looked and
    found nothing — so the UI says "unknown" rather than asserting a
    state nothing observed."""
    assert _derive_engine_state("stopped", "") == ""


def test_reported_value_wins_over_speed_derived_status():
    """The reported reading is evidence; the status is an inference
    from speed.  Evidence wins — that is the whole bug."""
    assert _derive_engine_state("stopped", "moving") == "On"
    assert _derive_engine_state("moving", "off") == "Off"


def test_unknown_reported_word_does_not_become_off():
    """A provider word we do not recognise is not proof of "off"."""
    assert _derive_engine_state("stopped", "cranking") == ""


def test_absent_field_keeps_the_old_heuristic():
    """ABSENT is not the same answer as EMPTY.

    Only the warehouse reader emits ``engineStates``; the live-Samsara
    fallback (cold cache) carries no engine field at all.  Showing the
    whole fleet as "unknown" because OUR cache is cold would be a
    worse lie than the one this fixes, so that path keeps the legacy
    speed heuristic.
    """
    assert _derive_engine_state("moving") == "On"
    assert _derive_engine_state("idle") == "Idle"
    assert _derive_engine_state("stopped") == "Off"


def test_empty_and_absent_disagree_on_purpose():
    """The one line that separates 'we looked, nothing there' from
    'we have no field to look at'."""
    assert _derive_engine_state("stopped", "") == ""       # looked → unknown
    assert _derive_engine_state("stopped", None) == "Off"  # no field → legacy


def test_detail_page_overlay_reaches_the_derivation():
    """The detail endpoint reads LIVE Samsara, whose payload carries no
    engine state — so the warehouse value has to be overlaid onto the
    match before ``_normalize_detail`` runs, or the absent-field branch
    fires and a standing truck reads "Off".

    That is what put a confident "Off" directly beneath a banner saying
    the device cannot read the engine.  Asserted structurally: the
    overlay must write the SAME key the warehouse reader emits, so one
    derivation path serves the list and the page.
    """
    import inspect

    from features.vehicles import router

    src = inspect.getsource(router.vehicle_detail)
    assert "engine_state_by_id" in src, "detail page must overlay engine state"
    assert '"engineStates"' in src, (
        "overlay must use the warehouse reader's key, not a second shape"
    )


def test_empty_engine_state_survives_the_overlay():
    """"" is an ANSWER — the ingest looked and found no engine feed.
    Skipping it as missing would drop the page back to the speed
    heuristic and re-invent the bug."""
    import inspect

    from features.vehicles import router

    src = inspect.getsource(router.vehicle_detail)
    assert "if state is not None:" in src, (
        'empty string must reach the overlay; a truthiness check drops it'
    )
