"""``manual`` — declared everywhere, configurable nowhere.

An operator's edit is the highest-ranked source in the system, and for
a long time the model hid it: entities declared ``("datatruck",
"samsara")`` while the engine ranked a hardcoded ``"manual"`` above
both.  These tests pin the repair from both sides —

  DECLARED: every registered entity carries manual in ``sources``,
  injected by the registry so no call site can forget it.

  NOT CONFIGURABLE: precedence refuses it as a primary, strips it from
  stored orders, and never offers it in the UI payload.  The rank is a
  code invariant because the conflicts UI works by pinning a chosen
  value as manual — if a provider could outrank it, every resolution a
  person makes would be silently reverted by the next nightly sync.
"""
from __future__ import annotations

import json

import pytest

from capabilities.source import (
    MANUAL_SOURCE,
    get_entity,
    merge_fields,
    pin_manual,
    source_rank,
)

# Importing the mixins is what registers the three entities.
import adapters.storage.vehicles_registry  # noqa: F401
import adapters.storage.drivers  # noqa: F401
import adapters.storage.loads  # noqa: F401


def test_every_registered_entity_declares_manual():
    for entity_type in ("vehicle", "driver", "load"):
        ent = get_entity(entity_type)
        assert ent is not None, f"{entity_type} not registered"
        assert MANUAL_SOURCE in ent.sources, (
            f"{entity_type}: manual missing from the declared model — the "
            "registry must inject it"
        )
        assert MANUAL_SOURCE not in ent.provider_sources, (
            f"{entity_type}: manual leaked into the CONFIGURABLE set"
        )


def test_manual_outranks_every_order_even_one_that_names_it():
    """Rank -1 regardless of what an order claims.

    A stored order that smuggles "manual" in must change nothing: the
    invariant lives in ``source_rank``, not in data.
    """
    for order in (
        ("datatruck", "samsara"),
        ("manual", "datatruck", "samsara"),
        ("datatruck", "manual"),
        (),
    ):
        assert source_rank(MANUAL_SOURCE, order) < 0
        for provider in ("datatruck", "samsara"):
            if provider in order:
                assert source_rank(MANUAL_SOURCE, order) < source_rank(
                    provider, order)


def test_merge_never_touches_a_manually_pinned_field():
    ent = get_entity("vehicle")
    prov = pin_manual({}, ["vin"])
    result = merge_fields(
        current={"vin": "OPERATOR-CORRECTED", "make": ""},
        provenance=prov,
        owner_fallback="samsara",
        incoming={"vin": "PROVIDER-STALE", "make": "FREIGHTLINER"},
        source="samsara",
        fields=("vin", "make"),
        precedence=ent.default_precedence,
    )
    assert "vin" not in result.updates, (
        "a sync overwrote an operator's correction — the exact failure "
        "the manual rank exists to prevent"
    )
    assert result.updates.get("make") == "FREIGHTLINER", (
        "fill-empty must still work beside the pin"
    )


def test_pin_manual_returns_a_new_map():
    original = {"make": "samsara"}
    pinned = pin_manual(original, ["vin", "year"])
    assert pinned == {"make": "samsara", "vin": "manual", "year": "manual"}
    assert original == {"make": "samsara"}, (
        "pin_manual mutated its input — a shared provenance map would "
        "leak pins between records"
    )
    assert pin_manual(None, ["vin"]) == {"vin": "manual"}


@pytest.mark.asyncio
async def test_precedence_refuses_manual_and_stored_orders_never_contain_it(pg_db):
    from capabilities.source import get_precedence, set_precedence

    acct = (await pg_db.create_account("Manual Src Co")).id
    # An owner "choosing" manual as primary is a no-op lie — refused.
    stored = await set_precedence(pg_db, acct, "vehicle", {"vin": "manual"})
    assert "vin" not in stored

    # A legitimate choice expands over PROVIDERS only.
    stored = await set_precedence(pg_db, acct, "vehicle", {"vin": "datatruck"})
    assert stored["vin"][0] == "datatruck"
    assert MANUAL_SOURCE not in stored["vin"]

    # And a stored order that carries manual anyway (hand-edited row,
    # pre-repair data) is stripped on read rather than echoed.
    ent = get_entity("vehicle")
    key = "vehicle_field_precedence"
    await pg_db.set_account_setting(
        acct, key, json.dumps({"vin": ["manual", "samsara", "datatruck"]}),
    )
    prec = await get_precedence(pg_db, acct, "vehicle")
    assert MANUAL_SOURCE not in prec["vin"]
    assert prec["vin"][0] == "samsara"


@pytest.mark.asyncio
async def test_the_ui_payload_never_offers_manual(pg_db):
    from capabilities.source import precedence_options

    acct = (await pg_db.create_account("Manual Opt Co")).id
    opts = await precedence_options(pg_db, acct, "vehicle")
    assert MANUAL_SOURCE not in opts["sources"], (
        "manual rendered as a pickable provider — it has no connection "
        "and its rank is not a choice"
    )
    assert opts["sources"], "provider list must not be empty"
    for f in opts["fields"]:
        assert f["primary"] != MANUAL_SOURCE
