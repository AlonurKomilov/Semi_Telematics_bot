"""A second opinion about a person is not a vote that the person exists.

The vehicle half of this argument lives in
``features/vehicles/tests/test_spec_fill_never_creates_a_vehicle`` and
the danger there is money: a new source that creates rows in the
registry moves a customer's invoice. Here the danger is a person.

The providers that reach this feed describe a driver without ever
saying whether they still work for the carrier — ORIENT's roster has no
role, no status, no active flag and no termination date. A source that
cannot tell a current driver from somebody who left in March does not
get to decide who is on the roster.

Which is why the promise is structural rather than an argument: the
storage method this feed calls has no INSERT in it. The tests below
guard the two halves the module itself owns — that it asks EVERY
provider, and that it hands each one's rows in under that provider's
own name, because ``source`` is what the precedence panel ranks and
what ``driver_field_provenance`` records.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import features.drivers.spec_ingest as spec_ingest


class _Provider:
    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom
        self.asked = 0

    async def get_driver_spec(self):
        self.asked += 1
        if self._boom:
            raise RuntimeError("upstream 503")
        return list(self._rows)


class _Tenant:
    """Records exactly what the roster was asked to do."""

    def __init__(self, counts=None):
        self.calls: list[dict] = []
        self._counts = counts or {}

    async def project_provider_driver_spec(self, account_id, provider_id, rows):
        self.calls.append({"provider_id": provider_id, "rows": rows})
        base = {
            "linked": len(rows), "written": len(rows), "filled_cdl": 0,
            "skipped_unlinked": 0, "conflicts": 0,
        }
        base.update(self._counts)
        return base


@pytest.fixture
def wired(monkeypatch):
    def _install(providers: dict, counts=None):
        tenant = _Tenant(counts)

        async def _resolve(account_id, capability):
            return list(providers)

        async def _tenant(account_id):
            return tenant

        async def _client(account_id, provider_id, **kw):
            return providers[provider_id]

        monkeypatch.setattr(spec_ingest, "resolve_all_providers_for", _resolve)
        monkeypatch.setattr(spec_ingest, "get_tenant_db", _tenant)
        monkeypatch.setattr(spec_ingest, "get_telematics_client", _client)
        return tenant
    return _install


def _row(pdid="17", **kw):
    base = {
        "provider_driver_id": pdid, "display_name": "John Smith",
        "phone": "+15551111", "cdl_number": "D123456",
        "cdl_state": "CA", "company_code": "PTG",
    }
    base.update(kw)
    return base


# ── The promise ───────────────────────────────────────────────────

def test_the_module_never_reaches_for_a_creating_path():
    """Grep-proof, in both directions.

    ``project_provider_driver_spec`` is the one storage call allowed
    here. Its Datatruck sibling matches on a licence and an email and
    its neighbouring import CREATES users — calling either from a
    scheduled feed is the mistake this test exists to catch, and it is
    an easy one to make because the names are two words apart.
    """
    src = inspect.getsource(spec_ingest)
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "project_provider_driver_spec" in called
    for forbidden in (
        "project_datatruck_drivers",
        "apply_datatruck_driver_import",
        "create_user",
    ):
        assert forbidden not in called, (
            f"a scheduled driver feed called {forbidden}"
        )


# ── The merge itself ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_provider_offering_an_identity_is_asked(wired):
    """A union of opinions. Asking only the first in catalog order
    would throw away the second one, which is the entire reason this
    feed exists."""
    providers = {
        "samsara": _Provider([_row("100")]),
        "orient_eld": _Provider([_row("200")]),
    }
    tenant = wired(providers)

    merged = await spec_ingest.ingest_driver_spec(7)

    assert providers["samsara"].asked == 1
    assert providers["orient_eld"].asked == 1
    assert merged == 2
    assert {c["provider_id"] for c in tenant.calls} == {"samsara", "orient_eld"}


@pytest.mark.asyncio
async def test_each_providers_rows_go_in_under_its_own_name(wired):
    """``provider_id`` is what the precedence panel ranks, what the link
    table is keyed by, and what ``driver_field_provenance`` records —
    mislabel it and the owner's ordering applies to the wrong provider
    while the link matches nobody."""
    tenant = wired({"orient_eld": _Provider([_row()])})
    await spec_ingest.ingest_driver_spec(7)
    assert tenant.calls[0]["provider_id"] == "orient_eld"


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_stop_the_other(wired):
    providers = {
        "samsara": _Provider([], boom=True),
        "orient_eld": _Provider([_row()]),
    }
    tenant = wired(providers)
    assert await spec_ingest.ingest_driver_spec(7) == 1
    assert [c["provider_id"] for c in tenant.calls] == ["orient_eld"]


@pytest.mark.asyncio
async def test_an_empty_answer_writes_nothing(wired):
    """Agreement between the roster and the provider is the healthy
    state, not a stalled feed."""
    tenant = wired({"orient_eld": _Provider([])})
    assert await spec_ingest.ingest_driver_spec(7) == 0
    assert tenant.calls == []


@pytest.mark.asyncio
async def test_nothing_offering_an_identity_is_zero_not_a_raise(wired):
    tenant = wired({})
    assert await spec_ingest.ingest_driver_spec(7) == 0
    assert tenant.calls == []


@pytest.mark.asyncio
async def test_an_account_with_no_links_yet_is_zero_not_a_failure(wired):
    """The first state every account is in. Nothing is linked, so every
    row is skipped — and that must read as "nobody has linked anyone",
    never as a broken feed."""
    tenant = wired(
        {"orient_eld": _Provider([_row("17"), _row("18")])},
        counts={"linked": 0, "written": 0, "skipped_unlinked": 2},
    )
    assert await spec_ingest.ingest_driver_spec(7) == 0
    assert tenant.calls, "the provider was never asked"
