""""No electronic logging device is connected" — said to an account
looking at five green keys.

`connected` meant "at least one reading has been ingested". So an
account that had just wired its first ELD — every company key set, every
probe green, the first five-minute poll not yet due — was told its
device was absent.

That is not a cautious answer. It is a false one, on the page whose
entire purpose is not making false statements about hours of service.
The page was built to refuse the mirror-image lie ("an empty table means
nobody is near their limit") and shipped this one facing the other way.

Three states, not two:

    nothing connected        say so, and point at Integrations
    connected, nothing yet   say THAT, and say why it might stay empty
    connected, with rows     the table

And the second has two different causes needing two different
sentences: the feed is young, or another integration is the one being
polled and this one will never fill on its own. Only the first resolves
itself by waiting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.eld.service import get_hours


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _row(**kw):
    base = {
        "provider_id": "orient_eld",
        "provider_driver_id": "19192",
        "user_id": None,
        "duty_status": "driving",
        "drive_remaining_seconds": None,
        "shift_remaining_seconds": None,
        "cycle_remaining_seconds": None,
        "break_in_seconds": None,
        "last_status_change": _iso(30),
        "driver_name": "Provider Jane",
        "source_ts": _iso(2),
        "company_code": "PTG",
        "updated_at": _iso(0),
        "display_name": "",
        "truck_num": "",
        "linked": False,
        "vehicles": [],
    }
    base.update(kw)
    return base


class _DB:
    def __init__(self, rows):
        self._rows = list(rows)

    async def get_driver_hos_live(self, account_id, user_id=None):
        return list(self._rows)

    async def count_driver_hos_live(self, account_id):
        return len(self._rows)


def _feed(serving=None, connected=()):
    names = {"orient_eld": "ORIENT ELD", "samsara": "Samsara"}
    return {
        "serving": serving,
        "serving_name": names.get(serving or "", ""),
        "connected": [{"id": p, "name": names[p]} for p in connected],
        "shadowed": [
            {"id": p, "name": names[p]} for p in connected if p != serving
        ],
    }


# ── The screenshot ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_freshly_connected_eld_is_not_reported_as_absent():
    """Five keys set, every probe green, nothing polled yet."""
    out = await get_hours(
        _DB([]), 1, feed=_feed("orient_eld", ["orient_eld"]))

    assert out["connected"] is True, (
        "an account looking at a connected ELD was told none exists"
    )
    assert out["awaiting_first_reading"] is True
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_nothing_connected_still_says_so():
    """The state the page was built for must survive the fix."""
    out = await get_hours(_DB([]), 1, feed=_feed(None, []))
    assert out["connected"] is False
    assert out["awaiting_first_reading"] is False


@pytest.mark.asyncio
async def test_rows_alone_are_enough_to_be_connected():
    """If we hold readings, something produced them — whatever the
    integrations layer says today. A disconnected-then-reconnected
    account must not lose its last known clocks to a lookup."""
    out = await get_hours(_DB([_row()]), 1, feed=_feed(None, []))
    assert out["connected"] is True
    assert out["awaiting_first_reading"] is False


@pytest.mark.asyncio
async def test_a_failed_lookup_is_not_read_as_nothing_connected():
    """``None`` is a third answer. Treating a failed integrations read
    as "no device" is the same mistake one layer up — and it would
    reintroduce exactly this bug whenever the platform DB hiccups."""
    out = await get_hours(_DB([_row()]), 1, feed=None)
    assert out["connected"] is True
    assert out["awaiting_first_reading"] is False


# ── The quiet cause ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_shadowed_integration_is_named():
    """Two connected ELDs, one polled. The loser is never asked, and
    that fact otherwise lives in a single server log line while the
    operator stares at an empty page."""
    out = await get_hours(
        _DB([]), 1, feed=_feed("samsara", ["samsara", "orient_eld"]))

    assert out["connected"] is True
    assert out["awaiting_first_reading"] is True
    assert out["feed"]["serving_name"] == "Samsara"
    assert [p["name"] for p in out["feed"]["shadowed"]] == ["ORIENT ELD"]


@pytest.mark.asyncio
async def test_no_contention_leaves_nothing_shadowed():
    out = await get_hours(
        _DB([]), 1, feed=_feed("orient_eld", ["orient_eld"]))
    assert out["feed"]["shadowed"] == []


@pytest.mark.asyncio
async def test_a_filled_table_can_still_be_the_wrong_device():
    """The quietest version: rows arrive from the integration that WAS
    polled, everything looks healthy, and the ELD the operator just
    wired is doing nothing. The answer still names it."""
    out = await get_hours(
        _DB([_row(provider_id="samsara")]), 1,
        feed=_feed("samsara", ["samsara", "orient_eld"]))

    assert out["connected"] is True
    assert out["awaiting_first_reading"] is False
    assert [p["name"] for p in out["feed"]["shadowed"]] == ["ORIENT ELD"]


@pytest.mark.asyncio
async def test_the_caller_can_hand_the_feed_in():
    """``get_hours`` stays testable without a platform database, and a
    caller that already looked it up does not pay for a second read."""
    sentinel = _feed("orient_eld", ["orient_eld"])
    out = await get_hours(_DB([]), 1, feed=sentinel)
    assert out["feed"] is sentinel


# ── The lookup itself ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_feed_status_actually_runs(monkeypatch):
    """Every test above hands ``feed`` in, so none of them ever entered
    ``feed_status``.  It shipped with an unimported name, and its own
    ``except Exception`` turned that NameError into ``None`` — the
    function returned "unknown" on every call, in production, while the
    suite stayed green.  The commit guard caught it; this test is what
    should have.
    """
    from types import SimpleNamespace

    import features.eld.service as service

    class _PDB:
        async def get_account_integration(self, account_id, provider_id):
            if provider_id in ("samsara", "orient_eld"):
                return SimpleNamespace(status="connected", feature_toggles={})
            return None

    import infra.platform as platform
    monkeypatch.setattr(platform, "get_platform_db", lambda: _PDB())

    got = await service.feed_status(1)

    assert got is not None, (
        "feed_status returned unknown on a healthy lookup — something "
        "inside it raised and the except swallowed it"
    )
    assert {p["id"] for p in got["connected"]} == {"samsara", "orient_eld"}
    assert got["serving"] == "samsara", "catalog order decides"
    assert [p["id"] for p in got["shadowed"]] == ["orient_eld"]
    assert got["serving_name"] == "Samsara"


@pytest.mark.asyncio
async def test_a_programming_error_is_not_reported_as_unknown(monkeypatch):
    """The failure mode that hid the bug.  A broken import or a typo is
    OUR bug, not an unreachable integrations layer, and answering
    "unknown" forever is how it survives a full test run."""
    import features.eld.service as service

    def _boom():
        raise NameError("name 'Capability' is not defined")

    import infra.platform as platform
    monkeypatch.setattr(platform, "get_platform_db", _boom)

    with pytest.raises(NameError):
        await service.feed_status(1)


@pytest.mark.asyncio
async def test_a_real_outage_is_still_unknown(monkeypatch):
    """The platform DB being down is not a programming error, and the
    page must get its third answer rather than a 500."""
    import features.eld.service as service

    class _Down:
        async def get_account_integration(self, account_id, provider_id):
            raise RuntimeError("connection pool exhausted")

    import infra.platform as platform
    monkeypatch.setattr(platform, "get_platform_db", lambda: _Down())

    assert await service.feed_status(1) is None
