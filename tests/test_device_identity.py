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
