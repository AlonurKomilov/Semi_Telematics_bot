"""ELD arbitrates its own readings, at its own URL.

`/vehicles/config` belongs to Vehicles. Hours of service is its own
domain with its own permissions and its own page, so routing an ELD
setting through the Vehicles feature would put one feature's config in
another's URL. The chain a reader follows is source → eld →
`/eld/config`, exactly as it is source → vehicles → `/vehicles/config`
next door.

Three decisions here are not the obvious ones, and each has a test:

  THE UNIT IS THE WHOLE READING. The vehicle entity arbitrates field by
  field, which is right for independent facts about a static object — a
  VIN from one integration, a plate from another. A duty status and its
  clocks are ONE observation from ONE certified device at ONE instant;
  mixing them produces a reading neither device reported, on the surface
  that must never invent a state.

  "MOST RECENT" IS AN OPTION, NOT THE RULE. Freshest-wins is the obvious
  default to an engineer and the wrong default to an operator who has
  decided which device they trust — a newer reading from the device they
  are migrating AWAY from would quietly outrank the one they moved to.
  Whose hours are authoritative is the owner's call.

  THERE IS NO MANUAL PIN. Every other entity resolves a conflict by
  pinning a value as `manual`. Doing that here would put a
  human-authored duty status into a table every surface calls a
  read-only mirror of a certified device.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.eld.config import ELD_ENTITY, NEWEST, READING_FIELD, _refuse_pin
from features.eld.service import collapse_overlap


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _row(provider, user_id=7, minutes_ago=5, **kw):
    base = {
        "provider_id": provider,
        "provider_driver_id": f"{provider}-1",
        "user_id": user_id,
        "duty_status": "driving",
        "source_ts": _iso(minutes_ago),
    }
    base.update(kw)
    return base


# ── Its own entity, its own URL ───────────────────────────────────

def test_the_eld_entity_is_registered_separately_from_vehicle():
    from capabilities.source.registry import get_entity

    ent = get_entity(ELD_ENTITY)
    assert ent is not None, (
        "the ELD feature did not register its own entity — its precedence "
        "would have had to live under the Vehicles one"
    )
    assert ent.entity_type != "vehicle"


def test_it_arbitrates_one_whole_reading_not_a_field_each():
    """Field-by-field is right next door and wrong here."""
    from capabilities.source.registry import get_entity

    ent = get_entity(ELD_ENTITY)
    assert ent.fields == (READING_FIELD,)
    assert "duty_status" not in ent.fields
    assert "drive_remaining_seconds" not in ent.fields


def test_the_devices_and_the_rule_are_both_offerable():
    from capabilities.source.registry import get_entity

    offered = set(get_entity(ELD_ENTITY).provider_sources)
    assert "samsara" in offered and "orient_eld" in offered
    assert NEWEST in offered, (
        "'most recent' must be selectable — set_precedence drops any "
        "primary that is not a declared source, so an unlisted rule "
        "would be silently ignored on save"
    )


def test_its_config_lives_at_its_own_url_and_wins_it():
    from interfaces.api.app import create_api

    app = create_api()

    def wins(method, path):
        for r in app.routes:
            if (hasattr(r, "methods") and method in r.methods
                    and r.path_regex.match(path)):
                return r.endpoint.__name__
        return ""

    assert wins("GET", "/api/eld/config") == "get_config"
    assert wins("PUT", "/api/eld/config") == "put_config"
    # And the feature router's own routes are not shadowed by it.
    assert wins("GET", "/api/eld/hours") == "hours"
    assert wins("GET", "/api/eld/hours/7") == "driver_hours"


@pytest.mark.asyncio
async def test_pinning_a_duty_status_by_hand_is_refused():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        await _refuse_pin(None, 1, 7, READING_FIELD, "driving")
    assert e.value.status_code == 409
    assert "system of record" in e.value.detail


# ── What the choice actually does ─────────────────────────────────

def test_no_choice_leaves_both_readings_visible():
    """The behaviour before anybody sets this, and the safe direction:
    two readings a dispatcher can see beats one we picked by guessing."""
    rows = [_row("samsara"), _row("orient_eld")]
    assert len(collapse_overlap(rows, [])) == 2


def test_the_chosen_device_wins_the_whole_reading():
    rows = [_row("samsara", minutes_ago=1), _row("orient_eld", minutes_ago=9)]
    kept = collapse_overlap(rows, ["orient_eld", "samsara"])
    assert [r["provider_id"] for r in kept] == ["orient_eld"], (
        "the owner's choice lost to a fresher reading from the device "
        "they did not choose"
    )


def test_most_recent_wins_when_that_is_what_was_chosen():
    rows = [_row("samsara", minutes_ago=1), _row("orient_eld", minutes_ago=9)]
    kept = collapse_overlap(rows, [NEWEST, "orient_eld", "samsara"])
    assert [r["provider_id"] for r in kept] == ["samsara"]


def test_an_undateable_reading_never_beats_one_we_can_date():
    """"We cannot say when this was observed" must not win a contest
    about which observation is newer."""
    rows = [_row("samsara", source_ts=""), _row("orient_eld", minutes_ago=90)]
    kept = collapse_overlap(rows, [NEWEST, "orient_eld", "samsara"])
    assert [r["provider_id"] for r in kept] == ["orient_eld"]


def test_drivers_on_one_device_are_untouched():
    """The normal case — two ELDs, disjoint drivers — is a union and
    needs no arbitration at all."""
    rows = [_row("samsara", user_id=7), _row("orient_eld", user_id=8)]
    kept = collapse_overlap(rows, ["orient_eld", "samsara"])
    assert len(kept) == 2


def test_unlinked_rows_are_never_collapsed():
    """Two unlinked rows might be one person or two. Guessing merges two
    humans into one, which is worse than showing two rows."""
    rows = [_row("samsara", user_id=None), _row("orient_eld", user_id=None)]
    kept = collapse_overlap(rows, ["orient_eld", "samsara"])
    assert len(kept) == 2


def test_a_provider_the_order_never_heard_of_still_beats_nothing():
    rows = [_row("retired_eld")]
    kept = collapse_overlap(rows, ["orient_eld", "samsara"])
    assert [r["provider_id"] for r in kept] == ["retired_eld"]


def test_the_collapse_happens_before_anything_is_counted():
    """A driver shown twice would be counted twice, hidden twice and
    warned about twice."""
    import inspect

    from features.eld import service

    src = inspect.getsource(service.get_hours)
    assert src.index("collapse_overlap") < src.index("before_scope = len(rows)")
