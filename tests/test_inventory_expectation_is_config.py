"""The inventory expectation is CONFIG, and config adds no table.

`capabilities/config/docs/ARCHITECTURE.md` states the rule this holds:
"Add ZERO new permissions and ZERO new tables.  If the setting seems to
need per-role values, it's either view arrangement (→ role scope) or it
violates the blast-radius rule."

It exists because the first cut broke it.  What a vehicle is expected to
carry shipped as its own table with its own gate, outside the family, and
the Permissions matrix showed Inventory with an empty Config cell while
the setting plainly existed.  Four to ten small rows had not outgrown a
KV blob, which is the only thing that buys a feature its own rows.

The table was removed before it was ever deployed — and then came back
once, because the deletion sat uncommitted in a working tree three
sessions share and a peer restored the file.  A guard is what makes that
recovery permanent instead of something somebody has to remember.
"""
from __future__ import annotations

import re

import pytest

from tests._repo import REPO

DEAD_TABLE = "inventory_expected_items"

#: Where the expectation actually lives now, and under which flag.
CATALOGUE_KEY = "inventory_expected"
FOCUS_PREFIX = "inventory_focus."

SCHEMA_FILES = (
    "adapters/storage/migrations.py",
    "adapters/storage/schema.py",
    "adapters/storage/platform_migrations.py",
    "adapters/storage/platform_schema.py",
)


@pytest.mark.parametrize("rel", SCHEMA_FILES)
def test_the_expectation_never_becomes_a_table_again(rel: str) -> None:
    text = (REPO / rel).read_text(encoding="utf-8")
    assert DEAD_TABLE not in text, (
        f"{rel} reintroduces {DEAD_TABLE}.  The inventory expectation is "
        "config: it belongs in account_settings behind the config family, "
        "and the family's own recipe forbids a new table for it."
    )


def test_no_module_anywhere_still_reaches_for_it() -> None:
    """A table gone from the schema but still queried is worse than both."""
    hits = []
    for path in REPO.rglob("*.py"):
        if "node_modules" in str(path) or path.name.startswith("test_"):
            continue
        if DEAD_TABLE in path.read_text(encoding="utf-8", errors="ignore"):
            hits.append(str(path.relative_to(REPO)))
    assert not hits, f"still referencing {DEAD_TABLE}: {hits}"


def test_both_keys_are_declared_and_carry_the_right_flag() -> None:
    """An undeclared key is refused at PUT /settings, so this is not
    bookkeeping — it is how the setting works at all.

    The two flags are the whole design: the CATALOGUE is account-wide
    because whether a truck is short its ELD is a fact about the truck,
    and the FOCUS is per-role because which rows a role goes red about is
    attention, not truth.
    """
    from capabilities.config.account import owner_for

    catalogue = owner_for(CATALOGUE_KEY)
    assert catalogue is not None, f"{CATALOGUE_KEY} is not declared"
    assert catalogue.permission == "can_manage_config_all", (
        "the catalogue decides what data MEANS — the blast-radius rule "
        "makes that account-wide, always"
    )

    focus = owner_for(FOCUS_PREFIX + "safety")
    assert focus is not None, f"{FOCUS_PREFIX}* is not declared"
    assert focus.permission == "can_manage_config_role", (
        "a role's focus is its own arrangement, never account-wide — and "
        "the own-role wall is code, not this flag"
    )


def test_the_matrix_shows_inventory_riding_both_scopes() -> None:
    """The owner found this by reading the Permissions page: a feature
    with config and an empty Config cell is the matrix misreporting who
    can change what, which is the one job that column has."""
    grid = (REPO / "interfaces/dashboard/src/features/permissions/verbGrid.ts") \
        .read_text(encoding="utf-8")
    assert "can_view_inventory: [" in grid
    # The entry is a list of [flag, note] pairs, so it ends at the outer
    # "]," on its own line — splitting on the first "]," would stop
    # inside the first pair and pass while the second was missing.
    block = grid.split("can_view_inventory: [", 1)[1].split("\n  ],", 1)[0]
    assert "can_manage_config_all" in block, "the catalogue scope is missing"
    assert "can_manage_config_role" in block, "the focus scope is missing"


def test_the_dashboard_opens_it_with_the_house_gear_not_a_shape_of_its_own() -> None:
    """FeatureConfigGear exists to end exactly this.

    Its own docstring counts the damage: "Six surfaces, six shapes, six
    icons — and no way to learn 'where do I change this?' once and have
    it hold on the next page."  Inventory's config shipped as a page TAB
    first, which made a seventh.  The owner spotted it by looking at a
    page that had the gear and one that did not.

    `alsoWhen` is asserted too, because without it the door closes on the
    person most likely to want it: Inventory is the first feature to ride
    both config scopes, and a fleet manager holds only the own-role half.
    """
    page = (REPO / "interfaces/dashboard/src/features/inventory/InventoryPage.tsx") \
        .read_text(encoding="utf-8")
    assert "<FeatureConfigGear" in page, (
        "Inventory's config must open from the house gear, in the slot every "
        "other feature keeps it in"
    )
    assert 'alsoWhen="can_manage_config_role"' in page
    assert "PAGE_TABS" not in page, "the bespoke Expected tab is back"


def test_each_half_of_the_editor_asks_for_its_own_flag() -> None:
    """The config family's recipe, in as many words: mirror the server's
    check on the affordance, because a button leading to a 403 is worse
    than no button.

    This shipped gated on `can_manage_inventory` — the flag for adding and
    verifying ITEMS — while the server gates the catalogue on
    `can_manage_config_all`.  A fleet manager saw an enabled Save that
    answered 403.
    """
    raw = (REPO / "interfaces/dashboard/src/features/inventory/ExpectedEditor.tsx") \
        .read_text(encoding="utf-8")
    # Comments stripped first.  This file's own comment RECORDS the wrong
    # flag as the mistake it was, and a guard that reads prose matches its
    # own explanation — which is how a rule comes to forbid describing it.
    editor = re.sub(r"/\*.*?\*/", " ", raw, flags=re.S)
    editor = re.sub(r"^\s*//.*$", " ", editor, flags=re.M)
    assert "viewHas('can_manage_config_all')" in editor
    assert "viewHas('can_manage_config_role')" in editor
    assert "can_manage_inventory" not in editor, (
        "the catalogue is config, not the feature's own Manage"
    )
