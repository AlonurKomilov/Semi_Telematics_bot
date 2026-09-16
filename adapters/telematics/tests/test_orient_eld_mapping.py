"""Where ORIENT ELD's spelling stops.

Unlike the Samsara mapping tests next door, these fixtures are not
guesses: every shape here was copied from a live ``/api/logs/tracking``
response on a real account (22 drivers, 2026-09-15), with the driver
PII replaced.  The vendor quirks pinned below are the ones that were
observed, not the ones that seemed likely.

The one that matters most is the timestamp: ORIENT emits
``status_activation_time_utc`` WITHOUT a zone suffix while
``datetime_utc`` in the same object carries a ``Z``.  Both are UTC.
Reading the first as naive local time moves a duty status by the
reader's offset — four hours on the account this was verified against,
in the direction that makes a driver look like they started their shift
later than they did.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from adapters.telematics.orient_eld.client import (
    MultiCompanyOrientClient,
    USER_AGENT,
    OrientEldClient,
    OrientEldRateGate,
    _safe_detail,
    build_multi_company_orient_client,
    utc_iso,
)
from adapters.telematics.orient_eld.provider import (
    OrientEldProvider,
    _duty_status,
    to_snapshot,
)
from adapters.telematics.protocol import (
    Capability, DutyStatus, HosClock, TelematicsProvider,
)


def _row(**kw):
    """One live tracking row, PII replaced."""
    base = {
        "location": "12mi N from Martinsburg, WV",
        "location_latitude": 39.62866973876953,
        "location_longitude": -78.01799774169922,
        "name": "Jane",
        "surname": "Ruiz",
        "driver_id": 19192,
        "odometer_value": 444865,
        "engine_hours": 12190.0,
        "speed": 0.0,
        "status": "OFF_DUTY",
        "status_duration": 496.443332,
        "username": "jruiz",
        "vehicle_id": 14518,
        "vehicle_number": "001",
        "datetime": "2026-09-15T01:19:42",
        "datetime_utc": "2026-09-15T05:18:18Z",
        # No zone suffix — the quirk this file exists for.
        "status_activation_time": "2026-09-15T01:19:42",
        "status_activation_time_utc": "2026-09-15T05:19:42",
        "location_datetime_utc": "2026-09-15T05:18:18Z",
        "company_timezone": "America/New_York",
        "ignition_status": "on",
        "metric_units": "miles",
    }
    base.update(kw)
    return base


class _Client:
    """Stands in for the fan-out; the provider only calls one method."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.asked = 0

    async def get_tracking(self):
        self.asked += 1
        return list(self._rows)

    async def close(self):
        return None


# ── The vocabulary boundary ───────────────────────────────────────

@pytest.mark.parametrize("vendor,ours", [
    # Observed live.
    ("DRIVING",  DutyStatus.DRIVING),
    ("SLEEPER",  DutyStatus.SLEEPER),
    ("OFF_DUTY", DutyStatus.OFF_DUTY),
    ("ON_DUTY",  DutyStatus.ON_DUTY),
    # FMCSA statuses nobody happened to be in when the account was
    # sampled.  Mapped anyway: the day one appears is not the day to
    # discover it reads as UNKNOWN.
    ("PERSONAL_CONVEYANCE", DutyStatus.PERSONAL_CONVEYANCE),
    ("YARD_MOVE",           DutyStatus.YARD_MOVE),
])
def test_every_vendor_spelling_maps_to_one_of_ours(vendor, ours):
    assert _duty_status(vendor) == ours


@pytest.mark.parametrize("spelling", [
    "off_duty", "OFF-DUTY", "offduty", "Off Duty", "  OFF_DUTY  ",
])
def test_a_rename_that_only_changes_punctuation_costs_nothing(spelling):
    assert _duty_status(spelling) == DutyStatus.OFF_DUTY


@pytest.mark.parametrize("raw", ["", None, "ASLEEP", "duty", 17])
def test_an_unmapped_status_becomes_unknown_and_never_off_duty(raw):
    """The direction of this failure is the whole point.  Resolving an
    unknown spelling to off-duty would be indistinguishable from the
    device reporting a driver at rest."""
    assert _duty_status(raw) == DutyStatus.UNKNOWN


def test_no_vendor_spelling_survives_into_a_snapshot():
    snap = to_snapshot(_row(status="DRIVING"))
    assert snap.duty_status in DutyStatus.ALL
    assert snap.duty_status == "driving"


# ── The timestamp quirk ───────────────────────────────────────────

def test_a_utc_field_with_no_zone_suffix_is_still_read_as_utc():
    """The bug this guards: parsed as naive, this row's duty status
    would move by the reader's offset."""
    assert utc_iso("2026-09-15T05:19:42") == "2026-09-15T05:19:42+00:00"


def test_a_utc_field_that_does_say_z_agrees_with_one_that_does_not():
    assert utc_iso("2026-09-15T05:19:42Z") == utc_iso("2026-09-15T05:19:42")


@pytest.mark.parametrize("raw", ["", None, "junk", "not-a-date", 0])
def test_an_unreadable_timestamp_is_blank_not_a_guess(raw):
    """Blank is what every surface already reads as "unknown age".
    Substituting now() would call a reading fresh that we cannot date."""
    assert utc_iso(raw) == ""


def test_the_reading_age_comes_from_the_telemetry_not_the_status_clock():
    """``datetime_utc`` is NOT the UTC rendering of ``datetime`` — it is
    the telemetry sample time, and it is what decides staleness.  On the
    live row these differed by 84 seconds."""
    snap = to_snapshot(_row())
    assert snap.source_ts == "2026-09-15T05:18:18+00:00"
    assert snap.last_status_change == "2026-09-15T05:19:42+00:00"


def test_the_age_falls_back_when_the_telemetry_stamp_is_missing():
    snap = to_snapshot(_row(location_datetime_utc=None))
    assert snap.source_ts == "2026-09-15T05:18:18+00:00"


def test_no_timestamp_at_all_is_blank_rather_than_our_fetch_time():
    snap = to_snapshot(_row(location_datetime_utc=None, datetime_utc=""))
    assert snap.source_ts == ""


def test_the_derived_duration_is_not_stored_anywhere():
    """``status_duration`` is the server's own clock minus the
    activation time — across a whole live page every row's sum landed on
    the same instant.  Storing it would bake OUR fetch time into a
    column that reads like the vendor's."""
    snap = to_snapshot(_row(status_duration=99999.0))
    assert "99999" not in repr(snap)


# ── What the vendor does not report ───────────────────────────────

def test_every_clock_is_none_and_that_is_the_declaration():
    snap = to_snapshot(_row())
    for field in HosClock.FIELDS.values():
        assert getattr(snap, field) is None


def test_the_provider_declares_exactly_what_it_fills():
    assert OrientEldProvider.hos_clocks_reported == frozenset()


def test_none_is_not_zero_anywhere_in_the_snapshot():
    """A zero would mean the driver must stop.  None means we were not
    told.  A vendor that reports neither must produce the second."""
    snap = to_snapshot(_row())
    assert 0 not in (
        snap.drive_remaining_seconds, snap.shift_remaining_seconds,
        snap.cycle_remaining_seconds, snap.break_in_seconds,
    )


# ── Rows the store could never key ────────────────────────────────

@pytest.mark.parametrize("bad", [None, ""])
def test_a_row_with_no_driver_id_is_dropped_not_invented(bad):
    assert to_snapshot(_row(driver_id=bad)) is None


@pytest.mark.asyncio
async def test_the_provider_drops_unkeyable_rows_and_keeps_the_rest():
    provider = OrientEldProvider(_Client([_row(driver_id=None), _row()]))
    snaps = await provider.get_driver_hos()
    assert [s.provider_driver_id for s in snaps] == ["19192"]


@pytest.mark.asyncio
async def test_no_drivers_is_an_empty_list_not_an_error():
    assert await OrientEldProvider(_Client([])).get_driver_hos() == []


def test_the_vendor_name_is_assembled_from_both_halves():
    """Display prefers OUR roster name; this is what an operator sees
    for a driver nobody has linked yet, so a first name alone would not
    identify anyone."""
    assert to_snapshot(_row()).driver_name == "Jane Ruiz"
    assert to_snapshot(_row(name="", surname="")).driver_name == "jruiz"


# ── The fan-out ───────────────────────────────────────────────────

def test_a_single_company_account_need_not_invent_a_company_code():
    client = build_multi_company_orient_client({"api_key": "k"})
    assert client.company_codes == ["default"]


def test_a_company_with_no_key_is_skipped_rather_than_built_empty():
    """An empty key 401s on every call and spends the account's budget
    to learn nothing."""
    client = build_multi_company_orient_client(
        {"companies": {"A": "ka", "B": "", "C": None}})
    assert client.company_codes == ["A"]


def test_no_credentials_at_all_builds_an_empty_client_not_a_crash():
    assert len(build_multi_company_orient_client({})) == 0


def test_a_blank_key_is_refused_at_the_client_itself():
    with pytest.raises(ValueError):
        OrientEldClient("   ")


class _FanClient:
    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom

    async def get_tracking(self):
        if self._boom:
            raise RuntimeError("expired key")
        return [dict(r) for r in self._rows]

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_each_row_is_tagged_with_the_company_it_came_from():
    mc = MultiCompanyOrientClient({
        "A": _FanClient([_row(driver_id=1)]),
        "B": _FanClient([_row(driver_id=2)]),
    })
    rows = await mc.get_tracking()
    assert {r["_company_code"] for r in rows} == {"A", "B"}


@pytest.mark.asyncio
async def test_one_companys_expired_key_does_not_empty_the_account():
    """The failure this prevents: a single dead key making an entire
    fleet look like it is off duty."""
    mc = MultiCompanyOrientClient({
        "GOOD": _FanClient([_row(driver_id=1)]),
        "DEAD": _FanClient([], boom=True),
    })
    rows = await mc.get_tracking()
    assert [r["driver_id"] for r in rows] == [1]


@pytest.mark.asyncio
async def test_a_partial_connect_stays_reachable_and_names_the_hole():
    """This drives ``account_integrations.status``, and a non-connected
    row makes the resolver skip the account ENTIRELY.

    "All five must pass" therefore meant one expired key stopped hours
    of service for all five companies — undoing one layer up the
    containment ``_fan_out`` provides, where a dead key costs only its
    own drivers. It happened on a live account: a 12-second health-check
    timeout flipped the row to error and the feed stopped writing.

    So: reachable if ANY key works, and the message names the ones that
    do not. The per-company rows already carry the precise signal."""
    class _Probe:
        def __init__(self, ok):
            self._ok = ok

        async def test_connection(self):
            return (self._ok,
                    "connected to X" if self._ok else "API key rejected",
                    {"dot_number": "1"} if self._ok else None)

        async def close(self):
            return None

    mc = MultiCompanyOrientClient({"A": _Probe(True), "B": _Probe(False)})
    ok, message, _ = await mc.test_connection()
    assert ok is True, (
        "one failing key stopped the four that work — the resolver "
        "skips a non-connected row for the whole account"
    )
    assert "B" in message and "1 of 2" in message


@pytest.mark.asyncio
async def test_no_keys_at_all_is_not_a_green_connection():
    ok, message, _ = await MultiCompanyOrientClient({}).test_connection()
    assert ok is False
    assert "no ORIENT ELD API key" in message


# ── The rate gate ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_tracking_budget_is_enforced_per_group():
    """One call per six seconds is the vendor's published tracking
    budget; the directory budget is ninety per minute.  One shared
    counter would either throttle the cheap reads or overrun the
    expensive one."""
    gate = OrientEldRateGate()
    slept: list[float] = []

    async def _no_sleep(sec):
        slept.append(sec)

    orig = asyncio.sleep
    asyncio.sleep = _no_sleep  # type: ignore[assignment]
    try:
        await gate.acquire("tracking")
        await gate.acquire("tracking")
        assert slept and slept[0] > 5.0, "the second call was not gated"
        slept.clear()
        for _ in range(10):
            await gate.acquire("directory")
        assert not slept, "the directory budget was throttled needlessly"
    finally:
        asyncio.sleep = orig  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_a_slow_group_does_not_hold_up_a_fast_one():
    """One lock for all three budgets serialised them.

    Measured before the fix: a directory read queued behind a tracking
    read's six-second wait acquired at +6.00s instead of +0.05s, because
    the single lock was held across the sleep.  The budgets are
    independent upstream and must be independent here — a five-minute
    ingest that also wants the roster should not pay the tracking
    budget twice.
    """
    gate = OrientEldRateGate()
    real_sleep = asyncio.sleep
    order: list[str] = []

    async def _quick(_sec):
        # Stands in for the real six-second wait, short enough to test.
        await real_sleep(0.05)

    await gate.acquire("tracking")          # tracking budget now full

    async def _tracking():
        await gate.acquire("tracking")      # must wait
        order.append("tracking")

    async def _directory():
        await real_sleep(0.005)             # start just after
        await gate.acquire("directory")     # must NOT wait
        order.append("directory")

    asyncio.sleep = _quick                  # type: ignore[assignment]
    try:
        await asyncio.gather(_tracking(), _directory())
    finally:
        asyncio.sleep = real_sleep          # type: ignore[assignment]

    assert order == ["directory", "tracking"], (
        "the directory read waited behind the tracking budget — the "
        "groups are sharing one lock again"
    )


def test_each_budget_has_its_own_lock():
    gate = OrientEldRateGate()
    assert gate._locks["tracking"] is not gate._locks["directory"]
    assert gate._locks["locations"] is not gate._locks["tracking"]


# ── What an error message may repeat ──────────────────────────────

@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_failure_never_repeats_the_upstream_body(status):
    """These messages reach the application log.  A 401 body is the one
    shape that can echo the credential back, and the status code already
    says everything an operator needs: the key is wrong."""
    detail = _safe_detail(status, {"detail": "invalid api key sk_live_abc123"})
    assert "sk_live_abc123" not in detail
    assert "authentication rejected" in detail


@pytest.mark.parametrize("status", [422, 500, 503])
def test_other_failures_keep_the_detail_that_makes_them_debuggable(status):
    """A 422 names the parameter it disliked.  Throwing that away to be
    safe from a risk that only exists on auth failures would make every
    other failure a guessing game."""
    assert "page" in _safe_detail(status, {"detail": "bad page value"})


def test_an_endpoint_this_map_has_not_heard_of_takes_the_tightest_budget():
    """Guessing generous on an unknown limit is how a key gets rate
    limited for every company sharing it."""
    from adapters.telematics.orient_eld.client import _group_for
    assert _group_for("/api/something/new") == "tracking"
    assert _group_for("/api/logs/tracking") == "tracking"
    assert _group_for("/api/drivers") == "directory"
    assert _group_for("/api/vehicles") == "directory"
    assert _group_for("/api/vehicles/locations") == "locations"


# ── Shape ─────────────────────────────────────────────────────────

def test_the_provider_satisfies_the_protocol():
    assert isinstance(OrientEldProvider(_Client([])), TelematicsProvider)


# A capability may appear here ONLY with the reason it has no FeedSpec.
# A feed row reports COUNT(*) and MAX(ts_col) for one table, so a feed
# whose table cannot answer that question is better off not claiming a
# row than claiming one that is permanently wrong.
_FEEDLESS = {
    Capability.DRIVER_SPEC: "users has no ingest timestamp column",
}


def test_the_capability_is_declared_with_a_feed_behind_it():
    """A declared capability renders a toggle; a toggle with no feed
    behind it controls nothing — unless the feed row itself would lie,
    which is a decision that has to be written down."""
    from adapters.telematics.catalog import PROVIDER_CATALOG

    entry = PROVIDER_CATALOG["orient_eld"]

    # Every claim lands together — the id, the provider's declaration,
    # the toggle default and a feed that fills a table.
    assert entry.capabilities == OrientEldProvider.supported_capabilities
    assert entry.capabilities == {
        Capability.DRIVER_HOS,
        # A SECOND OPINION on trucks another integration registered.
        # Not VEHICLE_STATE: this is what a truck IS, not where it is,
        # and several providers can each half-know it — which is what
        # capabilities/source arbitrates.
        Capability.VEHICLE_SPEC,
        # The same second opinion about PEOPLE — the name, phone and
        # licence on file for a driver an admin has linked by hand.
        Capability.DRIVER_SPEC,
    }
    fed = {f.capability for f in entry.feeds}
    unexplained = entry.capabilities - fed - set(_FEEDLESS)
    assert not unexplained, (
        f"capabilities with no feed and no reason: {sorted(unexplained)}"
    )
    # And the other direction: a reason that no longer applies is a
    # stale excuse, so it has to be deleted when the feed arrives.
    assert not (set(_FEEDLESS) & fed), (
        "a capability is listed as feedless AND has a feed: "
        f"{sorted(set(_FEEDLESS) & fed)}"
    )
    for cap in entry.capabilities:
        assert entry.feature_defaults[cap]["enabled"] is True, cap


def test_the_client_introduces_itself():
    """aiohttp's default agent names Python, not us.

    ORIENT does not enforce a User-Agent today — but this key is a live
    production credential against a rate-limited API, and when a call of
    ours misbehaves the vendor's logs should name the product doing it.
    The live-map session lost a whole POI feed to exactly this: a
    volunteer mirror started answering the default agent with HTTP 406
    before reading the request, and the client reported it as "the
    source is not answering".
    """
    assert "4truck" in USER_AGENT
    assert "aiohttp" not in USER_AGENT.lower()
    assert "python" not in USER_AGENT.lower()


@pytest.mark.asyncio
async def test_every_request_carries_it():
    """Set on the SESSION, so it rides every call rather than the one
    someone remembered to decorate."""
    client = OrientEldClient("k", company_code="X")
    try:
        session = await client._ensure_session()
        assert session.headers.get("User-Agent") == USER_AGENT
        assert session.headers.get("x-api-key") == "k"
    finally:
        await client.close()


def test_the_truck_the_device_reported_survives_the_mapping():
    """ORIENT sends it on every row and the first version dropped it,
    so sixty-nine drivers showed sixty-nine dashes in the Truck column
    while the answer was in the payload all along."""
    assert to_snapshot(_row()).provider_vehicle == "001"


@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("", ""), ("  ", ""), (" 6729 ", "6729"), (243, "243"),
])
def test_a_missing_or_padded_unit_number_is_normalised(raw, expected):
    """Blank must stay blank: a surface falls back to our roster on an
    empty string, and whitespace masquerading as a truck would stop
    that fallback while displaying nothing."""
    assert to_snapshot(_row(vehicle_number=raw)).provider_vehicle == expected


@pytest.mark.asyncio
async def test_every_key_failing_is_still_a_failure():
    """"Any" is not "never red". Nothing reachable is nothing."""
    class _Probe:
        async def test_connection(self):
            return False, "API key rejected", None

        async def close(self):
            return None

    mc = MultiCompanyOrientClient({"A": _Probe(), "B": _Probe()})
    ok, message, _ = await mc.test_connection()
    assert ok is False
    assert "A" in message and "B" in message


@pytest.mark.asyncio
async def test_one_hung_company_cannot_spend_the_callers_budget():
    """The health check allows 12s for everything. A probe killed from
    outside reports a bare "timeout after 12 s" that names nobody — so
    each company is probed under its own clock and the failure names
    the company."""
    import adapters.telematics.orient_eld.client as mod

    class _Hangs:
        async def test_connection(self):
            await asyncio.sleep(3600)

        async def close(self):
            return None

    class _Works:
        async def test_connection(self):
            return True, "connected to X", {"dot_number": "1"}

        async def close(self):
            return None

    real = mod.PROBE_TIMEOUT_SEC
    mod.PROBE_TIMEOUT_SEC = 0.05
    try:
        mc = MultiCompanyOrientClient({"SLOW": _Hangs(), "OK": _Works()})
        ok, message, _ = await asyncio.wait_for(
            mc.test_connection(), timeout=5)
    finally:
        mod.PROBE_TIMEOUT_SEC = real

    assert ok is True
    assert "SLOW" in message, "the failure did not name the company"


def test_the_clients_timeout_is_under_its_callers_budget():
    """A client whose timeout exceeds its caller's can never report its
    own failure — the caller kills it first and records a generic one.
    The health check's budget is 12s; both of ours must fit inside."""
    from adapters.telematics.orient_eld.client import (
        PROBE_TIMEOUT_SEC, REQUEST_TIMEOUT_SEC,
    )
    from capabilities.integrations.telematics_health import (
        _HEALTH_CHECK_TIMEOUT_SEC,
    )

    assert REQUEST_TIMEOUT_SEC < _HEALTH_CHECK_TIMEOUT_SEC
    assert PROBE_TIMEOUT_SEC < _HEALTH_CHECK_TIMEOUT_SEC


# ── The identity feed ─────────────────────────────────────────────

class _RosterClient:
    """Stands in for the fan-out on the roster read."""

    def __init__(self, rows):
        self._rows = list(rows)

    async def get_drivers(self):
        return list(self._rows)

    async def get_tracking(self):
        return []

    async def close(self):
        return None


def _roster(driver_id, *, name="John", surname="Smith", phone="+15551111",
            dl="D123456", state="CA", email="j@example.com", company=""):
    return {
        "driver_id": driver_id, "name": name, "surname": surname,
        "phone": phone, "dl_number": dl, "license_state": state,
        "email": email, "_company_code": company,
    }


@pytest.mark.asyncio
async def test_the_roster_maps_to_the_identity_shape():
    p = OrientEldProvider(_RosterClient([_roster(17)]))
    rows = await p.get_driver_spec()

    assert rows == [{
        "provider_driver_id": "17",
        "display_name": "John Smith",
        "phone": "+15551111",
        "cdl_number": "D123456",
        "cdl_state": "CA",
        "company_code": "",
    }]


@pytest.mark.asyncio
async def test_the_email_is_read_and_dropped():
    """It is a login identity here — Google sign-in links on it and
    verification mail goes to it — not a profile field a vendor roster
    may write. The vendor sends it; we do not carry it."""
    p = OrientEldProvider(_RosterClient([_roster(17, email="x@y.com")]))
    rows = await p.get_driver_spec()

    assert "email" not in rows[0]
    assert "x@y.com" not in str(rows[0])


@pytest.mark.asyncio
async def test_a_row_with_no_driver_id_is_skipped():
    """No key means no link means nothing to fill; inventing one would
    attach a licence number to a person chosen at random."""
    p = OrientEldProvider(_RosterClient([
        _roster(None), _roster(""), _roster(17),
    ]))
    rows = await p.get_driver_spec()

    assert [r["provider_driver_id"] for r in rows] == ["17"]


@pytest.mark.asyncio
async def test_an_id_two_companies_claim_is_dropped_from_identity():
    """The tripwire both feeds share, and the asymmetry between them.

    Hours of service keeps both drivers under distinct keys — a wrong
    row there is a duty status on the wrong line. Identity drops the id
    entirely, because a wrong row there writes one person's licence
    number onto another person's record, encrypted, where nobody will
    look at it again.
    """
    p = OrientEldProvider(_RosterClient([
        _roster(17, company="AAA", dl="AAA-1"),
        _roster(17, company="BBB", dl="BBB-1"),
        _roster(18, company="AAA"),
    ]))
    rows = await p.get_driver_spec()

    assert [r["provider_driver_id"] for r in rows] == ["18"]


@pytest.mark.asyncio
async def test_one_company_listing_a_driver_twice_is_one_driver():
    p = OrientEldProvider(_RosterClient([
        _roster(17, company="AAA"), _roster(17, company="AAA"),
    ]))
    rows = await p.get_driver_spec()

    assert len(rows) == 1


@pytest.mark.asyncio
async def test_both_feeds_spell_a_driver_id_the_same_way():
    """The link an admin makes is made against the id the HOURS surface
    showed them. If the identity feed spelled the same driver's id
    differently, the link would match nothing — and it would fail in
    silence, as a driver who simply never gets filled.

    So they are checked against each other, on the same driver, through
    the one detector both of them use.
    """
    tracking = _row(driver_id=17, _company_code="AAA")
    roster = _roster(17, company="AAA")

    class _Both:
        async def get_tracking(self):
            return [tracking]

        async def get_drivers(self):
            return [roster]

        async def close(self):
            return None

    p = OrientEldProvider(_Both())
    hos = await p.get_driver_hos()
    spec = await p.get_driver_spec()

    assert hos[0].provider_driver_id == spec[0]["provider_driver_id"]
