"""Activity Trail — the contract, pinned.

Phase 1 covers the pure helpers and the structural contract; adopter
round-trips ride each feature's own router tests as they convert.
"""

import inspect

import pytest

from capabilities.activity_trail import (
    delete_changes, diff_rows, mask_changes, new_group_id,
)


# ── diff_rows ─────────────────────────────────────────────────────

def test_diff_captures_from_and_to():
    old = {"due_miles": 236772, "priority": "medium"}
    new = {"due_miles": 250000, "priority": "medium"}
    assert diff_rows(old, new) == {"due_miles": {"from": 236772, "to": 250000}}


def test_diff_excludes_equal_and_bookkeeping_fields():
    old = {"id": 1, "account_id": 9, "updated_at": "a", "note": "x"}
    new = {"id": 1, "account_id": 9, "updated_at": "b", "note": "x"}
    assert diff_rows(old, new) == {}


def test_diff_numeric_equality_does_not_invent_edits():
    # 35000 (int from the API) vs 35000.0 (real from the DB) is NOT an edit
    assert diff_rows({"i": 35000}, {"i": 35000.0}) == {}
    # but bool is not silently numeric: True vs 1 IS a change
    assert diff_rows({"b": True}, {"b": 1}) != {}


def test_diff_clearing_a_field_is_a_change():
    assert diff_rows({"vendor": "ACME"}, {"vendor": None}) == {
        "vendor": {"from": "ACME", "to": None}
    }
    assert diff_rows({"vendor": ""}, {"vendor": None}) == {
        "vendor": {"from": "", "to": None}
    }


def test_diff_fields_allowlist_narrows():
    old = {"a": 1, "b": 1}
    new = {"a": 2, "b": 2}
    assert diff_rows(old, new, fields=["a"]) == {"a": {"from": 1, "to": 2}}


# ── delete_changes: the recovery record ───────────────────────────

def test_delete_carries_every_meaningful_field_to_null():
    row = {"id": 64, "account_id": 1, "vehicle_name": "224",
           "due_miles": 236772.0, "note": "", "vendor": None,
           "created_at": "2026-06-02"}
    body = delete_changes(row)
    assert body == {
        "vehicle_name": {"from": "224", "to": None},
        "due_miles": {"from": 236772.0, "to": None},
        # created_at is DATA in a recovery record — a restored row
        # keeps its original birth date
        "created_at": {"from": "2026-06-02", "to": None},
    }
    # every "from" is the restore value — replaying them rebuilds the row
    assert all(v["to"] is None for v in body.values())


# ── groups ────────────────────────────────────────────────────────

def test_group_ids_are_unique_opaque_strings():
    a, b = new_group_id(), new_group_id()
    assert a != b and isinstance(a, str) and len(a) == 32


# ── sensitive masking (reader side) ───────────────────────────────

def test_mask_hides_values_but_not_field_names():
    changes = {"cdl_number": {"from": "D123", "to": "D999"},
               "truck_num": {"from": "1", "to": "2"}}
    masked = mask_changes("driver", changes, viewer_can_see=False)
    assert masked["cdl_number"] == {"from": "•••", "to": "•••"}
    assert masked["truck_num"] == changes["truck_num"]      # not sensitive
    # holder of the owning feature's permission sees everything
    assert mask_changes("driver", changes, viewer_can_see=True) == changes


def test_mask_never_mutates_at_write_shape():
    # unknown entity types pass through untouched — masking is opt-in
    changes = {"anything": {"from": 1, "to": 2}}
    assert mask_changes("mystery", changes, viewer_can_see=False) == changes


# ── the read facade: one wire shape from four arms ────────────────

def test_facade_normalizes_all_arm_shapes_to_one():
    from capabilities.activity_trail.facade import (
        normalize_inventory, normalize_load, normalize_trail,
    )
    trail = normalize_trail({
        "id": 1, "entity_type": "maintenance_task", "entity_id": "64",
        "action": "delete", "changes": {"due_miles": {"from": 236772, "to": None}},
        "actor_user_id": 7, "group_id": "g1", "context": {}, "note": "",
        "created_at": "2026-07-31T10:00:00+00:00",
    })
    load = normalize_load({
        "id": 2, "load_id": 9, "event_type": "edited",
        "changes": {"total_rate": [1500, 1700]},        # legacy pair form
        "actor_user_id": 7, "dispatcher_user_id": None, "note": "",
        "created_at": "2026-07-31T09:00:00+00:00",
    })
    inv = normalize_inventory({
        "id": 3, "item_id": 5, "event_type": "status_change",
        "from_status": "missing", "to_status": "installed",
        "from_vehicle_id": None, "to_vehicle_id": None,
        "actor_user_id": 7, "driver_user_id": 11, "note": "",
        "created_at": "2026-07-31T08:00:00+00:00",
    })
    for ev in (trail, load, inv):
        assert set(ev) >= {"source", "entity_type", "entity_id", "action",
                           "changes", "actor_user_id", "actor_space",
                           "created_at"}
    # every arm's changes speak {from,to} — including loads' pair form
    assert load["changes"] == {"total_rate": {"from": 1500, "to": 1700}}
    assert inv["changes"]["status"] == {"from": "missing", "to": "installed"}
    assert trail["actor_space"] == "platform"


def test_facade_merge_is_newest_first_and_capped():
    from capabilities.activity_trail.facade import merge_arms
    a = [{"created_at": "2026-07-31T10:00:00"}, {"created_at": "2026-07-31T08:00:00"}]
    b = [{"created_at": "2026-07-31T09:00:00"}]
    merged = merge_arms(a, b, limit=2)
    assert [e["created_at"][11:13] for e in merged] == ["10", "09"]


def test_facade_collapses_bulk_groups_without_losing_count():
    from capabilities.activity_trail.facade import collapse_groups
    ev = lambda i, gid=None: {
        "source": "trail", "id": f"trail:{i}", "entity_type": "maintenance_task",
        "entity_id": str(i), "action": "delete", "changes": {},
        "actor_user_id": 7, "actor_space": "platform", "group_id": gid,
        "context": {}, "note": "", "created_at": f"2026-07-31T10:00:{i:02d}",
    }
    rows = [ev(1), ev(2, "g"), ev(3, "g"), ev(4, "g"), ev(5)]
    out = collapse_groups(rows)
    assert len(out) == 3                       # single + group + single
    group = next(e for e in out if e.get("is_group"))
    assert group["count"] == 3                 # NEVER truncated
    assert group["sample_entity_ids"] == ["2", "3", "4"]


# ── structural contract on the storage mixin ─────────────────────

def test_append_never_commits_and_requires_system_context():
    """Same-transaction is the whole point: the writer must not commit
    (the caller's mutation transaction commits both), and an actorless
    event must declare itself as system-on-behalf."""
    from adapters.storage.activity_trail import ActivityTrailMixin
    src = inspect.getsource(ActivityTrailMixin.append_activity_events)
    assert ".commit()" not in src, "append must ride the caller's transaction"
    assert "system" in src, "actorless events must declare context['system']"


def test_mixin_is_registered_on_database():
    from adapters.storage import ActivityTrailMixin, Database
    assert issubclass(Database, ActivityTrailMixin)
    assert hasattr(Database, "append_activity_events")
    assert hasattr(Database, "list_activity_events")
    assert hasattr(Database, "prune_activity_events")


def test_migration_registered_exactly_once_at_the_end():
    from adapters.storage.migrations import _MIGRATIONS
    names = [n for n, _ in _MIGRATIONS]
    assert names.count("175_activity_events") == 1
    # appended after everything that existed when it shipped
    assert names.index("175_activity_events") > names.index("170_load_events")


def test_no_fourth_event_table_rule():
    """Advisor ruling: the facade unions EXACTLY three arms —
    activity_events + the two shipped rich tables.  A new per-feature
    *_events table is the drift this test exists to catch."""
    import pathlib
    import re
    allowed = {
        "activity_events", "load_events", "vehicle_inventory_events",
        # Grandfathered non-trail tables that merely end in _events —
        # machine stores (webhook idempotency, telemetry, scoring), not
        # who-did-what trails.  Do NOT grow this list with a trail.
        "processed_stripe_events", "email_webhook_events",
        "parking_events", "score_events",
    }
    root = pathlib.Path(__file__).resolve().parent.parent / "adapters" / "storage"
    offenders = set()
    for f in root.glob("*.py"):
        for m in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+_events)\b", f.read_text()
        ):
            if m.group(1) not in allowed:
                offenders.add(f"{f.name}:{m.group(1)}")
    assert not offenders, (
        f"new per-feature event table(s) {offenders} — adopt "
        "activity_events instead (capabilities/activity_trail)"
    )


# ── the entity registry: feature declarations, hub engine ─────────

def test_registry_loads_and_permissions_are_real_featureset_flags():
    from dataclasses import fields
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded,
    )
    from capabilities.permissions.roles import FeatureSet
    ensure_declarations_loaded()
    ensure_declarations_loaded()                     # idempotent
    assert len(_ENTITIES) >= 20
    real_flags = {f.name for f in fields(FeatureSet)}
    for et, d in _ENTITIES.items():
        assert d.view_permissions, f"{et} declares no view permission"
        for p in d.view_permissions:
            assert p in real_flags, (
                f"{et} declares unknown permission {p!r} — "
                "fix the feature's activity.py"
            )


def test_registry_and_frontend_entity_vocabulary_agree():
    """The frontend's ENTITY_LABEL map and the backend registry must
    cover each other — a new entity type needs BOTH its activity.py
    declaration and its display label, or history renders raw keys /
    404s."""
    import pathlib
    import re
    from capabilities.activity_trail.registry import (
        ensure_declarations_loaded, registered_entity_types,
    )
    ensure_declarations_loaded()
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "interfaces/dashboard/src/components/history/HistoryList.tsx"
           ).read_text()
    block = re.search(r"ENTITY_LABEL[^=]*= \{(.*?)\};", src, re.S).group(1)
    frontend = set(re.findall(r"^\s*([a-z_]+):", block, re.M))
    backend = set(registered_entity_types())
    assert frontend - backend == set(), (
        f"frontend labels without registry declarations: {frontend - backend}"
    )
    assert backend - frontend == set(), (
        f"registered entity types without frontend labels: {backend - frontend}"
    )
