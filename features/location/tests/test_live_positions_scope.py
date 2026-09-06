"""The live-position endpoint admits a vehicle by identity, not by text.

``/map/vehicles/live`` is polled every five seconds by the dashboard's
Live Map, the Telegram mini app and the browser extension.  It used to
decide membership with a SUBSTRING over the display name, so a member
assigned unit "1" also received positions for 110, 128 and 101, and an
assignment of "230" reached 2303.  Those are the rows a narrowed member
is ALLOWED to see, which makes an over-match a disclosure rather than a
cosmetic bug — the same argument that retired the substring on the list
endpoint beside it (capabilities/permissions/vehicle_scope).

These tests exercise the ladder against the shapes that actually broke.
"""
from capabilities.permissions.vehicle_scope import VehicleIdentity, VehicleScope


def _admitted(scope: VehicleScope, raw: list[dict]) -> set[str]:
    """What the endpoint's filter keeps, expressed on the raw payload."""
    return {
        str(v.get("id")) for v in raw
        if v.get("id") is not None and scope.allows_row(v)
    }


def test_a_shorter_unit_number_does_not_admit_the_longer_ones():
    raw = [
        {"id": "p1", "name": "1"},
        {"id": "p2", "name": "110"},
        {"id": "p3", "name": "128"},
        {"id": "p4", "name": "101"},
    ]
    scope = VehicleScope.from_names(["1"])
    assert _admitted(scope, raw) == {"p1"}


def test_230_does_not_admit_2303():
    raw = [{"id": "a", "name": "230"}, {"id": "b", "name": "2303"}]
    assert _admitted(VehicleScope.from_names(["230"]), raw) == {"a"}


def test_100_does_not_admit_a_trailer_whose_name_contains_it():
    raw = [{"id": "a", "name": "100"}, {"id": "b", "name": "AK1001"}]
    assert _admitted(VehicleScope.from_names(["100"]), raw) == {"a"}


def test_the_provider_id_decides_when_both_sides_carry_one():
    """Rung 2: a truck the provider renamed still belongs to its driver.

    The assignment says "229"; the provider now calls the vehicle
    "229 Idris Ahmed".  The registry link carries the provider id, so
    the rename cannot cost the driver their own truck.
    """
    raw = [{"id": "s-77", "name": "229 Idris Ahmed"}, {"id": "s-88", "name": "301"}]
    scope = VehicleScope.of(VehicleIdentity.make(external_id="s-77", name="229"))
    assert _admitted(scope, raw) == {"s-77"}


def test_a_vehicle_with_no_usable_rung_is_denied():
    """Wrong-hidden is an annoyance; wrong-shown is a breach."""
    raw = [{"id": "x", "name": ""}]
    assert _admitted(VehicleScope.from_names(["142"]), raw) == set()


def test_an_empty_scope_admits_nothing():
    raw = [{"id": "a", "name": "1"}, {"id": "b", "name": "2"}]
    assert VehicleScope().empty
    assert _admitted(VehicleScope(), raw) == set()


def test_a_mixed_linkage_assignment_keeps_the_unlinked_truck():
    """The edge this endpoint felt worst, now closed.

    A driver holds two trucks; only one has been linked to the provider
    in our registry yet.  While the scope pooled its rungs, the linked
    truck's provider id made the ladder commit to rung 2 for BOTH rows,
    so the unlinked truck missed and was denied — the driver lost their
    own truck here, and only here, because the raw provider payload
    this endpoint reads carries no registry id to answer on rung 1.

    Each vehicle now answers for itself, so the unlinked one is still
    judged on its name.
    """
    scope = VehicleScope.of(
        VehicleIdentity.make(registry_id=1, name="100"),               # not linked yet
        VehicleIdentity.make(registry_id=2, external_id="prov-200", name="200"),
    )
    raw = [{"id": "prov-100", "name": "100"}, {"id": "prov-200", "name": "200"}]
    assert _admitted(scope, raw) == {"prov-100", "prov-200"}


def test_a_linked_truck_still_refuses_a_stranger_with_the_same_number():
    """What per-vehicle must NOT cost: unit numbers are reused across
    companies, so another company's "200" carries a different provider
    id.  The assigned truck is linked, so it answers on rung 2 and says
    no — it never drops to the name it shares."""
    scope = VehicleScope.of(
        VehicleIdentity.make(registry_id=1, name="100"),
        VehicleIdentity.make(registry_id=2, external_id="prov-200", name="200"),
    )
    raw = [{"id": "other-co-200", "name": "200"}]
    assert _admitted(scope, raw) == set()


def test_a_name_only_scope_still_matches_every_assigned_truck():
    """A driver whose trucks are none of them linked keeps rung 3,
    which is the ladder's own documented floor."""
    scope = VehicleScope.from_names(["100", "200"])
    raw = [{"id": "prov-100", "name": "100"}, {"id": "prov-200", "name": "200"}]
    assert _admitted(scope, raw) == {"prov-100", "prov-200"}


def test_the_flattened_constructor_is_gone():
    """Pooled sets are what chose a rung from the wrong vehicle.  A call
    site that rebuilt them must fail loudly, not quietly work."""
    import pytest
    with pytest.raises(TypeError):
        VehicleScope(registry_ids=frozenset({1}))       # type: ignore[call-arg]
