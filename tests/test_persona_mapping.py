"""Unit tests for capabilities.alerting.persona_mapping.

Pure-function tests — no DB, no fixtures.  Locks down the alert_type
→ persona table that the routing resolver consults on every alert
dispatch.  A regression here re-routes traffic silently to the wrong
group, so the assertions are explicit per alert_type, not parametrised
over the dict — keeps the failure message readable.
"""

from __future__ import annotations

import pytest

from capabilities.alerting import persona_mapping as pm
from capabilities.alerting.pipeline import _PIPELINE_TO_ROUTE_KEY


def test_dispatcher_persona_owns_live_operations():
    assert pm.persona_for_alert("geofence") == pm.DISPATCHER
    assert pm.persona_for_alert("parking") == pm.DISPATCHER
    assert pm.persona_for_alert("fuel") == pm.DISPATCHER


def test_safety_persona_owns_behavior_and_media():
    assert pm.persona_for_alert("events") == pm.SAFETY
    assert pm.persona_for_alert("event") == pm.SAFETY
    assert pm.persona_for_alert("camera") == pm.SAFETY


def test_fleet_persona_owns_mechanical_health():
    assert pm.persona_for_alert("faults") == pm.FLEET
    assert pm.persona_for_alert("fault") == pm.FLEET
    assert pm.persona_for_alert("health") == pm.FLEET
    assert pm.persona_for_alert("scorecard") == pm.FLEET
    assert pm.persona_for_alert("maintenance") == pm.FLEET
    assert pm.persona_for_alert("samsara_sync") == pm.FLEET


def test_hr_persona_owns_driver_documents():
    assert pm.persona_for_alert("documents") == pm.HR
    assert pm.persona_for_alert("doc_expiry") == pm.HR


def test_owner_admin_persona_owns_cross_cutting():
    assert pm.persona_for_alert("system") == pm.OWNER_ADMIN
    assert pm.persona_for_alert("reescalate") == pm.OWNER_ADMIN


def test_unknown_alert_type_falls_back_to_owner_admin():
    """Safer than dropping a never-seen alert silently."""
    assert pm.persona_for_alert("brand_new_unmapped_type") == pm.OWNER_ADMIN
    assert pm.persona_for_alert("") == pm.OWNER_ADMIN


def test_personas_tuple_covers_every_returned_persona():
    """Every persona the mapping can return is in the public PERSONAS
    tuple so callers iterating over personas don't miss a bucket."""
    returned = {pm.persona_for_alert(at) for at in pm.all_alert_types()}
    assert returned <= set(pm.PERSONAS)


def test_persona_mapping_covers_every_pipeline_alert_type():
    """Drift detector: every alert_type the pipeline knows how to route
    (``pipeline._PIPELINE_TO_ROUTE_KEY``) must have a persona mapping,
    otherwise the new-mode resolver silently dumps it into the
    owner_admin fallback.

    Failure modes this catches:
      - Adding a new alert_type to pipeline._PIPELINE_TO_ROUTE_KEY
        without updating persona_mapping
      - Renaming an alert_type in one place without the other
    """
    pipeline_types = set(_PIPELINE_TO_ROUTE_KEY.keys())
    mapped_types = set(pm.all_alert_types())
    missing = pipeline_types - mapped_types
    assert not missing, (
        f"alert_types in pipeline but not in persona_mapping: {sorted(missing)}"
    )


def test_all_alert_types_returns_immutable_tuple():
    """Callers iterate over this in tests; must not be a live dict view."""
    assert isinstance(pm.all_alert_types(), tuple)


@pytest.mark.parametrize("persona", ["owner_admin", "dispatcher", "safety", "fleet", "hr"])
def test_persona_constants_match_persona_tuple(persona):
    assert persona in pm.PERSONAS


# ── Inverse view: alert_types_for_persona + alert_types_for_role ────


def test_alert_types_for_persona_dispatcher():
    """Dispatcher → live-ops alert types only."""
    assert pm.alert_types_for_persona(pm.DISPATCHER) == frozenset(
        {"geofence", "parking", "fuel"}
    )


def test_alert_types_for_persona_safety():
    """Safety → behavior + media alerts."""
    assert pm.alert_types_for_persona(pm.SAFETY) == frozenset(
        {"events", "event", "camera"}
    )


def test_alert_types_for_persona_fleet():
    """Fleet → mechanical alerts (both fault aliases included)."""
    assert pm.alert_types_for_persona(pm.FLEET) == frozenset(
        {"fault", "faults", "health", "scorecard", "maintenance", "samsara_sync"}
    )


def test_alert_types_for_persona_hr():
    """HR → driver documents only."""
    assert pm.alert_types_for_persona(pm.HR) == frozenset(
        {"documents", "doc_expiry"}
    )


def test_alert_types_for_persona_owner_admin():
    """Owner/admin persona keeps the cross-cutting tokens only — the
    'see everything' behavior is opt-in via role_sees_all_alerts.
    A safety alert IS NOT in the owner_admin persona's type set."""
    types = pm.alert_types_for_persona(pm.OWNER_ADMIN)
    assert "system" in types
    assert "reescalate" in types
    assert "events" not in types
    assert "faults" not in types


def test_alert_types_for_persona_unknown_returns_empty():
    assert pm.alert_types_for_persona("ghost") == frozenset()


def test_persona_partition_covers_every_alert_type():
    """Every alert_type in the routing table belongs to exactly one
    persona — the inverse partition is total + disjoint per
    (alert_type, persona)."""
    all_partitioned = set()
    for persona in pm.PERSONAS:
        types = pm.alert_types_for_persona(persona)
        # No alert_type should appear under two personas.
        overlap = all_partitioned & types
        assert overlap == set(), (
            f"alert_types {sorted(overlap)} appear under multiple personas"
        )
        all_partitioned |= types
    # Every mapped alert_type ended up in some persona.
    assert all_partitioned == set(pm.all_alert_types())


def test_alert_types_for_role_operational():
    assert pm.alert_types_for_role("dispatcher") == frozenset(
        {"geofence", "parking", "fuel"}
    )
    assert pm.alert_types_for_role("safety") == frozenset(
        {"events", "event", "camera"}
    )
    assert pm.alert_types_for_role("fleet") == frozenset(
        {"fault", "faults", "health", "scorecard", "maintenance", "samsara_sync"}
    )
    assert pm.alert_types_for_role("hr") == frozenset({"documents", "doc_expiry"})


def test_alert_types_for_role_owner_admin_returns_owner_admin_types_only():
    """Strict binding: owner / admin no longer bypass the persona
    filter.  Their persona is OWNER_ADMIN whose alert types are just
    the cross-cutting digest tokens (``system`` / ``reescalate``) —
    NOT faults, events, etc.  Operational triage for an owner happens
    by switching view to Fleet/Safety/Dispatch (the SPA sends
    X-View-As; backend resolves the count for that role's types)."""
    owner_admin_types = frozenset({"system", "reescalate"})
    assert pm.alert_types_for_role("owner") == owner_admin_types
    assert pm.alert_types_for_role("admin") == owner_admin_types
    # Owner / Admin do NOT see operational alert types in their default view.
    assert "faults" not in pm.alert_types_for_role("owner")
    assert "events" not in pm.alert_types_for_role("admin")


def test_alert_types_for_role_unmapped_returns_empty():
    """Accounting / Driver / unknown roles return empty.  Callers
    treat empty as "no operational pending alerts at this scope" —
    accounting uses cost reports, driver is vehicle-scoped via a
    different code path."""
    assert pm.alert_types_for_role("accounting") == frozenset()
    assert pm.alert_types_for_role("driver") == frozenset()
    assert pm.alert_types_for_role("unknown") == frozenset()
