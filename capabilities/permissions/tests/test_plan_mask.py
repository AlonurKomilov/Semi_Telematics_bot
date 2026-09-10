"""The plan mask — layer 1 of the resolver — and the vocabulary under it.

What a plan may leave out, how ``["*"]`` behaves, what the mask forces
off, and the FAIL-CLOSED rules: an unknown plan closes the sellable set,
an unreadable table keeps the last-known one, and a config flag is never
masked.  DB-free — the table is fed through ``refresh_plans(pdb)`` with
a stand-in that answers ``list_plans``.
"""

from __future__ import annotations

import pytest

from capabilities.permissions import plans
from capabilities.permissions.registry import CROSS_FEATURE_FLAGS, ENTRIES
from capabilities.permissions.roles import FeatureSet


class _Pdb:
    def __init__(self, rows=None, fail: bool = False):
        self.rows, self.fail = rows or [], fail

    async def list_plans(self):
        if self.fail:
            raise RuntimeError("db down")
        return self.rows


def _plan(tier, included, quotas=None):
    return {"tier": tier, "label": tier.title(), "included": included, "quotas": quotas or {}}


def _all_on() -> FeatureSet:
    """Every flag granted — the widest role, before any mask."""
    return FeatureSet(**{f: True for f in FeatureSet.__dataclass_fields__})


@pytest.fixture(autouse=True)
def _fresh_table():
    """Every test starts from a never-loaded table and leaves none behind."""
    plans.forget()
    yield
    plans.forget()


async def _load(*rows):
    assert await plans.refresh_plans(_Pdb(list(rows)), force=True)


# ── the vocabulary ────────────────────────────────────────────────

def test_the_sellable_set_is_every_entry_but_administration_and_overview():
    ids = {e.id for e in ENTRIES}
    assert set(plans.EXCLUDABLE) <= ids
    assert "overview" not in plans.EXCLUDABLE
    by_id = {e.id: e for e in ENTRIES}
    assert not any(by_id[i].tier == "administration" for i in plans.EXCLUDABLE)
    # what the owner must always reach is not for sale
    for must in ("team_management", "role_permissions", "settings", "billing", "invites"):
        assert must not in plans.EXCLUDABLE, must
    # a service is sellable like a feature
    assert "ai_assistant" in plans.EXCLUDABLE
    assert "maintenance" in plans.EXCLUDABLE


def test_an_entry_that_rides_another_is_not_a_plan_line():
    """``flags=[]`` entries are governed by the verb they ride: the mask
    could force nothing off for them, so a plan cannot name them."""
    for rider in ("scheduled_reports", "dot_binder", "kpi_my_payouts", "scorecard_rules", "vendors"):
        assert rider not in plans.EXCLUDABLE, rider
        assert plans.plan_includes("anything", rider)          # always included, even unknown tier
    for fid in plans.EXCLUDABLE:
        assert plans._FLAGS_OF[fid], f"{fid} is sellable but forces no flag off"


def test_the_console_vocabulary():
    cat = plans.catalog()
    assert [c["id"] for c in cat] == list(plans.EXCLUDABLE)
    by = {c["id"]: c for c in cat}
    assert by["ai_assistant"]["label"] == "AI Assistant" and by["ai_assistant"]["kind"] == "service"
    assert by["cost_per_mile"]["label"] == "Cost Per Mile"
    assert by["truck-anatomy"]["label"] == "Truck Anatomy"
    assert by["vehicle_documents"]["parent"] == "vehicles"
    assert all(c["flags"] for c in cat)
    assert plans.PLAN_KEY_RE.match("pro") and plans.PLAN_KEY_RE.match("gold_2027")
    assert not plans.PLAN_KEY_RE.match("Pro") and not plans.PLAN_KEY_RE.match("x") and not plans.PLAN_KEY_RE.match("a-b")
    assert set(plans.quota_defaults("free")) == set(plans.QUOTA_KEYS)
    assert plans.quota_defaults("nope") == plans.quota_defaults("free")


def test_normalize_included():
    assert plans.normalize_included(["*"]) == (["*"], [])
    assert plans.normalize_included(["maintenance", "*", "vehicles"]) == (["*"], [])
    inc, unknown = plans.normalize_included(["work_orders", "maintenance", "maintenance", "billing", "nope", "scheduled_reports"])
    assert inc == ["maintenance", "work_orders"]                  # registry order, deduplicated
    assert unknown == ["billing", "nope", "scheduled_reports"]    # not for sale / unknown / rides another
    assert plans.normalize_included([]) == ([], [])


def test_no_two_sellable_entries_share_a_flag():
    """Excluding one entry forces ITS flags off; a flag two entries
    listed would strip the one still included.  Riding entries declare
    ``flags=[]`` for exactly this reason."""
    seen: dict[str, str] = {}
    for fid, flags in plans._FLAGS_OF.items():
        for f in flags:
            assert f not in seen, f"{f} listed by both {seen[f]} and {fid}"
            seen[f] = fid


def test_a_config_flag_belongs_to_no_plan_line():
    for flags in plans._FLAGS_OF.values():
        assert not (flags & CROSS_FEATURE_FLAGS)


# ── the mask ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_everything_included_forces_nothing_off():
    await _load(_plan("pro", ["*"]))
    fs = _all_on()
    assert plans.apply_plan_mask(fs, "pro") == fs
    assert plans.plan_flags_off(plans.included_for("pro")) == set()
    assert plans.plan_includes("pro", "maintenance")


@pytest.mark.asyncio
async def test_a_plan_that_leaves_maintenance_out_forces_its_flags_off_and_nothing_else():
    inc = [i for i in plans.EXCLUDABLE if i != "maintenance"]
    await _load(_plan("free", inc))
    fs = _all_on()
    masked = plans.apply_plan_mask(fs, "free")
    off = {f for f in FeatureSet.__dataclass_fields__ if not getattr(masked, f)}
    assert off == set(plans._FLAGS_OF["maintenance"]) & set(FeatureSet.__dataclass_fields__)
    assert off, "maintenance opens at least one flag"
    assert not plans.plan_includes("free", "maintenance")
    assert plans.plan_includes("free", "vehicles")


@pytest.mark.asyncio
async def test_what_is_not_for_sale_is_always_included():
    await _load(_plan("free", []))          # the narrowest plan there is
    assert plans.plan_includes("free", "overview")
    assert plans.plan_includes("free", "role_permissions")
    assert plans.plan_includes("free", "billing")
    masked = plans.apply_plan_mask(_all_on(), "free")
    # the owner's escape hatches survive the narrowest plan
    for f in ("can_manage_permissions", "can_manage_users", "can_manage_billing", "can_manage_account"):
        assert getattr(masked, f) is True, f
    # and so does every config flag
    for f in CROSS_FEATURE_FLAGS:
        if hasattr(masked, f):
            assert getattr(masked, f) is True, f


# ── fail-closed ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unknown_plan_closes_the_sellable_set():
    await _load(_plan("pro", ["*"]))
    assert plans.included_for("gold") is None
    assert not plans.plan_includes("gold", "maintenance")
    assert plans.plan_includes("gold", "billing")
    masked = plans.apply_plan_mask(_all_on(), "gold")
    assert not any(getattr(masked, f) for f in plans._ALL_SELLABLE_FLAGS if hasattr(masked, f))
    assert masked.can_manage_billing and masked.can_manage_users     # the way back stays open
    # and no tier at all is the same closed answer
    assert not plans.plan_includes(None, "maintenance")


def test_a_never_loaded_table_is_closed():
    assert plans.included_for("pro") is None
    assert not plans.plan_includes("pro", "maintenance")
    assert plans.plan_flags_off(None) == set(plans._ALL_SELLABLE_FLAGS)


@pytest.mark.asyncio
async def test_an_unreadable_table_keeps_the_last_known_one():
    await _load(_plan("pro", ["*"]), _plan("free", ["vehicles"]))
    assert not await plans.refresh_plans(_Pdb(fail=True), force=True)
    assert plans.included_for("pro") == plans.EVERYTHING
    assert plans.included_for("free") == frozenset({"vehicles"})
    assert plans.plan_includes("free", "vehicles")
    assert not plans.plan_includes("free", "maintenance")


@pytest.mark.asyncio
async def test_a_fresh_read_replaces_the_table_whole():
    await _load(_plan("pro", ["*"]), _plan("legacy", ["*"]))
    await _load(_plan("pro", ["vehicles"]))
    assert plans.included_for("legacy") is None         # a dropped row is gone
    assert plans.included_for("pro") == frozenset({"vehicles"})


@pytest.mark.asyncio
async def test_the_ttl_reloads_only_when_stale(monkeypatch):
    await _load(_plan("pro", ["*"]))
    calls = []

    class _Counting(_Pdb):
        async def list_plans(self):
            calls.append(1)
            return [_plan("pro", ["vehicles"])]

    assert await plans.refresh_plans(_Counting())          # fresh → no read
    assert calls == [] and plans.included_for("pro") == plans.EVERYTHING
    monkeypatch.setattr(plans, "_LOADED_AT", plans.time.monotonic() - plans._TTL_S - 1)
    assert await plans.refresh_plans(_Counting())          # stale → read
    assert calls == [1] and plans.included_for("pro") == frozenset({"vehicles"})


@pytest.mark.asyncio
async def test_invalidate_marks_the_table_stale_and_drops_cached_permissions(monkeypatch):
    await _load(_plan("pro", ["*"]))
    dropped = []
    monkeypatch.setattr("capabilities.permissions.roles.invalidate_permissions_cache",
                        lambda account_id=None: dropped.append(account_id))
    plans.invalidate_plans()
    assert plans._LOADED_AT is None
    assert dropped == [None]                               # every account, every tier
    assert plans.included_for("pro") == plans.EVERYTHING   # last-known survives until the reload


def test_an_account_row_with_no_tier_is_on_free():
    from types import SimpleNamespace
    assert plans.tier_of(SimpleNamespace(tier="pro")) == "pro"
    assert plans.tier_of(SimpleNamespace(tier="")) == "free"
    assert plans.tier_of(SimpleNamespace()) == "free"


def test_last_known_tier_per_account():
    plans.remember_tier(7, "pro")
    plans.remember_tier(8, None)
    assert plans.last_known_tier(7) == "pro"
    assert plans.last_known_tier(8) is None


# ── the customer's view of a plan ─────────────────────────────────

@pytest.mark.asyncio
async def test_excluded_view_is_what_the_mask_does_and_names_the_feature_behind_a_flag():
    inc = [i for i in plans.EXCLUDABLE if i != "maintenance"]
    await _load(_plan("free", inc), _plan("pro", ["*"]))
    assert plans.excluded_for("free") == ["maintenance"]
    assert plans.excluded_for("pro") == []
    assert plans.excluded_for("gold") == list(plans.EXCLUDABLE)        # unknown → closed → all
    assert plans.excluded_flags_for("free") == sorted(plans._FLAGS_OF["maintenance"])
    assert plans.excluded_flags_for("pro") == []
    assert plans.excludes_flag("free", "can_view_maintenance") == "maintenance"
    assert plans.excludes_flag("free", "can_manage_maintenance") == "maintenance"
    assert plans.excludes_flag("free", "can_view_vehicles") is None
    assert plans.excludes_flag("free", "can_manage_billing") is None    # never a plan line
    assert plans.excludes_flag("free", "no_such_flag") is None
    assert plans.excludes_flag("gold", "can_view_vehicles") == "vehicles"
    assert plans.plan_label("free") == "Free" and plans.plan_label("gold") == "Gold" and plans.plan_label(None) == ""


# ── quotas ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_plan_quota_wins_over_the_default_only_when_it_is_a_number():
    await _load(_plan("pro", ["*"], {"max_users": 25, "max_companies": "many", "flag": True}))
    assert plans.quota_for("pro", "max_users", 5) == 25
    assert plans.quota_for("pro", "max_companies", 3) == 3      # not a number → default
    assert plans.quota_for("pro", "flag", 1) == 1               # a bool is not a number
    assert plans.quota_for("pro", "max_ai", 9) == 9             # unset → default
    assert plans.quota_for("gold", "max_users", 5) == 5         # unknown plan → default
    assert plans.quota_for(None, "max_users", 5) == 5


# ── the resolver hook ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_resolver_applies_the_plan_before_the_account_mask(monkeypatch):
    from types import SimpleNamespace
    from capabilities.permissions import roles

    inc = [i for i in plans.EXCLUDABLE if i != "maintenance"]
    await _load(_plan("free", inc))
    acct = SimpleNamespace(tier="free", disabled_modules="", coaching_enabled=True)

    class _Db:
        async def get_account(self, account_id):
            return acct

    monkeypatch.setattr("infra.platform._db", _Db())
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: _Db())
    fs = await roles._apply_plan_mask(_all_on(), 42)
    assert not fs.can_view_maintenance
    assert fs.can_view_vehicles
    assert plans.last_known_tier(42) == "free"


@pytest.mark.asyncio
async def test_the_resolver_falls_back_to_the_last_known_tier_when_the_row_is_unreadable(monkeypatch):
    await _load(_plan("free", ["vehicles"]), _plan("pro", ["*"]))
    plans.remember_tier(42, "pro")

    class _Db:
        async def get_account(self, account_id):
            raise RuntimeError("db down")

    from capabilities.permissions import roles
    monkeypatch.setattr("infra.platform._db", _Db())
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: _Db())
    fs = await roles._apply_plan_mask(_all_on(), 42)
    assert fs.can_view_maintenance                        # pro, remembered
    fs2 = await roles._apply_plan_mask(_all_on(), 43)     # never seen → closed
    assert not fs2.can_view_maintenance


@pytest.mark.asyncio
async def test_a_process_with_no_platform_has_no_plan_to_apply(monkeypatch):
    """A unit test or a script: no platform, no mask — the account mask's
    own skip.  Only a LIVE platform that fails closes the sellable set."""
    from capabilities.permissions import roles
    monkeypatch.setattr("infra.platform._db", None)
    fs = await roles._apply_plan_mask(_all_on(), 42)      # never-loaded table, no row
    assert fs == _all_on()


@pytest.mark.asyncio
async def test_feature_available_gives_the_same_answer_as_the_mask():
    from types import SimpleNamespace
    from capabilities.permissions.modules import feature_available

    await _load(_plan("free", [i for i in plans.EXCLUDABLE if i != "maintenance"]))
    acct = SimpleNamespace(tier="free", disabled_modules="", coaching_enabled=True)
    assert not feature_available(acct, "maintenance")
    assert feature_available(acct, "vehicles")
    assert feature_available(acct, "coaching")
    acct.tier = "gold"                                     # unknown plan → closed
    assert not feature_available(acct, "vehicles")
