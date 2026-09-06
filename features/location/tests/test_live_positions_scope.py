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
from capabilities.permissions.vehicle_scope import VehicleScope


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
    scope = VehicleScope(names=frozenset({"1"}))
    assert _admitted(scope, raw) == {"p1"}


def test_230_does_not_admit_2303():
    raw = [{"id": "a", "name": "230"}, {"id": "b", "name": "2303"}]
    assert _admitted(VehicleScope(names=frozenset({"230"})), raw) == {"a"}


def test_100_does_not_admit_a_trailer_whose_name_contains_it():
    raw = [{"id": "a", "name": "100"}, {"id": "b", "name": "AK1001"}]
    assert _admitted(VehicleScope(names=frozenset({"100"})), raw) == {"a"}


def test_the_provider_id_decides_when_both_sides_carry_one():
    """Rung 2: a truck the provider renamed still belongs to its driver.

    The assignment says "229"; the provider now calls the vehicle
    "229 Idris Ahmed".  The registry link carries the provider id, so
    the rename cannot cost the driver their own truck.
    """
    raw = [{"id": "s-77", "name": "229 Idris Ahmed"}, {"id": "s-88", "name": "301"}]
    scope = VehicleScope(external_ids=frozenset({"s-77"}), names=frozenset({"229"}))
    assert _admitted(scope, raw) == {"s-77"}


def test_a_vehicle_with_no_usable_rung_is_denied():
    """Wrong-hidden is an annoyance; wrong-shown is a breach."""
    raw = [{"id": "x", "name": ""}]
    assert _admitted(VehicleScope(names=frozenset({"142"})), raw) == set()


def test_an_empty_scope_admits_nothing():
    raw = [{"id": "a", "name": "1"}, {"id": "b", "name": "2"}]
    assert VehicleScope().empty
    assert _admitted(VehicleScope(), raw) == set()


def test_a_mixed_linkage_assignment_loses_the_unlinked_truck_here():
    """The ladder's sharp edge, pinned so it is known rather than found.

    A driver holds two trucks; only one has been linked to the provider
    in our registry yet.  The scope then carries a non-empty external-id
    set, so ``allows`` commits to rung 2 for BOTH rows — including the
    unlinked truck's, whose provider id is not in the set.  It stops
    there without trying the name that would have matched.

    The driver loses their own truck on this endpoint (the list endpoint
    still shows it: that payload carries registry ids, so rung 1
    answers).  It fails CLOSED, which is why it ships: the substring
    this replaced admitted four trucks that were never theirs.

    The fix is NOT to fall through to the name.  Unit numbers are reused
    across companies in one account, so a name match would admit another
    company's truck of the same number — a disclosure, in exchange for
    an annoyance.  Correcting it properly means the scope carrying one
    identity per vehicle instead of three flattened sets, which is its
    own change across every consumer.
    """
    scope = VehicleScope(
        registry_ids=frozenset({1, 2}),
        external_ids=frozenset({"prov-200"}),   # only truck 200 is linked
        names=frozenset({"100", "200"}),
    )
    raw = [{"id": "prov-100", "name": "100"}, {"id": "prov-200", "name": "200"}]
    assert _admitted(scope, raw) == {"prov-200"}


def test_a_name_only_scope_still_matches_every_assigned_truck():
    """The edge above needs SOME linked truck to bite.  A driver whose
    trucks are none of them linked keeps rung 3, which is the state the
    ladder's own docstring calls its floor."""
    scope = VehicleScope(names=frozenset({"100", "200"}))
    raw = [{"id": "prov-100", "name": "100"}, {"id": "prov-200", "name": "200"}]
    assert _admitted(scope, raw) == {"prov-100", "prov-200"}
