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
from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted

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


# ── The archive gate ────────────────────────────────────────────────
#
# An operator-archived truck whose gateway is still bolted in kept
# every warehouse-driven feature alive: alerts DMing, maintenance
# posting "overdue" with a live odometer, PTI inspections assigned to
# drivers, scorecard digests, parking and camera AI spend — and the
# billed quantity, which counts `vehicle_state_live.captured_at` and
# never looks at the registry at all.  61 of 88 verified leaks read
# that table without joining `vehicles`, so they cannot ask whether a
# truck is archived.  The gate answers for them, once, at the write.


def _rows(*refs):
    return [{"vehicle_id": r, "name": f"unit-{r}", "speed_mph": 0} for r in refs]


def _gate(rows, archived_refs):
    """The filter exactly as sync.py applies it."""
    return [r for r in rows
            if str(r.get("vehicle_id") or "") not in archived_refs]


def test_the_gate_drops_only_the_archived_ref():
    kept = _gate(_rows("aaa", "bbb", "ccc"), {"bbb"})
    assert [r["vehicle_id"] for r in kept] == ["aaa", "ccc"]


def test_an_empty_archived_set_changes_nothing():
    # Fail-open: a registry read that failed yields an empty set, and one
    # leaked tick is a far smaller harm than blanking a fleet's telemetry
    # on a transient database error.
    rows = _rows("aaa", "bbb")
    assert _gate(rows, set()) == rows


@pytest.mark.asyncio
async def test_only_operator_archived_refs_are_gated(pg_db):
    """Bare `is_active = 0` would zombie a swept truck forever.

    The departure sweep retires a badge that went silent and promises
    it will ingest again the moment it reports — so the gate must key
    on the operator's decision, never on the flag alone.
    """
    acct = 10009001
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "A1", "telematics_ref": "ref-a"},
        {"company_code": "PTG", "unit_number": "B2", "telematics_ref": "ref-b"},
        {"company_code": "PTG", "unit_number": "C3", "telematics_ref": "ref-c"},
    ], source="samsara")
    rows = {v.unit_number: v for v in await pg_db.list_vehicles(acct)}

    # A person archives A1; the sweep retires B2 for silence.
    await pg_db.deactivate_vehicle(acct, rows["A1"].id)
    await pg_db._db.execute(
        "UPDATE vehicles SET is_active = 0 WHERE id = ?", (rows["B2"].id,))
    await pg_db._db.commit()

    gated = await pg_db.operator_archived_refs(acct)
    assert gated == {"ref-a"}, (
        "the swept badge must keep ingesting so it can be revived")


@pytest.mark.asyncio
async def test_archiving_records_why_and_keeps_the_status_it_overwrote(pg_db):
    """Restore has to put the truck back the way it was.

    Archiving stamps status='inactive' over whatever the operator had
    set — 'yard', 'shop', 'available' — so without keeping the previous
    value a restore can only guess 'active' over a truck that was in
    the shop.
    """
    acct = 10009002
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "D4", "telematics_ref": "ref-d",
         "status": "shop"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db._db.execute(
        "UPDATE vehicles SET status = 'shop' WHERE id = ?", (v.id,))
    await pg_db._db.commit()

    await pg_db.deactivate_vehicle(acct, v.id, actor_user_id=7)
    (archived,) = await pg_db.list_vehicles(acct, include_inactive=True)
    assert archived.archived_reason == "operator"
    assert archived.status_before_archive == "shop"
    assert archived.status == "inactive"
    # Identity is NOT destroyed — that is what lets a restore re-attach
    # the truck to its own device in one act.
    assert archived.telematics_ref == "ref-d"


@pytest.mark.asyncio
async def test_the_identity_watch_still_sees_an_archived_truck(pg_db):
    """The gate runs AFTER the watch, and that order is load-bearing.

    If a gateway is pulled off an archived truck and bolted into a
    different one, the VIN change must still be recorded — otherwise
    the archived row keeps a telematics_ref that now names another
    physical truck, and restoring it would re-attach the wrong vehicle.
    """
    acct = 10009003
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "E5", "telematics_ref": "ref-e",
         "vin": "VINOLD0000000001"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db.deactivate_vehicle(acct, v.id)

    ident = await pg_db.get_identity_map(acct)
    assert "ref-e" in ident, (
        "an archived truck must stay visible to the identity watch, or a "
        "gateway moving onto another truck goes unrecorded")


def test_the_state_write_still_has_exactly_one_call_site():
    """The gate filters rows in sync.py, immediately before the single
    call to ``upsert_vehicle_state``.  That is only a complete gate
    while there IS one call site.  A second provider learning to write
    vehicle state would slip past it silently — so the guard is a test,
    not a comment.

    If this fires: move the filter into a shared ingest helper both
    callers use, rather than copying it.
    """
    import re
    from pathlib import Path

    repo = _REPO
    callers = []
    for path in repo.rglob("*.py"):
        parts = set(path.parts)
        if parts & {"node_modules", ".git", "tests", "__pycache__"}:
            continue
        if path.name.startswith("test_"):
            continue
        text = path.read_text(errors="ignore")
        for m in re.finditer(r"\.upsert_vehicle_state\s*\(", text):
            line = text.count("\n", 0, m.start()) + 1
            callers.append(f"{path.relative_to(repo)}:{line}")

    assert len(callers) == 1, (
        "the archive gate covers one write into vehicle_state_live; "
        f"found {len(callers)}: {callers}"
    )


@pytest.mark.asyncio
async def test_alerting_hushes_every_retired_truck_ingest_hushes_only_some(pg_db):
    """Two predicates, deliberately different widths.

    ALERTING (`archived_refs`) is wide: a truck that is not on the fleet
    list should not be paging anyone, whichever way it left.

    INGEST (`operator_archived_refs`) is narrow: a sweep-retired badge
    must be allowed straight back in the moment it reports again, which
    is the departure sweep's documented contract.  Using the wide one
    there would zombie it forever.
    """
    acct = 10009004
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "F6", "telematics_ref": "ref-f"},
        {"company_code": "PTG", "unit_number": "G7", "telematics_ref": "ref-g"},
        {"company_code": "PTG", "unit_number": "H8", "telematics_ref": "ref-h"},
    ], source="samsara")
    rows = {v.unit_number: v for v in await pg_db.list_vehicles(acct)}
    await pg_db.deactivate_vehicle(acct, rows["F6"].id)          # a person
    await pg_db._db.execute(                                      # the sweep
        "UPDATE vehicles SET is_active = 0 WHERE id = ?", (rows["G7"].id,))
    await pg_db._db.commit()

    assert await pg_db.archived_refs(acct) == {"ref-f", "ref-g"}
    assert await pg_db.operator_archived_refs(acct) == {"ref-f"}


@pytest.mark.asyncio
async def test_archiving_closes_alerts_already_on_the_board(pg_db):
    """Stopping new alerts says nothing about the open ones.

    `critical_reescalate` re-notifies unacknowledged rows straight out
    of alert_history, which never joins the registry — so a fault
    raised before a truck was archived kept paging hourly afterwards,
    up to its retry cap.
    """
    acct = 10009005
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "J9", "telematics_ref": "ref-j"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)

    await pg_db._db.execute(
        "INSERT INTO alert_history (account_id, alert_type, vehicle_id, "
        "vehicle_name, last_detail, status, first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (acct, "fault", "ref-j", "J9", "DTC 1234", "active",
         "2026-08-20T00:00:00Z", "2026-08-20T00:00:00Z"),
    )
    await pg_db._db.commit()

    await pg_db.deactivate_vehicle(acct, v.id)

    cur = await pg_db._db.execute(
        "SELECT status FROM alert_history WHERE account_id = ? "
        "AND vehicle_id = ?", (acct, "ref-j"))
    statuses = [r[0] for r in await cur.fetchall()]
    assert statuses == ["cleared"], (
        "an open alert must stop re-escalating when its truck is retired")


@pytest.mark.asyncio
async def test_live_names_are_an_allow_list_not_a_deny_list(pg_db):
    """A door number is reusable, so retiring one must not silence it.

    The surfaces that identify a vehicle by NAME (scorecards) cannot
    filter by excluding archived names: once a retired truck's number
    goes on a different truck, a deny-list would silence the live one
    too.  Keeping only names an ACTIVE row still claims is correct
    whichever way round it happens.
    """
    acct = 10009006
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "K1", "telematics_ref": "ref-k"},
    ], source="samsara")
    (old,) = await pg_db.list_vehicles(acct)
    await pg_db.deactivate_vehicle(acct, old.id)

    # The number goes on a different truck, in another company.
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "OSY", "unit_number": "K1", "telematics_ref": "ref-k2"},
    ], source="samsara")

    live = await pg_db.active_unit_names(acct)
    assert "k1" in live, (
        "the live truck that inherited the number must keep alerting")


@pytest.mark.asyncio
async def test_restore_puts_the_truck_back_the_way_it_was(pg_db):
    """One act, because archiving destroyed nothing.

    The telematics ref was never cleared, so restoring re-links nothing
    — the ingest gate simply stops dropping this truck's rows.  And the
    status comes back as it WAS: a truck retired out of the shop
    returns to the shop, not to a guessed 'active'.
    """
    acct = 10009007
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "L2", "telematics_ref": "ref-l"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db._db.execute(
        "UPDATE vehicles SET status = 'shop' WHERE id = ?", (v.id,))
    await pg_db._db.commit()

    await pg_db.deactivate_vehicle(acct, v.id, actor_user_id=7)
    assert await pg_db.list_vehicles(acct) == []
    assert {r.unit_number for r in await pg_db.list_archived_vehicles(acct)} \
        == {"L2"}
    # While archived it is gated out of the ingest.
    assert await pg_db.operator_archived_refs(acct) == {"ref-l"}

    assert await pg_db.restore_vehicle(acct, v.id, actor_user_id=7) is True
    (back,) = await pg_db.list_vehicles(acct)
    assert back.status == "shop", "restored to a guess instead of its status"
    assert back.archived_reason == "" and back.status_before_archive == ""
    assert back.telematics_ref == "ref-l"
    # And the gate lets it through again — telemetry resumes with no
    # re-linking.
    assert await pg_db.operator_archived_refs(acct) == set()
    assert await pg_db.list_archived_vehicles(acct) == []


@pytest.mark.asyncio
async def test_restore_falls_back_honestly_when_the_status_was_never_kept(pg_db):
    """Rows archived before status_before_archive existed have no
    recorded previous status.  'active' is the honest fallback — we do
    not know, so we do not invent something specific like 'shop'."""
    acct = 10009008
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "M3", "telematics_ref": "ref-m"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    # An older archive: flag and status set, nothing preserved.
    await pg_db._db.execute(
        "UPDATE vehicles SET is_active = 0, status = 'inactive', "
        "archived_reason = 'operator', status_before_archive = '' "
        "WHERE id = ?", (v.id,))
    await pg_db._db.commit()

    assert await pg_db.restore_vehicle(acct, v.id) is True
    (back,) = await pg_db.list_vehicles(acct)
    assert back.status == "active"


@pytest.mark.asyncio
async def test_restoring_a_live_truck_does_nothing(pg_db):
    """Guarded by `is_active = 0` in the UPDATE, so a double-click or a
    stale page cannot rewrite a working truck's status."""
    acct = 10009009
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "N4", "telematics_ref": "ref-n"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db._db.execute(
        "UPDATE vehicles SET status = 'in_transit' WHERE id = ?", (v.id,))
    await pg_db._db.commit()

    assert await pg_db.restore_vehicle(acct, v.id) is False
    (still,) = await pg_db.list_vehicles(acct)
    assert still.status == "in_transit"


@pytest.mark.asyncio
async def test_archived_rows_speak_the_grids_column_names(pg_db, monkeypatch):
    """The Archived tab renders in the SAME grid as live trucks.

    So its rows have to answer to that grid's column keys — `name` and
    `company`, not the registry's `unit_number` and `company_code`.
    Shipped once with the manage-dialog shape instead: every row drew a
    blank Vehicle and a dash for Company, eight identical empty lines.

    `registry_id` is the sharper one: the row menu keys Restore off it,
    so without it the action never appears and the tab is a dead end
    you cannot act on.
    """
    from features.vehicles import router as vr

    acct = 10009010
    await pg_db.upsert_from_integration(acct, [
        {"company_code": "PTG", "unit_number": "P7", "telematics_ref": "ref-p",
         "vehicle_type": "truck"},
    ], source="samsara")
    (v,) = await pg_db.list_vehicles(acct)
    await pg_db.deactivate_vehicle(acct, v.id)

    async def _fake_tenant(_account_id):
        return pg_db
    monkeypatch.setattr(vr, "_get_tenant_db", _fake_tenant)

    out = await vr.list_archived_vehicles(user={"account_id": acct})
    assert out["count"] == 1
    row = out["vehicles"][0]

    # The keys the grid's columns actually read.
    assert row["name"] == "P7"
    assert row["company"] == "PTG"
    assert row["vehicle_type"] == "truck"
    # Without this the Restore action is never offered.
    assert row["registry_id"] == v.id
    # And why it left, which the reader needs to tell "someone decided"
    # from "its gateway went silent".
    assert row["archived_reason"] == "operator"
