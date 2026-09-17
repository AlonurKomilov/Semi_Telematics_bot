"""A dataset can ask WHICH provider instead of assuming Samsara.

The fan-out gate reads an account's integration row before it runs a
job, and it defaulted to the Samsara row for every dataset. So a
feature built to be provider-agnostic — ELD — was gated on a vendor it
does not name: an account that disconnected Samsara and connected a
different ELD would be skipped HERE, before its own resolver ever ran,
and the feed would go dark with nothing to distinguish that from "no
provider connected".

``provider_id=None`` on the dataset means "resolve by capability". The
eight datasets that predate the resolver keep the default and are
untouched.
"""

from __future__ import annotations

import pytest

from capabilities.data_lifecycle.ingest import (
    IngestDataset, all_datasets, discover,
)


def _dataset(**kw):
    base = dict(
        key="test.thing", owner="test", job_id="test_thing",
        capability="test_capability", cadence={"interval_min": 5},
        run=lambda account_id: 0, tables=("t",), freshness_sla_min=30,
    )
    base.update(kw)
    return IngestDataset(**base)


#: Datasets that RESOLVE their provider instead of assuming one.
#:
#: Named rather than counted, because adding one is a decision: a
#: resolving dataset runs for an account with no Samsara row, which is
#: the whole point for a feed whose provider may be anybody — and the
#: wrong answer for the eight that are Samsara-fed and would then be
#: asked of a provider that does not serve them.
_RESOLVING = {
    "eld.driver_hos",      # whichever ELD the account connected
    "vehicles.spec_fill",  # a second opinion, from whoever offers one
    "drivers.spec_fill",   # the same, about people an admin linked
    "vehicles.state",      # one write path: every provider reporting live
                           # state is asked and arbitrated per reading
}


def test_only_the_resolving_datasets_opt_out_of_the_samsara_default():
    assert _dataset().provider_id == "samsara"
    discover()
    for d in all_datasets():
        if d.key in _RESOLVING:
            assert d.provider_id is None, (
                f"{d.key} is listed as resolving but still names a provider"
            )
        else:
            assert d.provider_id == "samsara", d.key


def test_eld_asks_for_resolution():
    discover()
    eld = next(d for d in all_datasets() if d.key == "eld.driver_hos")
    assert eld.provider_id is None, (
        "gated on the Samsara row, a swapped ELD is skipped before its "
        "own resolver runs"
    )


@pytest.mark.asyncio
async def test_a_resolving_dataset_runs_for_an_account_with_no_samsara_row(
    monkeypatch,
):
    """The scenario the default made impossible."""
    import capabilities.data_lifecycle.ingest.engine as engine

    ran: list[int] = []

    async def _run(account_id):
        ran.append(account_id)
        return 1

    seen = {}

    async def _fan_out(capability, coro_factory, *, provider_id="samsara"):
        seen["provider_id"] = provider_id
        await coro_factory(7)

    async def _resolve(account_id, capability):
        return object()          # something IS connected, just not Samsara

    async def _tenant(account_id):
        class _T:
            async def record_ingest_run(self, *a, **kw):
                return None
        return _T()

    monkeypatch.setattr(
        "capabilities.integrations.shared.helpers."
        "_for_each_account_with_capability", _fan_out)
    monkeypatch.setattr(
        "capabilities.integrations.shared.resolver.resolve_provider_for",
        _resolve)
    monkeypatch.setattr("infra.platform.get_tenant_db", _tenant)

    await engine.run_dataset(_dataset(provider_id=None, run=_run))

    assert seen["provider_id"] is None, "the gate was still asked for Samsara"
    assert ran == [7]


@pytest.mark.asyncio
async def test_nothing_connected_is_not_recorded_as_a_run(monkeypatch):
    """``ingest_runs`` must keep meaning "we had a provider and
    fetched". Recording a skip would make the watchdog read "we
    fetched and got nothing", which on a compliance feed is the one
    statement it must never make."""
    import capabilities.data_lifecycle.ingest.engine as engine

    ran: list[int] = []
    recorded: list[int] = []

    async def _run(account_id):
        ran.append(account_id)
        return 1

    async def _fan_out(capability, coro_factory, *, provider_id="samsara"):
        await coro_factory(7)

    async def _resolve(account_id, capability):
        return None              # no ELD on this account

    async def _tenant(account_id):
        class _T:
            async def record_ingest_run(self, *a, **kw):
                recorded.append(account_id)
        return _T()

    monkeypatch.setattr(
        "capabilities.integrations.shared.helpers."
        "_for_each_account_with_capability", _fan_out)
    monkeypatch.setattr(
        "capabilities.integrations.shared.resolver.resolve_provider_for",
        _resolve)
    monkeypatch.setattr("infra.platform.get_tenant_db", _tenant)

    await engine.run_dataset(_dataset(provider_id=None, run=_run))

    assert ran == [], "the job ran for an account with no provider"
    assert recorded == [], "a skip was recorded as a run"
