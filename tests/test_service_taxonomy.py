"""The repair taxonomy module: System (L1) → Assembly (L2) → Part (L3).

Pins the two things the extraction exists to guarantee: the old import
names still resolve to the SAME objects (so ~15 call sites, migrations
included, never had to be touched), and THE DELEGATION RULE has exactly
one source — the SQL predicate is generated from ``DELEGATING_SYSTEMS``
rather than hand-copied, because a stale copy misfiles spend silently.
"""
from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import service_assemblies, service_tasks, service_taxonomy


# ── The rename is an alias, not a copy ──────────────────────────────

def test_deprecated_alias_is_the_primary_object():
    """``SERVICE_TASK_SYSTEMS`` said the axis belonged to service tasks,
    which is what sent the taxonomy to live in that module.
    ``VEHICLE_SYSTEMS`` is the domain noun; the old name must stay
    bound to the SAME tuple, not a second copy that can drift."""
    assert service_taxonomy.SERVICE_TASK_SYSTEMS is service_taxonomy.VEHICLE_SYSTEMS
    assert service_tasks.SERVICE_TASK_SYSTEMS is service_taxonomy.VEHICLE_SYSTEMS


def test_old_import_sites_still_resolve_to_the_same_objects():
    """Migrations, both platform routers, the feature routers and the
    existing tests all import from the old modules.  Re-export, don't
    reimplement."""
    for name in ("SYSTEM_KEYS", "SYSTEM_LABELS",
                 "normalize_system_key", "suggest_system_for"):
        assert getattr(service_tasks, name) is getattr(service_taxonomy, name), name
    for name in ("SERVICE_ASSEMBLIES", "DELEGATING_SYSTEMS",
                 "normalize_assembly_key", "suggest_assembly_for"):
        assert getattr(service_assemblies, name) is getattr(service_taxonomy, name), name


def test_taxonomy_never_imports_back_from_its_consumers():
    """A back-import would recreate the very cycle the module exists to
    break — L2 used to import SYSTEM_KEYS from the service-task module,
    i.e. from one of L1's consumers."""
    src = open(service_taxonomy.__file__, encoding="utf-8").read()
    assert "from adapters.storage.service_tasks" not in src
    assert "from adapters.storage.service_assemblies" not in src
    assert "from adapters.storage.parts_catalog" not in src


def test_entity_concerns_did_not_move():
    """Storage mixins and the seeded task list are entity concerns; only
    the classification vocabulary belongs here."""
    for name in ("STANDARD_SERVICE_TASKS", "service_task_name_key",
                 "system_bucket_collision", "ServiceTasksMixin"):
        assert hasattr(service_tasks, name)
        assert not hasattr(service_taxonomy, name), f"{name} should NOT have moved"
    assert hasattr(service_assemblies, "ServiceAssembliesMixin")
    assert not hasattr(service_taxonomy, "ServiceAssembliesMixin")


# ── One source for THE DELEGATION RULE ──────────────────────────────

def test_rollup_case_is_generated_from_the_frozenset():
    """The predicate must list exactly the delegating systems — no more
    (a component system would wrongly delegate) and no fewer (a PM
    fleet's parts would stay stuck under PM)."""
    sql = service_taxonomy.system_rollup_case("st.system_key", "al.system_key")
    listed = re.search(r"IN \(([^)]*)\)", sql).group(1)
    assert {v.strip().strip("'") for v in listed.split(",")} \
        == set(service_taxonomy.DELEGATING_SYSTEMS)


def test_rollup_case_keeps_the_two_load_bearing_subtleties():
    """Both were in the hand-written SQL and both matter:
    ``IS NOT NULL`` (a part with NO assembly must stay on the task's
    system rather than delegate to nothing), and the coalesced ELSE
    (an untagged line's task system is NULL, and the reports bucket ''
    as Unassigned, never NULL)."""
    sql = " ".join(
        service_taxonomy.system_rollup_case("st.system_key", "al.system_key").split())
    assert "AND al.system_key IS NOT NULL" in sql
    assert "ELSE COALESCE(st.system_key, '') END" in sql
    assert "COALESCE(st.system_key, '') IN (" in sql


def test_work_orders_has_no_hand_written_delegation_list():
    """The regression this extraction prevents: three literal copies of
    the delegating systems that a change to the frozenset would leave
    stale, misfiling spend with no error anywhere."""
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     "adapters", "storage", "work_orders.py"),
        encoding="utf-8",
    ).read()
    for key in service_taxonomy.DELEGATING_SYSTEMS:
        if key:                       # '' is not a searchable literal
            assert f"'{key}', '" not in src, f"hand-written {key!r} list is back"


def test_every_assembly_still_parents_a_real_system_after_the_move():
    for key, _label, system in service_taxonomy.SERVICE_ASSEMBLIES:
        assert system in service_taxonomy.SYSTEM_KEYS, f"{key} → {system}"
