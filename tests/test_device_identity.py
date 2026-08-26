"""Identity watch — hardware changes become recorded events.

Truck 128's odometer changed scale silently and surfaced two days
later as a 337,931-mile month.  The watch compares each ingest tick
against the registry's identity anchors (VIN, gateway serial) and the
previous live odometer, and appends deduped events the operator hears
about immediately.
"""

from __future__ import annotations

import pytest

from capabilities.integrations.samsara.sync import _detect_identity_events

IDENT = {"ref1": {"vin": "1XKAD49X1KJ211111", "gateway_serial": "G-AAA",
                  "registry_id": 37, "unit_number": "128",
                  "company_code": "PTG"}}
FLEET = [{"id": "ref1", "name": "128", "vin": "1XKAD49X1KJ211111",
          "gateway_serial": "G-AAA"}]
NOW = "2026-08-07T10:00:00+00:00"


def test_no_change_no_events():
    prev = {"ref1": {"odometer_mi": 567781.0,
                     "odometer_time": "2026-08-07T09:59:00Z"}}
    assert _detect_identity_events(
        FLEET, IDENT, prev,
        {"ref1": {"miles": 567782.0, "time": "2026-08-07T10:00:00Z"}}, NOW,
    ) == []


def test_vin_change_is_a_different_truck():
    fleet = [dict(FLEET[0], vin="2NKHHM6X2FM999999")]
    ev = _detect_identity_events(fleet, IDENT, {}, {}, NOW)
    assert [e["kind"] for e in ev] == ["vin_change"]
    assert ev[0]["old_value"].endswith("211111")


def test_gateway_swap_detected():
    fleet = [dict(FLEET[0], gateway_serial="G-ZZZ")]
    ev = _detect_identity_events(fleet, IDENT, {}, {}, NOW)
    assert [e["kind"] for e in ev] == ["gateway_swap"]


def test_odometer_rebase_gap_aware():
    """+336k in one minute = scale change; +200 after a 3h silence is
    plausible catch-up and stays silent."""
    prev = {"ref1": {"odometer_mi": 567781.0,
                     "odometer_time": "2026-08-07T09:59:00Z"}}
    ev = _detect_identity_events(
        FLEET, IDENT, prev,
        {"ref1": {"miles": 904200.0, "time": "2026-08-07T10:00:00Z"}}, NOW,
    )
    assert [e["kind"] for e in ev] == ["odo_rebase"]

    prev_gap = {"ref1": {"odometer_mi": 567781.0,
                         "odometer_time": "2026-08-07T07:00:00Z"}}
    assert _detect_identity_events(
        FLEET, IDENT, prev_gap,
        {"ref1": {"miles": 567981.0, "time": "2026-08-07T10:00:00Z"}}, NOW,
    ) == []


def test_ordinary_driving_is_not_a_scale_change():
    """The production false positive this rule shipped with.

    Unit 130 reports its odometer only every few hours.  The gap was
    read from the row's ``source_ts`` — refreshed every minute by GPS —
    so the threshold collapsed to a flat 50 miles and nine ordinary
    days' driving (69 to 400 miles) were each filed as "Odometer
    changed scale".  Ten rows on the review card, nine of them wrong.
    """
    prev = {"ref1": {"odometer_mi": 769273.0,
                     # Last odometer six hours ago...
                     "odometer_time": "2026-08-13T10:00:00Z",
                     # ...while the ROW itself was refreshed seconds ago.
                     "source_ts": "2026-08-13T15:59:00Z"}}
    assert _detect_identity_events(
        FLEET, IDENT, prev,
        {"ref1": {"miles": 769673.0, "time": "2026-08-13T16:00:00Z"}},
        "2026-08-13T16:00:00+00:00",
    ) == [], "400 miles in six hours is a truck driving, not a re-base"


def test_untimed_readings_flag_only_the_impossible():
    """With no reading times we cannot measure a gap, so we refuse to
    invent a threshold: only a jump no elapsed time could excuse."""
    prev = {"ref1": {"odometer_mi": 769273.0}}
    assert _detect_identity_events(
        FLEET, IDENT, prev, {"ref1": {"miles": 769673.0}}, NOW,
    ) == []
    ev = _detect_identity_events(
        FLEET, IDENT, prev, {"ref1": {"miles": 1_107_000.0}}, NOW,
    )
    assert [e["kind"] for e in ev] == ["odo_rebase"]


@pytest.mark.asyncio
async def test_event_log_dedupes_exact_transitions(pg_db):
    e = {"registry_id": 37, "vehicle_id": "ref1", "vehicle_name": "128",
         "kind": "odo_rebase", "old_value": "567781",
         "new_value": "904200", "observed_at": NOW}
    first = await pg_db.record_device_events(1, [e])
    again = await pg_db.record_device_events(1, [e])  # same transition
    rows = await pg_db.get_device_events(1)
    assert len(rows) == 1 and rows[0]["kind"] == "odo_rebase"
    # The return value is the notify list: only the FIRST sighting is
    # "new" — a re-detected transition must never re-ping the admins.
    assert [x["kind"] for x in first] == ["odo_rebase"]
    assert again == []


@pytest.mark.asyncio
async def test_notices_go_to_the_accounts_admins(monkeypatch):
    """Identity events are the ACCOUNT's fleet news: delivery rides
    notify_user to that account's admins — never an operator channel
    (tenant data must not cross the platform wall)."""
    from types import SimpleNamespace

    from capabilities.alerting import device_identity as di

    sent = []

    async def fake_notify_user(db, account_id, user_id, content, **kw):
        sent.append((account_id, user_id, content.category, content.body))

    class FakeDB:
        async def get_account_admins(self, account_id):
            return [SimpleNamespace(id=7), SimpleNamespace(id=9)]

    monkeypatch.setattr("capabilities.notifications.notify_user",
                        fake_notify_user)
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: FakeDB())

    await di.notify_device_identity_events(1, [
        {"vehicle_id": "ref1", "vehicle_name": "128", "kind": "odo_rebase",
         "old_value": "567781", "new_value": "904200", "observed_at": NOW},
    ])
    assert [(a, u) for a, u, _, _ in sent] == [(1, 7), (1, 9)]
    assert all(cat == "alert.device_identity" for _, _, cat, _ in sent)
    assert "128" in sent[0][3] and "904200" in sent[0][3]


def test_category_is_registered_for_admin_roles():
    import capabilities.alerting.notification_categories  # noqa: F401
    from capabilities.notifications.categories import get_category

    cat = get_category("alert.device_identity")
    assert cat is not None and cat.kind == "targeted"
    assert cat.audience("admin") and not cat.audience("driver")


def test_spec_fill_fields_all_exist_on_the_model():
    """Every merge field must be a real Vehicle attribute — a name in
    _SPEC_FILL without a model field made the registry upsert throw
    AttributeError on EVERY ingest tick (gateway_serial, 2026-08-07),
    silently starving the registry while the ingest carried on."""
    import dataclasses

    from adapters.storage.vehicles_registry import _SPEC_FILL, Vehicle

    model_fields = {f.name for f in dataclasses.fields(Vehicle)}
    missing = [f for f in _SPEC_FILL if f not in model_fields]
    assert not missing, f"_SPEC_FILL names absent from Vehicle: {missing}"


# ── Answering leaves a record ───────────────────────────────────────
#
# Answering an identity question is an ACCOUNT-WIDE act: the row goes
# inactive for everyone and the question leaves every admin's screen.
# "Same truck" also welds two identities' history together permanently.
# The warehouse row carried who and when, but the activity trail — the
# place an owner actually browses — knew nothing about it.


@pytest.mark.asyncio
async def test_answering_a_device_question_is_recorded_against_the_truck():
    from features.vehicles.router import _record_device_event_answer

    calls: list[dict] = []

    async def fake_record(db, account_id, actor, action, etype, eid, **kw):
        calls.append({"account_id": account_id, "actor": actor,
                      "action": action, "entity_type": etype,
                      "entity_id": eid, **kw})

    import capabilities.activity_trail as trail
    original = trail.record_simple
    trail.record_simple = fake_record
    try:
        await _record_device_event_answer(
            object(), 10000001, 42,
            {"id": 7, "kind": "vin_change", "vehicle_id": "abc-123",
             "vehicle_name": "128", "registry_id": 555,
             "old_value": "4V4NC9EH8KN196862",
             "new_value": "3AKJGLDV5GSGZ4085",
             "observed_at": "2026-08-13T04:00:00Z"},
            "same_truck", None,
        )
    finally:
        trail.record_simple = original

    assert len(calls) == 1
    c = calls[0]
    # Filed on the TRUCK, so it lands on the timeline an owner opens.
    assert c["entity_type"] == "vehicle" and c["entity_id"] == 555
    # The actor comes from the session, never the body.
    assert c["actor"] == 42
    # Named for the ANSWER — which way it went is the whole decision.
    assert c["action"] == "device_event.same_truck"
    # Keyed by the id the dashboard rendered, so a later audit can ask
    # "what became of THIS question" and join the two halves.
    assert c["context"]["callout_id"] == (
        "vehicle.vin_changed@vehicle:abc-123#2026-08-13T04:00:00Z")
    # Values, not prose: what the state WAS, not merely that it moved.
    assert c["context"]["old_value"] == "4V4NC9EH8KN196862"
    assert c["context"]["new_value"] == "3AKJGLDV5GSGZ4085"
    # The note says what the PERSON did, in the words they saw.
    assert c["note"] == (
        "Same truck — 128: 4V4NC9EH8KN196862 → 3AKJGLDV5GSGZ4085")


@pytest.mark.asyncio
async def test_a_split_records_the_unit_it_created():
    from features.vehicles.router import _record_device_event_answer

    calls: list[dict] = []

    async def fake_record(db, account_id, actor, action, etype, eid, **kw):
        calls.append({"action": action, **kw})

    import capabilities.activity_trail as trail
    original = trail.record_simple
    trail.record_simple = fake_record
    try:
        await _record_device_event_answer(
            object(), 10000001, 42,
            {"id": 7, "kind": "vin_change", "vehicle_id": "abc-123",
             "vehicle_name": "128", "registry_id": 555,
             "old_value": "A", "new_value": "B",
             "observed_at": "2026-08-13T04:00:00Z"},
            "different_truck:new_unit=PTG/301", 900,
        )
    finally:
        trail.record_simple = original

    # The action is the CHOICE, stripped of its parameters; the
    # parameters survive in the context rather than fragmenting the
    # vocabulary into one action per unit number.
    assert calls[0]["action"] == "device_event.different_truck"
    assert calls[0]["note"].startswith("Different truck — 128:")
    assert calls[0]["context"]["new_vehicle_id"] == 900
    assert calls[0]["context"]["resolution"] == "different_truck:new_unit=PTG/301"


@pytest.mark.asyncio
async def test_a_trail_failure_does_not_undo_a_completed_answer():
    """The registry surgery already happened.

    Raising here would tell the caller nothing happened when a unit had
    just been created and a telematics link moved — a worse lie than a
    missing trail entry, which at least leaves the warehouse row's own
    resolved_by/resolved_at intact.
    """
    from features.vehicles.router import _record_device_event_answer

    async def boom(*a, **kw):
        raise RuntimeError("trail down")

    import capabilities.activity_trail as trail
    original = trail.record_simple
    trail.record_simple = boom
    try:
        await _record_device_event_answer(
            object(), 10000001, 42,
            {"id": 7, "kind": "vin_change", "vehicle_id": "abc-123",
             "vehicle_name": "128", "registry_id": 555,
             "old_value": "A", "new_value": "B",
             "observed_at": "2026-08-13T04:00:00Z"},
            "same_truck", None,
        )
    finally:
        trail.record_simple = original


@pytest.mark.asyncio
async def test_an_unknown_event_kind_records_nothing():
    """No callout key means no id to file it under — a trail entry
    keyed on an empty string is worse than none."""
    from features.vehicles.router import _record_device_event_answer

    calls: list = []

    async def fake_record(*a, **kw):
        calls.append(1)

    import capabilities.activity_trail as trail
    original = trail.record_simple
    trail.record_simple = fake_record
    try:
        await _record_device_event_answer(
            object(), 10000001, 42,
            {"id": 7, "kind": "something_new", "vehicle_id": "abc-123",
             "observed_at": "2026-08-13T04:00:00Z"},
            "dismissed", None,
        )
    finally:
        trail.record_simple = original
    assert calls == []


@pytest.mark.asyncio
async def test_the_note_uses_the_word_on_the_button_not_the_wire_value():
    """`dismissed` is what the column stores; "Confirm" is what the
    person clicked.  A trail reporting the storage vocabulary makes a
    reader translate their own action back into it."""
    from features.vehicles.router import _record_device_event_answer

    calls: list[dict] = []

    async def fake_record(db, account_id, actor, action, etype, eid, **kw):
        calls.append({"action": action, **kw})

    import capabilities.activity_trail as trail
    original = trail.record_simple
    trail.record_simple = fake_record
    try:
        await _record_device_event_answer(
            object(), 10000001, 42,
            {"id": 9, "kind": "gateway_swap", "vehicle_id": "abc-9",
             "vehicle_name": "254", "registry_id": 77,
             "old_value": "G4WU-EEF-5B9", "new_value": "GZPF-V5G-6GP",
             "observed_at": "2026-08-20T00:00:00Z"},
            "dismissed", None,
        )
    finally:
        trail.record_simple = original

    assert calls[0]["note"] == "Confirmed — 254: G4WU-EEF-5B9 → GZPF-V5G-6GP"
    # The stored resolution is untouched — it rides in the context.
    assert calls[0]["context"]["resolution"] == "dismissed"
    assert calls[0]["action"] == "device_event.dismissed"


def test_the_resolve_endpoint_actually_calls_the_recorder():
    """The lesson from the dismissal path, applied here.

    Eight green tests once covered a dismissal endpoint no screen could
    reach, because they called it directly instead of travelling the
    route a user takes.  These tests exercise the recorder in
    isolation, so this one checks the one thing they cannot: that
    ``resolve_device_event`` is wired to it at all.
    """
    import ast
    import inspect

    from features.vehicles import router as vehicles_router

    src = inspect.getsource(vehicles_router)
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "resolve_device_event"
    )
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_record_device_event_answer" in called, (
        "an identity answer would go unrecorded — the trail entry is "
        "the only place an owner can see who welded two histories "
        "together"
    )
