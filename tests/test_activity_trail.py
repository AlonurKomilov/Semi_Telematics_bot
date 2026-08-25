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
           / "interfaces/dashboard/src/components/activity-trail/ActivityTrailList.tsx"
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


# ── restore honors the company boundary (security review, 2026-08-02) ──

class _Desc:
    """Minimal descriptor stand-in for the scope helper."""
    def __init__(self, company_scoped): self.company_scoped = company_scoped


def _delete_event(company_code):
    changes = {"vehicle_name": {"from": "224", "to": None}}
    if company_code is not None:
        changes["company_code"] = {"from": company_code, "to": None}
    return {"id": 1, "entity_type": "maintenance_task", "entity_id": "64",
            "action": "delete", "changes": changes, "context": {}}


@pytest.mark.asyncio
async def test_restore_scope_matches_the_sibling_routes(monkeypatch):
    """Restore is a WRITE into a company-scoped feature: it must clear
    the same boundary ``_require_company_visible_task`` clears, or it
    becomes the one write path around company assignment."""
    from capabilities.activity_trail import router as trail_router

    async def codes_for(assigned):
        async def _codes(_user):
            return assigned
        return _codes

    async def scope(assigned, company_code, company_scoped=True):
        monkeypatch.setattr(
            trail_router, "get_user_company_codes", await codes_for(assigned),
        )
        return await trail_router._in_company_scope(
            _Desc(company_scoped), _delete_event(company_code), {"role": "fleet"},
        )

    # restricted caller: own company yes, another company NO
    assert await scope(["COMPA"], "COMPA") is True
    assert await scope(["COMPA"], "COMPB") is False
    # case-insensitive, like filter_by_allowed_companies
    assert await scope(["compa"], "COMPA") is True
    # blank / absent company is NOT visible to a restricted caller —
    # exactly what _require_company_visible_task does
    assert await scope(["COMPA"], "") is False
    assert await scope(["COMPA"], None) is False
    # unrestricted caller (empty assignment, incl. owner) passes all
    assert await scope([], "COMPB") is True
    # entities whose routes aren't company-scoped are unaffected
    assert await scope(["COMPA"], "COMPB", company_scoped=False) is True


def test_company_scoped_entities_match_the_features_that_enforce_it():
    """The entity types whose own by-id routes company-filter must say
    so: maintenance tasks, work orders, and the legacy maintenance
    alias whose imported rows point at the same task ids."""
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded,
    )
    ensure_declarations_loaded()
    scoped = {et for et, d in _ENTITIES.items() if d.company_scoped}
    assert scoped == {"maintenance_task", "work_order", "maintenance"}


# ── the company wall must be a DECIDED question, not a default ─────

# Features whose by-id routes enforce the per-user company assignment.
# An entity owned by one of these either declares company_scoped, or
# appears below with the reason its records aren't company-owned.
COMPANY_WALL_FEATURES = frozenset({
    "maintenance", "work_orders", "vehicles", "parts", "coaching",
    "drivers", "loads",
})

# Documented exemptions — each is a decision, not an oversight.
SCOPE_EXEMPT: dict[str, str] = {
    # The part RECORD is account-wide shared vocabulary (name, number).
    # features/parts/router.py company-filters the price ANALYTICS, which
    # the trail never records.
    "part": "part rows are account-wide; only price analytics are scoped",
    # Templates carry no company column — they are account-wide recipes.
    "maintenance_template": "templates are account-wide, no company column",
    # The vehicle registry's by-id routes apply no company wall (only the
    # list/overview do), so history matches its own feature.
    "vehicle": "vehicles' by-id routes are not company-walled",
    # Written to vehicle_inventory_events (a legacy arm), never to
    # activity_events, so this endpoint serves nothing for it today.
    # Revisit the moment inventory adopts activity_events.
    "inventory_item": "writes to vehicle_inventory_events, not this store",
    # Scoped through a driver→company MAP, not a company_code column —
    # needs its own resolver before it can declare the wall.  No events
    # are written for these types today.
    "coaching": "scopes via driver→company map; no events written yet",
    "driver": "scopes via driver→company map; no events written yet",
    # Loads live in load_events (legacy arm) with their own own-scope
    # rules; the generic endpoint reads activity_events only.
    "load": "writes to load_events, not this store",
}


def test_company_scope_is_declared_or_explicitly_exempt():
    """Every entity owned by a company-walled feature must either
    declare the wall or carry a written reason it doesn't need one.

    This is the guard for the class of bug found twice on 2026-08-02:
    the trail served a company-scoped feature's records without the
    wall that feature's own routes enforce.  A new declaration cannot
    default its way past this — the test names the file to edit.
    """
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded,
    )
    ensure_declarations_loaded()
    undecided = [
        f"{et} (features/{d.feature}/activity.py)"
        for et, d in _ENTITIES.items()
        if d.feature in COMPANY_WALL_FEATURES
        and not d.company_scoped
        and et not in SCOPE_EXEMPT
    ]
    assert not undecided, (
        "these entity types are owned by a company-walled feature but "
        f"neither declare company_scoped nor document an exemption: {undecided}"
    )


def test_scope_exemptions_stay_honest():
    """An exemption for an entity that LATER declares the wall is stale
    bookkeeping — and an exemption for an entity nobody registers is
    dead weight."""
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded,
    )
    ensure_declarations_loaded()
    for et in SCOPE_EXEMPT:
        d = _ENTITIES.get(et)
        assert d is not None, f"exemption for unregistered entity {et!r}"
        assert not d.company_scoped, (
            f"{et} now declares the wall — drop its exemption"
        )


# ── the account-wide feed's GATE (security review, 2026-08-24) ──────
#
# /admin/activity is gated on can_manage_users alone.  It computed the
# right per-entity answer and then spent it on FIELD MASKING instead of
# access: `viewer_can_see` reached mask_changes, never a row filter.
# 23 of 26 entities declare no sensitive_fields, so that mask was a
# no-op — an HR user (can_manage_users yes, can_kpi no) read dispatcher
# payouts, load rates, work-order invoices and vendor contacts straight
# off the audit page.  Masking also only ever covered `changes`: never
# `context`, `note`, `entity_id` or `action`.
#
# These pin the fix: an entity you cannot open on its own page is
# ABSENT from the feed, whole.

class _FakeTrailDB:
    """The three arms account_activity fetches, with no database."""

    def __init__(self, rows):
        self._rows = rows

    async def list_activity_events(self, account_id, *, before_ts=None,
                                   limit=100, entity_type=None, group_id=None):
        return [r for r in self._rows
                if entity_type is None or r["entity_type"] == entity_type]

    async def list_trail_legacy_loads(self, *a, **k):
        return []

    async def list_trail_legacy_inventory(self, *a, **k):
        return []


def _trail_row(i, entity_type, marker="x"):
    """Every wire field carries the marker, so a test can prove the whole
    event left — not just its `changes`, the one field masking covered."""
    return {
        "id": i, "entity_type": entity_type, "entity_id": f"id-{marker}",
        "action": "update", "actor_user_id": 7, "group_id": None,
        "changes": {f"dollars_{marker}": {"from": 900, "to": 1200}},
        "context": {"dispatcher": f"person-{marker}"},
        "note": f"note-{marker}",
        "created_at": f"2026-08-24T10:0{i}:00+00:00",
    }


def test_feed_gate_has_no_default_so_a_forgetful_caller_cannot_leak():
    import inspect
    from capabilities.activity_trail.facade import account_activity
    p = inspect.signature(account_activity).parameters["viewer_can_see"]
    assert p.default is inspect.Parameter.empty, (
        "viewer_can_see must stay REQUIRED — a caller that omits the gate "
        "has to get a TypeError, never a silently unfiltered feed"
    )


async def test_feed_drops_entities_the_viewer_cannot_open():
    from capabilities.activity_trail.facade import account_activity
    db = _FakeTrailDB([_trail_row(1, "kpi_run", "GATED"),
                       _trail_row(2, "vehicle", "OPEN")])
    # HR-shaped: holds can_manage_users (so it reached this feed at all)
    # but not can_kpi.
    feed = await account_activity(
        db, 1, viewer_can_see={"kpi_run": False, "vehicle": True},
    )
    kinds = [e["entity_type"] for e in feed]
    assert "kpi_run" not in kinds, "a gated entity must not reach the feed"
    assert "vehicle" in kinds, "the viewer's own entities must still arrive"
    # and it leaves with NOTHING attached — including the four wire
    # fields no field-mask ever covered: context, note, entity_id, action.
    blob = repr(feed)
    assert "GATED" not in blob, (
        "the gated event's payload survived the drop somewhere in "
        f"changes/context/note/entity_id: {blob}"
    )
    assert "OPEN" in blob, "the visible event lost its payload"


async def test_feed_fails_closed_on_unregistered_entity_types():
    """The AI write path (capabilities/ai/actions.py) sets entity_type to
    whatever the executor returned — often "" — and dumps the whole
    approved tool payload into `note`, which no mask has ever covered.
    An unknown type is absent from the flag map, so `.get(et, False)`
    must drop it rather than wave it through."""
    from capabilities.activity_trail.facade import account_activity
    db = _FakeTrailDB([_trail_row(1, ""), _trail_row(2, "not_a_real_entity")])
    feed = await account_activity(db, 1, viewer_can_see={"vehicle": True})
    assert feed == [], "unregistered entity types must fail closed"


async def test_company_restricted_viewer_loses_company_scoped_entities():
    """Team Management's data scope is the second axis of one access
    model.  The aggregate feed cannot test a row's company (create and
    update events record only what CHANGED, and the company almost
    never does), so a restricted viewer is refused company_scoped
    entities wholesale and reads them through the per-record endpoint,
    which resolves the owning row."""
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded, viewer_entity_flags,
    )
    import capabilities.activity_trail.registry as reg
    ensure_declarations_loaded()
    scoped = [et for et, d in _ENTITIES.items() if d.company_scoped]
    assert scoped, "expected at least one company_scoped entity"

    class _Perms:
        def __getattr__(self, _name):      # holds every feature flag
            return True

    async def _all_perms(*a, **k):
        return _Perms()

    import capabilities.permissions.roles as roles
    import interfaces.api.deps as deps
    real_perms, real_codes = roles.get_user_permissions, deps.get_user_company_codes
    try:
        roles.get_user_permissions = _all_perms

        async def _restricted(_user):
            return ["ALPHA"]
        deps.get_user_company_codes = _restricted
        flags = await viewer_entity_flags(
            {"role": "owner", "account_id": 1})
        for et in scoped:
            assert flags[et] is False, (
                f"{et} is company_scoped and must be withheld from a "
                "company-restricted viewer's aggregate feed"
            )

        async def _unrestricted(_user):
            return []
        deps.get_user_company_codes = _unrestricted
        flags = await viewer_entity_flags(
            {"role": "owner", "account_id": 1})
        for et in scoped:
            assert flags[et] is True, (
                f"{et} must stay visible to an unrestricted viewer"
            )
    finally:
        roles.get_user_permissions = real_perms
        deps.get_user_company_codes = real_codes


def test_registry_restore_permissions_are_real_featureset_flags_too():
    """getattr(perms, p, False) swallows a typo silently — a misspelled
    flag reads as 'denied' for view (safe) but the same pattern gates
    restore, so both directions of the SSOT join get pinned."""
    from dataclasses import fields
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded,
    )
    from capabilities.permissions.roles import FeatureSet
    ensure_declarations_loaded()
    real_flags = {f.name for f in fields(FeatureSet)}
    for et, d in _ENTITIES.items():
        for p in d.restore_permissions:
            assert p in real_flags, (
                f"{et} declares unknown restore permission {p!r}"
            )


# ── the AI write path speaks the registry's vocabulary ─────────────
#
# capabilities/ai/actions.py serialises the WHOLE approved tool payload
# into `note`, which no field-mask has ever covered.  Post-fix the feed
# gates on entity type, so the payload now rides the target feature's
# own permission — but only if the executor names a type the registry
# knows.  It didn't: executors return "vehicle_inventory" while the
# registry calls it "inventory_item", and "alert" has no trail entity
# at all.  Unmapped names are invisible to EVERY reader (fail-closed),
# which would have deleted AI writes from the audit log instead.

def test_ai_trail_entity_resolves_or_falls_back_to_a_registered_type():
    from capabilities.ai.actions import _trail_entity_type
    assert _trail_entity_type({"target_type": "work_order"}) == "work_order"
    # the registry's name for the same thing
    assert _trail_entity_type({"target_type": "vehicle_inventory"}) == "inventory_item"
    # nothing named, nothing owning it → the account's own gate
    assert _trail_entity_type({"target_type": ""}) == "account"
    assert _trail_entity_type(None) == "account"
    assert _trail_entity_type({"target_type": "not_a_feature"}) == "account"


def test_ai_write_target_types_are_registered_trail_entities():
    """Pins the executor vocabulary against the registry, in both
    directions — a new AI tool that invents a target_type fails here
    rather than quietly vanishing from the audit log."""
    import pathlib
    import re
    from capabilities.activity_trail.registry import (
        ensure_declarations_loaded, registered_entity_types,
    )
    from capabilities.ai.actions import _AI_TRAIL_ENTITY
    ensure_declarations_loaded()
    reg = registered_entity_types()
    root = pathlib.Path(__file__).resolve().parent.parent
    found: set[str] = set()
    for py in (list((root / "features").rglob("*.py"))
               + list((root / "capabilities").rglob("*.py"))):
        for m in re.finditer(r'"target_type":\s*"([a-z_]+)"', py.read_text()):
            found.add(m.group(1))
    assert found, "no executor target_type literals found — pattern changed?"
    # Deliberately filed against the account: the trail owns no entity
    # for these, so can_manage_account is their honest home.
    account_filed = {"alert"}
    for t in sorted(found):
        mapped = _AI_TRAIL_ENTITY.get(t, t)
        assert mapped in reg or t in account_filed, (
            f"AI executors return target_type {t!r}, which is neither a "
            f"registered trail entity, nor mapped in _AI_TRAIL_ENTITY, nor "
            f"listed as deliberately account-filed. The trail gates on the "
            f"entity type, so an unknown one makes that AI write invisible "
            f"to every reader."
        )
