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
                     "source_ts": "2026-08-07T09:59:00Z"}}
    assert _detect_identity_events(FLEET, IDENT, prev,
                                   {"ref1": 567782.0}, NOW) == []


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
                     "source_ts": "2026-08-07T09:59:00Z"}}
    ev = _detect_identity_events(FLEET, IDENT, prev,
                                 {"ref1": 904200.0}, NOW)
    assert [e["kind"] for e in ev] == ["odo_rebase"]

    prev_gap = {"ref1": {"odometer_mi": 567781.0,
                         "source_ts": "2026-08-07T07:00:00Z"}}
    assert _detect_identity_events(FLEET, IDENT, prev_gap,
                                   {"ref1": 567981.0}, NOW) == []


@pytest.mark.asyncio
async def test_event_log_dedupes_exact_transitions(pg_db):
    e = {"registry_id": 37, "vehicle_id": "ref1", "vehicle_name": "128",
         "kind": "odo_rebase", "old_value": "567781",
         "new_value": "904200", "observed_at": NOW}
    await pg_db.record_device_events(1, [e])
    await pg_db.record_device_events(1, [e])          # same transition
    rows = await pg_db.get_device_events(1)
    assert len(rows) == 1 and rows[0]["kind"] == "odo_rebase"
