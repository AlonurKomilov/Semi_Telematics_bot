"""Live readings arbitrate under their own entity, on the Vehicles gear.

The roster's ``vehicle`` entity merges spec fields one at a time with a
hand edit on top — right for a VIN, wrong for a position.  The
``vehicle_state`` entity next to it makes the three decisions the ELD
entity made first, for the same reasons: the unit is the WHOLE reading,
"most recent" is an option and not the rule, and there is no manual
pin.  Its config rides ``/vehicles/config`` as a block, because the
setting decides what every vehicle read resolves to.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException

from capabilities import source as reconciliation
from capabilities.integrations.shared import vehicle_state as vs
from capabilities.source.readings import NEWEST
from capabilities.source.registry import get_entity


@pytest_asyncio.fixture
async def aid(pg_db) -> int:
    """The precedence store keys on a real account row (FK)."""
    return (await pg_db.create_account("State Co")).id


def test_the_state_entity_is_registered_separately_from_vehicle():
    ent = get_entity(vs.ENTITY)
    assert ent is not None and ent.entity_type != "vehicle"


def test_it_arbitrates_whole_readings_not_columns():
    ent = get_entity(vs.ENTITY)
    assert ent.fields == tuple(vs.READING_GROUPS)
    for column in ("lat", "lon", "odometer_mi", "fuel_pct", "captured_at"):
        assert column not in ent.fields


def test_the_providers_and_the_rule_are_both_offerable():
    offered = set(get_entity(vs.ENTITY).provider_sources)
    assert "samsara" in offered
    assert NEWEST in offered, (
        "'most recent' must be selectable — set_precedence drops any "
        "primary that is not a declared source"
    )
    assert "manual" not in offered
    # Metadata-only catalog entries are not something to win.
    assert "motive" not in offered and "geotab" not in offered


@pytest.mark.asyncio
async def test_a_pin_is_refused():
    with pytest.raises(HTTPException) as exc:
        await vs._refuse_pin(None, 1, 1, "location", "40,-74")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_the_panel_block_labels_the_rule(pg_db, aid):
    payload = await vs.config_payload(pg_db, aid)
    assert {"sources", "fields", "source_labels", "applies_when"} <= set(payload)
    assert [f["key"] for f in payload["fields"]] == list(vs.READING_GROUPS)
    assert all(f["primary"] == "samsara" for f in payload["fields"]), (
        "an account that never opened the panel behaves as today"
    )
    assert payload["source_labels"][NEWEST]
    assert payload["source_labels"]["samsara"] == "Samsara"


@pytest.mark.asyncio
async def test_a_choice_round_trips_and_leaves_the_roster_order_alone(pg_db, aid):
    before = await reconciliation.get_precedence(pg_db, aid, "vehicle")
    await reconciliation.set_precedence(pg_db, aid, vs.ENTITY, {"odometer": NEWEST})
    payload = await vs.config_payload(pg_db, aid)
    primary = {f["key"]: f["primary"] for f in payload["fields"]}
    assert primary["odometer"] == NEWEST
    assert primary["location"] == "samsara"
    assert await reconciliation.get_precedence(pg_db, aid, "vehicle") == before
