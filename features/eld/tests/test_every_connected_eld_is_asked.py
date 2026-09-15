"""Two ELDs on one account, and both of them get asked.

The account this was written against had ORIENT ELD connected with five
company keys, every probe green — and an empty Hours of Service page.
The reason was a single line: the resolver answers "which ONE provider
serves this capability", takes the first in catalog order, and a
telematics integration declared earlier was the only one being polled.
ORIENT was never called. Nothing errored; the fact lived in one log
line.

That single-winner rule is RIGHT for a capability whose rows are keyed
by the THING. Two providers writing truck 103's live state would take
turns overwriting each other, so one has to win.

Hours of service is not that shape, and the store already knew it:

    PRIMARY KEY (account_id, provider_id, provider_driver_id)
    ON CONFLICT (...) DO UPDATE          -- per provider
    and nothing, anywhere, deletes

Two ELDs coexist there by construction, and what they produce is not a
merge needing arbitration but a UNION of disjoint driver sets. An
account running five companies on one device and the rest on another
has each driver on exactly one certified ELD. Picking a winner does not
resolve a conflict — it hides half the fleet.

Nor is this ``capabilities/source``'s problem. Source arbitrates
several sources describing the SAME record, field by field, manual
pinned above all. Nothing here describes the same record twice, so
there is no field to arbitrate.

The one genuine exception — one human logged on two devices — is
deliberately NOT merged. Choosing which certified ELD is right about a
driver's hours is the operator's call, not a mirror's.
"""

from __future__ import annotations

import pytest

import features.eld.ingest as ingest
from adapters.telematics.protocol import DutyStatus, HosSnapshot


def _snap(pdid: str, **kw):
    base = dict(
        provider_driver_id=pdid,
        duty_status=DutyStatus.DRIVING,
        last_status_change="2026-09-15T07:00:00+00:00",
        source_ts="2026-09-15T07:30:00+00:00",
        driver_name=f"Driver {pdid}",
    )
    base.update(kw)
    return HosSnapshot(**base)


class _Provider:
    def __init__(self, snaps, boom=False):
        self._snaps, self._boom = snaps, boom
        self.asked = 0

    async def get_driver_hos(self):
        self.asked += 1
        if self._boom:
            raise RuntimeError("upstream 503")
        return list(self._snaps)


class _Store:
    def __init__(self, live=()):
        self.writes: list[dict] = []
        self._live = list(live)

    async def upsert_driver_hos(self, account_id, provider_id, rows, *,
                                links=None):
        self.writes.append({"provider_id": provider_id, "rows": rows})
        return len(rows)

    async def list_account_users(self, account_id):
        return []

    async def get_driver_hos_live(self, account_id, user_id=None):
        return list(self._live)


@pytest.fixture
def wired(monkeypatch):
    def _install(providers: dict, live=()):
        store = _Store(live)

        async def _resolve(account_id, capability):
            return list(providers)

        async def _tenant(account_id):
            return store

        async def _client(account_id, provider_id, **kw):
            return providers[provider_id]

        monkeypatch.setattr(ingest, "resolve_all_providers_for", _resolve)
        monkeypatch.setattr(ingest, "get_tenant_db", _tenant)
        monkeypatch.setattr(ingest, "get_telematics_client", _client)
        return store
    return _install


# ── The bug ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_connected_elds_are_asked(wired):
    """The one that was failing: a second ELD never called at all."""
    providers = {
        "samsara": _Provider([_snap("s1"), _snap("s2")]),
        "orient_eld": _Provider([_snap("o1"), _snap("o2"), _snap("o3")]),
    }
    store = wired(providers)

    written = await ingest.ingest_driver_hos(7)

    assert providers["orient_eld"].asked == 1, (
        "the second ELD was never polled — the whole bug"
    )
    assert providers["samsara"].asked == 1
    assert written == 5


@pytest.mark.asyncio
async def test_each_providers_rows_are_written_under_its_own_id(wired):
    """The store keys on the provider, so the two sets never collide —
    and each row can say which device reported it."""
    store = wired({
        "samsara": _Provider([_snap("s1")]),
        "orient_eld": _Provider([_snap("o1")]),
    })
    await ingest.ingest_driver_hos(7)

    by_provider = {w["provider_id"]: w["rows"] for w in store.writes}
    assert set(by_provider) == {"samsara", "orient_eld"}
    assert [r["provider_driver_id"] for r in by_provider["samsara"]] == ["s1"]
    assert [r["provider_driver_id"] for r in by_provider["orient_eld"]] == ["o1"]


@pytest.mark.asyncio
async def test_one_eld_failing_costs_only_its_own_drivers(wired):
    """An account cannot be made to go dark by whichever device happens
    to fail first."""
    providers = {
        "samsara": _Provider([], boom=True),
        "orient_eld": _Provider([_snap("o1"), _snap("o2")]),
    }
    store = wired(providers)

    written = await ingest.ingest_driver_hos(7)

    assert written == 2
    assert [w["provider_id"] for w in store.writes] == ["orient_eld"]


@pytest.mark.asyncio
async def test_one_eld_reporting_nothing_does_not_clear_the_other(wired):
    """An empty answer is not a wipe — for either provider."""
    store = wired({
        "samsara": _Provider([]),
        "orient_eld": _Provider([_snap("o1")]),
    })
    assert await ingest.ingest_driver_hos(7) == 1
    assert [w["provider_id"] for w in store.writes] == ["orient_eld"]


@pytest.mark.asyncio
async def test_no_connected_eld_is_still_zero_and_no_raise(wired):
    store = wired({})
    assert await ingest.ingest_driver_hos(7) == 0
    assert store.writes == []


@pytest.mark.asyncio
async def test_one_eld_behaves_exactly_as_before(wired):
    """The common case must not pay for the uncommon one."""
    providers = {"orient_eld": _Provider([_snap("o1"), _snap("o2")])}
    store = wired(providers)
    assert await ingest.ingest_driver_hos(7) == 2
    assert len(store.writes) == 1


# ── The case that IS a conflict ───────────────────────────────────

@pytest.mark.asyncio
async def test_one_human_on_two_devices_is_reported_not_merged(wired, caplog):
    """Mid-migration a driver can be logged on both. Two certified
    devices are then each authoritative about the same person's hours,
    and choosing between them is a compliance judgement that belongs to
    the operator — so both readings stay and the ambiguity is said out
    loud rather than met as a duplicated name on a page."""
    import logging

    live = [
        {"user_id": 42, "provider_id": "samsara"},
        {"user_id": 42, "provider_id": "orient_eld"},
        {"user_id": 43, "provider_id": "orient_eld"},
    ]
    wired({
        "samsara": _Provider([_snap("s1")]),
        "orient_eld": _Provider([_snap("o1")]),
    }, live=live)

    with caplog.at_level(logging.WARNING):
        await ingest.ingest_driver_hos(7)

    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "MORE THAN ONE ELD" in msg
    assert "user 42" in msg
    assert "user 43" not in msg, "a driver on one device is not a conflict"


@pytest.mark.asyncio
async def test_a_single_provider_never_runs_the_conflict_check(wired):
    """One ELD cannot disagree with itself, and the check costs a read
    of the whole table — it must not run on the common path."""
    class _Counting(_Store):
        reads = 0

        async def get_driver_hos_live(self, account_id, user_id=None):
            type(self).reads += 1
            return []

    store = wired({"orient_eld": _Provider([_snap("o1")])})
    _Counting.reads = 0
    await ingest.ingest_driver_hos(7)
    assert _Counting.reads == 0
